import asyncio
import hashlib
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_data import serialize_account
from .auth import get_db, require_session_account
from .config import Settings, get_settings
from .models import Account, Invitation, PlanAssignment, utcnow
from .security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/v1/web-auth", tags=["web-auth"])


class WebLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=128)


async def _limit_login(request: Request, email: str) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=503, detail="authentication_service_unavailable"
        )
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24]
    key = f"markitdown:auth-rate:web-login:{digest}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except RedisError:
        raise HTTPException(
            status_code=503, detail="authentication_service_unavailable"
        ) from None
    if count > 10:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


def _cookie_secure(settings: Settings) -> bool:
    return urlsplit(settings.public_base_url).scheme == "https"


@router.post("/login")
async def web_login(
    payload: WebLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = payload.email.strip().lower()
    await _limit_login(request, email)
    account = db.scalar(select(Account).where(Account.email == email))
    if account is None or not account.is_active:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if not await asyncio.to_thread(
        verify_password, payload.password, account.password_hash
    ):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if settings.email_verification_required and account.verified_at is None:
        raise HTTPException(status_code=403, detail="email_verification_required")

    session_token = create_access_token(
        account.id, settings.jwt_secret, version=account.session_version
    )
    csrf_token = secrets.token_urlsafe(32)
    cookie_settings = {
        "secure": _cookie_secure(settings),
        "samesite": "strict",
        "path": "/",
        "max_age": 3600,
    }
    response.set_cookie("md_session", session_token, httponly=True, **cookie_settings)
    response.set_cookie("md_csrf", csrf_token, httponly=False, **cookie_settings)
    return {"user": serialize_account(db, account, settings), "csrf_token": csrf_token}


@router.get("/me")
def web_me(
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return {"user": serialize_account(db, account, settings)}


@router.get("/csrf")
def csrf_token(
    request: Request,
    _account: Account = Depends(require_session_account),
):
    token = request.cookies.get("md_csrf")
    if not token:
        raise HTTPException(status_code=403, detail="csrf_cookie_missing")
    return {"csrfToken": token}


@router.post("/logout", status_code=204)
def web_logout(
    response: Response,
    account: Account = Depends(require_session_account),
    settings: Settings = Depends(get_settings),
):
    cookie_settings = {
        "secure": _cookie_secure(settings),
        "samesite": "strict",
        "path": "/",
    }
    response.delete_cookie("md_session", httponly=True, **cookie_settings)
    response.delete_cookie("md_csrf", httponly=False, **cookie_settings)


@router.post("/accept-invitation", status_code=201)
def accept_invitation(
    payload: AcceptInvitationRequest,
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    invitation = db.scalar(
        select(Invitation)
        .where(
            Invitation.token_hash == token_hash,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status_code=400, detail="invalid_or_expired_invitation")
    if db.scalar(select(Account.id).where(Account.email == invitation.email)):
        invitation.accepted_at = utcnow()
        db.commit()
        raise HTTPException(status_code=409, detail="account_already_exists")
    now = utcnow()
    account = Account(
        id=uuid4().hex,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        session_version=0,
        role=invitation.role,
        is_active=True,
        display_name=invitation.display_name,
        verified_at=now,
        created_at=now,
    )
    db.add(account)
    if invitation.plan_id:
        db.add(
            PlanAssignment(
                id=uuid4().hex,
                account_id=account.id,
                plan_id=invitation.plan_id,
                assigned_by_account_id=invitation.invited_by_account_id,
                starts_at=now,
            )
        )
    invitation.accepted_at = now
    db.commit()
    return {"status": "accepted", "email": account.email}
