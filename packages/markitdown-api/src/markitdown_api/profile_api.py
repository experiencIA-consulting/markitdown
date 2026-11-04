import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .account_data import serialize_account
from .auth import get_db, require_session_account
from .config import Settings, get_settings
from .models import Account, ApiKey, utcnow
from .security import hash_password, verify_password

router = APIRouter(prefix="/v1/account", tags=["account"])


class ProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.get("/me")
def get_profile(
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return serialize_account(db, account, settings)


@router.patch("/me")
def update_profile(
    payload: ProfilePatch,
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    account.display_name = (
        payload.display_name.strip()[:120] if payload.display_name else None
    )
    db.commit()
    db.refresh(account)
    return serialize_account(db, account, settings)


@router.post("/password")
async def change_password(
    payload: PasswordChange,
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
):
    if not await asyncio.to_thread(
        verify_password, payload.current_password, account.password_hash
    ):
        raise HTTPException(status_code=400, detail="invalid_current_password")
    if await asyncio.to_thread(
        verify_password, payload.new_password, account.password_hash
    ):
        raise HTTPException(status_code=422, detail="password_must_change")
    account.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
    account.session_version += 1
    now = utcnow()
    db.query(ApiKey).filter(
        ApiKey.account_id == account.id, ApiKey.revoked_at.is_(None)
    ).update({ApiKey.revoked_at: now}, synchronize_session=False)
    db.commit()
    return {
        "status": "password_changed",
        "message": "Sign in again and create a new API key.",
    }
