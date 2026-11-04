import hmac
import re
from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import SessionLocal
from .models import Account, ApiKey
from .security import decode_access_token, hash_api_key


@dataclass(frozen=True)
class Principal:
    account_id: str
    api_key_id: str
    role: str = "user"


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_session_account(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Account:
    bearer = _bearer_value(authorization)
    cookie_token = request.cookies.get("md_session")
    token = bearer or cookie_token
    claims = decode_access_token(token, settings.jwt_secret) if token else None
    if not claims:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    account_id, version = claims
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    if not account.is_active:
        raise HTTPException(status_code=403, detail="account_suspended")
    if account.session_version != version:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    if settings.email_verification_required and account.verified_at is None:
        raise HTTPException(status_code=403, detail="email_verification_required")
    if cookie_token and not bearer and request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf_cookie = request.cookies.get("md_csrf")
        csrf_header = request.headers.get("x-csrf-token")
        origin = request.headers.get("origin")
        expected_origin = settings.public_base_url.rstrip("/")
        if (
            not csrf_cookie
            or not csrf_header
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            raise HTTPException(status_code=403, detail="csrf_validation_failed")
        if origin and origin.rstrip("/") != expected_origin:
            raise HTTPException(status_code=403, detail="origin_not_allowed")
    return account


def require_admin_account(
    account: Account = Depends(require_session_account),
) -> Account:
    if account.role != "admin":
        raise HTTPException(status_code=403, detail="admin_access_required")
    return account


def require_api_principal(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not x_api_key or len(x_api_key) > 128:
        raise HTTPException(status_code=401, detail="invalid_api_key")
    key_hash = hash_api_key(x_api_key, settings.api_key_pepper)
    api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if api_key is None or api_key.revoked_at is not None:
        raise HTTPException(status_code=401, detail="invalid_api_key")
    account = db.get(Account, api_key.account_id)
    if (
        account is None
        or not account.is_active
        or (settings.email_verification_required and account.verified_at is None)
    ):
        raise HTTPException(status_code=401, detail="invalid_api_key")
    from .models import utcnow

    api_key.last_used_at = utcnow()
    db.commit()
    return Principal(account_id=account.id, api_key_id=api_key.id, role=account.role)


def _bearer_value(value: str | None) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"Bearer\s+([^\s]+)", value, flags=re.IGNORECASE)
    return match.group(1) if match else None
