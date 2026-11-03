import re
from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
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


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_session_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Account:
    token = _bearer_value(authorization)
    claims = decode_access_token(token, settings.jwt_secret) if token else None
    if not claims:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    account_id, version = claims
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    if account.session_version != version:
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    if settings.email_verification_required and account.verified_at is None:
        raise HTTPException(status_code=403, detail="email_verification_required")
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
    if account is None or (
        settings.email_verification_required and account.verified_at is None
    ):
        raise HTTPException(status_code=401, detail="invalid_api_key")
    from .models import utcnow

    api_key.last_used_at = utcnow()
    db.commit()
    return Principal(account_id=account.id, api_key_id=api_key.id)


def _bearer_value(value: str | None) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"Bearer\s+([^\s]+)", value, flags=re.IGNORECASE)
    return match.group(1) if match else None
