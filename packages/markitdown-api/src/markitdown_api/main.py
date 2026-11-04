import asyncio
import hashlib
import logging
import re
import smtplib
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from . import __version__
from .admin_api import router as admin_router
from .auth import Principal, get_db, require_api_principal, require_session_account
from .config import Settings, get_settings
from .db import SessionLocal, initialize_database
from .entitlements import get_quota_snapshot, quota_exceeded
from .job_queue import HEARTBEAT_KEY, enqueue
from .mail import send_account_email
from .models import Account, AccountToken, ApiKey, Conversion, utcnow
from .profile_api import router as profile_router
from .reports_api import public_router as public_plans_router
from .reports_api import router as reports_router
from .security import (
    create_access_token,
    generate_account_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)
from .storage import (
    ensure_storage,
    input_path,
    remove_input,
    remove_result,
    result_path,
    safe_extension,
    save_upload,
)
from .url_fetcher import UnsafeURL, validate_url_syntax
from .web_auth import router as web_auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_secrets()
    ensure_storage(settings)
    initialize_database()
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=3,
    )
    await redis.ping()
    app.state.redis = redis
    app.state.upload_semaphore = asyncio.Semaphore(1)
    try:
        yield
    finally:
        await redis.aclose()


app = FastAPI(title="MarkItDown Conversion API", version=__version__, lifespan=lifespan)
logger = logging.getLogger("markitdown_api")
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-used-for-timing-equalization")

app.include_router(web_auth_router)
app.include_router(profile_router)
app.include_router(admin_router)
app.include_router(reports_router)
app.include_router(public_plans_router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", request_id):
        request_id = uuid4().hex
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(
            "http_request_failed request_id=%s method=%s error_type=%s",
            request_id,
            request.method,
            type(exc).__name__,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path in {"/verify", "/reset-password"}:
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
    logger.info(
        "http_request_complete request_id=%s method=%s status=%s duration_ms=%d",
        request_id,
        request.method,
        response.status_code,
        int((time.monotonic() - started) * 1000),
    )
    return response


class AccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class PasswordResetConfirm(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=100)


class URLConversionRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    ocr: bool = False


class ConversionAcceptedResponse(BaseModel):
    id: str = Field(description="Conversion identifier used by the status/result URLs.")
    status: Literal["queued"] = "queued"
    status_url: str = Field(description="Relative URL to poll for conversion status.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
                    "status": "queued",
                    "status_url": "/v1/conversions/7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
                }
            ]
        }
    }


class ConversionStatusResponse(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed", "expired", "cancelled"]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_expires_at: datetime | None
    result_url: str | None = Field(
        description="Relative result URL when status is succeeded and the result is available."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
                    "status": "succeeded",
                    "error_code": None,
                    "created_at": "2026-11-04T12:00:00Z",
                    "started_at": "2026-11-04T12:00:01Z",
                    "completed_at": "2026-11-04T12:00:03Z",
                    "result_expires_at": "2026-11-05T12:00:03Z",
                    "result_url": "/v1/conversions/7f3a2c9e54b14d5e8a2b1c0d9e6f1234/result",
                }
            ]
        }
    }


def _redis(request: Request) -> Redis:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="queue_unavailable")
    return redis


async def _enforce_rate_limit(redis: Redis, account_id: str, limit: int) -> None:
    bucket = int(datetime.now(timezone.utc).timestamp()) // 60
    key = f"markitdown:rate:{account_id}:{bucket}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > limit:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


async def _enforce_identity_limit(
    request: Request, identity: str, action: str, limit: int, window_seconds: int
) -> None:
    digest = hashlib.sha256(identity.strip().lower().encode()).hexdigest()[:24]
    key = f"markitdown:auth-rate:{action}:{digest}"
    try:
        redis = _redis(request)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
    except RedisError:
        raise HTTPException(
            status_code=503, detail="rate_limit_service_unavailable"
        ) from None
    if count > limit:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


def _add_account_token(
    db: Session, account_id: str, purpose: str, ttl: timedelta
) -> str:
    token, token_hash = generate_account_token()
    db.add(
        AccountToken(
            id=uuid4().hex,
            account_id=account_id,
            token_hash=token_hash,
            purpose=purpose,
            expires_at=utcnow() + ttl,
        )
    )
    return token


async def _send_token_email(
    settings: Settings, email: str, purpose: str, token: str
) -> None:
    if not settings.smtp_configured:
        raise HTTPException(status_code=503, detail="email_delivery_not_configured")
    if purpose == "verify":
        subject, link = (
            "Verifica tu cuenta de MarkItDown API",
            f"{settings.public_base_url}/verify#token={token}",
        )
    else:
        subject, link = (
            "Restablece tu contraseña de MarkItDown API",
            f"{settings.public_base_url}/reset-password#token={token}",
        )
    try:
        await asyncio.to_thread(send_account_email, settings, email, subject, link)
    except (OSError, TimeoutError, smtplib.SMTPException):
        raise HTTPException(status_code=503, detail="email_delivery_failed") from None


def _consume_account_token(db: Session, raw_token: str, purpose: str):
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    record = db.scalar(
        select(AccountToken)
        .where(
            AccountToken.token_hash == token_hash,
            AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
            AccountToken.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if record is None:
        return None
    account = db.get(Account, record.account_id)
    return (record, account) if account else None


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request, db: Session = Depends(get_db)):
    try:
        db.execute(select(1))
        redis = _redis(request)
        await redis.ping()
        worker_alive = bool(await redis.exists(HEARTBEAT_KEY))
    except (HTTPException, RedisError, SQLAlchemyError):
        raise HTTPException(
            status_code=503, detail="dependencies_unavailable"
        ) from None
    if not worker_alive:
        raise HTTPException(status_code=503, detail="worker_unavailable")
    return {"status": "ready"}


@app.post("/v1/accounts", status_code=201)
async def create_account(
    payload: AccountCreate,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.public_signup_enabled:
        raise HTTPException(status_code=403, detail="public_signup_disabled")
    email = payload.email.strip().lower()
    await _enforce_identity_limit(request, email, "signup", 3, 3600)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="invalid_email")
    if settings.email_verification_required and not settings.smtp_configured:
        raise HTTPException(status_code=503, detail="email_verification_not_configured")
    if db.scalar(select(Account.id).where(Account.email == email)):
        raise HTTPException(status_code=409, detail="account_already_exists")
    account = Account(
        id=uuid4().hex,
        email=email,
        password_hash=await asyncio.to_thread(hash_password, payload.password),
        session_version=0,
        role="user",
        is_active=True,
        verified_at=None if settings.email_verification_required else utcnow(),
    )
    db.add(account)
    verify_token = (
        _add_account_token(db, account.id, "verify", timedelta(hours=24))
        if settings.email_verification_required
        else None
    )
    db.commit()
    if verify_token:
        await _send_token_email(settings, email, "verify", verify_token)
        return {"id": account.id, "email": email, "verification_required": True}
    api_key, prefix, key_id = generate_api_key()
    db.add(
        ApiKey(
            id=key_id,
            account_id=account.id,
            name="default",
            prefix=prefix,
            key_hash=hash_api_key(api_key, settings.api_key_pepper),
        )
    )
    db.commit()
    return {
        "id": account.id,
        "email": email,
        "access_token": create_access_token(
            account.id, settings.jwt_secret, version=account.session_version
        ),
        "api_key": api_key,
        "warning": "Store the API key now; it cannot be retrieved later.",
    }


@app.post("/v1/accounts/verify")
def verify_account(payload: TokenRequest, db: Session = Depends(get_db)):
    found = _consume_account_token(db, payload.token, "verify")
    if not found:
        raise HTTPException(status_code=400, detail="invalid_or_expired_token")
    record, account = found
    now = utcnow()
    record.used_at = now
    account.verified_at = now
    db.query(AccountToken).filter(
        AccountToken.account_id == account.id,
        AccountToken.purpose == "verify",
        AccountToken.used_at.is_(None),
    ).update({AccountToken.used_at: now}, synchronize_session=False)
    db.commit()
    return {"status": "verified", "email": account.email}


@app.get("/verify", response_class=HTMLResponse)
def verification_page():
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Verificar cuenta</title>"
        "<p>Confirma la verificación de tu cuenta.</p><button id=confirm>Verificar</button>"
        "<pre id=result></pre><script>const token=new URLSearchParams(location.hash.slice(1)).get('token');history.replaceState(null,'',location.pathname);"
        "document.getElementById('confirm').onclick=async()=>{const r=await fetch('/v1/accounts/verify',"
        "{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});"
        "document.getElementById('result').textContent=r.ok?'Cuenta verificada. Ya puedes iniciar sesión.':"
        "'El enlace no es válido o ha caducado.';};</script>"
    )


@app.post("/v1/auth/token")
async def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.public_signup_enabled:
        raise HTTPException(status_code=403, detail="public_signup_disabled")
    email = payload.email.strip().lower()
    await _enforce_identity_limit(request, email, "login", 10, 60)
    account = db.scalar(select(Account).where(Account.email == email))
    password_hash = (
        account.password_hash if account is not None else _DUMMY_PASSWORD_HASH
    )
    password_valid = await asyncio.to_thread(
        verify_password, payload.password, password_hash
    )
    if account is None or not account.is_active or not password_valid:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if settings.email_verification_required and account.verified_at is None:
        raise HTTPException(status_code=403, detail="email_verification_required")
    return {
        "access_token": create_access_token(
            account.id, settings.jwt_secret, version=account.session_version
        ),
        "token_type": "bearer",
        "expires_in": 3600,
    }


@app.post("/v1/accounts/verification/resend", status_code=202)
async def resend_verification(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = payload.email.strip().lower()
    await _enforce_identity_limit(request, email, "verify", 3, 3600)
    account = db.scalar(select(Account).where(Account.email == email))
    if account is not None and account.verified_at is None and settings.smtp_configured:
        token = _add_account_token(db, account.id, "verify", timedelta(hours=24))
        db.commit()
        await _send_token_email(settings, account.email, "verify", token)
    return {
        "status": "accepted",
        "message": "If verification is pending, instructions will be sent.",
    }


@app.post("/v1/accounts/password-reset", status_code=202)
async def request_password_reset(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = payload.email.strip().lower()
    await _enforce_identity_limit(request, email, "password_reset", 3, 3600)
    account = db.scalar(select(Account).where(Account.email == email))
    if account is not None and settings.smtp_configured:
        token = _add_account_token(
            db, account.id, "password_reset", timedelta(minutes=30)
        )
        db.commit()
        await _send_token_email(settings, account.email, "password_reset", token)
    return {
        "status": "accepted",
        "message": "If the account exists, instructions will be sent.",
    }


@app.post("/v1/accounts/password-reset/confirm")
def reset_password(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    found = _consume_account_token(db, payload.token, "password_reset")
    if not found:
        raise HTTPException(status_code=400, detail="invalid_or_expired_token")
    record, account = found
    now = utcnow()
    record.used_at = now
    account.password_hash = hash_password(payload.new_password)
    account.session_version += 1
    db.query(AccountToken).filter(
        AccountToken.account_id == account.id,
        AccountToken.purpose == "password_reset",
        AccountToken.used_at.is_(None),
    ).update({AccountToken.used_at: now}, synchronize_session=False)
    db.query(ApiKey).filter(
        ApiKey.account_id == account.id, ApiKey.revoked_at.is_(None)
    ).update({ApiKey.revoked_at: now}, synchronize_session=False)
    db.commit()
    return {"status": "password_reset"}


@app.get("/reset-password", response_class=HTMLResponse)
def password_reset_page():
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Restablecer contraseña</title>"
        "<label>Nueva contraseña <input id=password type=password minlength=12 maxlength=128></label>"
        "<button id=confirm>Guardar</button><pre id=result></pre><script>const token=new URLSearchParams(location.hash.slice(1)).get('token');history.replaceState(null,'',location.pathname);"
        "document.getElementById('confirm').onclick=async()=>{const password=document.getElementById('password').value;"
        "const r=await fetch('/v1/accounts/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({token,new_password:password})});document.getElementById('result').textContent=r.ok?"
        "'Contraseña actualizada.':'Enlace no válido o caducado; revisa que tenga al menos 12 caracteres.';};</script>"
    )


@app.post("/v1/api-keys", status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    active_keys = db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.account_id == account.id, ApiKey.revoked_at.is_(None))
    )
    if active_keys >= 20:
        raise HTTPException(status_code=409, detail="active_api_key_limit_reached")
    key, prefix, key_id = generate_api_key()
    db.add(
        ApiKey(
            id=key_id,
            account_id=account.id,
            name=payload.name,
            prefix=prefix,
            key_hash=hash_api_key(key, settings.api_key_pepper),
        )
    )
    db.commit()
    return {
        "id": key_id,
        "name": payload.name,
        "api_key": key,
        "warning": "Store this key now; it cannot be retrieved later.",
    }


@app.get("/v1/api-keys")
def list_api_keys(
    account: Account = Depends(require_session_account), db: Session = Depends(get_db)
):
    keys = db.scalars(
        select(ApiKey)
        .where(ApiKey.account_id == account.id)
        .order_by(ApiKey.created_at.desc())
    ).all()
    return [
        {
            "id": key.id,
            "name": key.name,
            "prefix": key.prefix,
            "created_at": key.created_at,
            "last_used_at": key.last_used_at,
            "revoked_at": key.revoked_at,
        }
        for key in keys
    ]


@app.delete("/v1/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str,
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
):
    key = db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.account_id == account.id)
    )
    if key is None:
        raise HTTPException(status_code=404, detail="api_key_not_found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        db.commit()


@app.get("/v1/usage")
def usage(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
):
    conversions = db.scalars(
        select(Conversion)
        .where(Conversion.account_id == account.id)
        .order_by(Conversion.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "created_at": item.created_at,
                "status": item.status,
                "source_type": item.source_type,
                "input_extension": item.input_extension,
                "input_bytes": item.input_bytes,
                "input_mb": (
                    item.input_bytes / 1_000_000
                    if item.input_bytes is not None
                    else None
                ),
                "page_count": item.page_count,
                "output_char_count": item.output_char_count,
                "output_utf8_bytes": item.output_utf8_bytes,
                "ocr_requested": item.ocr_requested,
                "external_provider": item.external_provider,
                "duration_ms": item.duration_ms,
                "error_code": item.error_code,
            }
            for item in conversions
        ],
        "limit": limit,
        "offset": offset,
    }


def _check_ocr(ocr: bool, snapshot, settings: Settings) -> None:
    if not ocr:
        return
    if not snapshot.limits["ocr_enabled"]:
        raise HTTPException(status_code=403, detail="ocr_not_in_plan")
    if not settings.ocr_configured:
        raise HTTPException(status_code=503, detail="ocr_provider_not_configured")
    limit = snapshot.limits["max_ocr_units_month"]
    if limit is not None and snapshot.usage["ocr_units"] >= limit:
        raise HTTPException(status_code=429, detail="monthly_ocr_quota_exceeded")


async def _enqueue_job(
    request: Request, db: Session, conversion: Conversion, job: dict, settings: Settings
):
    try:
        accepted = await enqueue(_redis(request), job, settings.max_pending_jobs)
    except (HTTPException, RedisError):
        accepted = False
        conversion.error_code = "queue_unavailable"
    if not accepted:
        conversion.status = "failed"
        conversion.error_code = conversion.error_code or "queue_full"
        conversion.completed_at = utcnow()
        conversion.duration_ms = 0
        db.commit()
        remove_input(settings, conversion.id)
        status = 503 if conversion.error_code == "queue_unavailable" else 429
        raise HTTPException(status_code=status, detail=conversion.error_code)


async def _receive_upload(
    request: Request, settings: Settings, max_file_bytes: int, snapshot
):
    content_length = request.headers.get("content-length")
    if content_length is None:
        raise HTTPException(status_code=411, detail="content_length_required")
    try:
        request_bytes = int(content_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_content_length") from None
    request_limit = min(settings.max_upload_bytes, max_file_bytes)
    if request_bytes > request_limit + 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")
    if "multipart/form-data" not in request.headers.get("content-type", "").lower():
        raise HTTPException(status_code=415, detail="multipart_form_required")

    semaphore = getattr(request.app.state, "upload_semaphore", asyncio.Semaphore(1))
    async with semaphore:
        try:
            form = await request.form(max_files=1, max_fields=1)
        except (StarletteHTTPException, MultiPartException, ValueError):
            raise HTTPException(
                status_code=400, detail="invalid_multipart_form"
            ) from None
        try:
            upload = form.get("file")
            if (
                upload is None
                or not hasattr(upload, "file")
                or not hasattr(upload, "filename")
            ):
                raise HTTPException(status_code=422, detail="file_required")
            if set(form.keys()) - {"file", "ocr"}:
                raise HTTPException(status_code=422, detail="unexpected_form_field")
            ocr_value = str(form.get("ocr", "false")).lower()
            if ocr_value not in {"true", "false", "1", "0"}:
                raise HTTPException(status_code=422, detail="invalid_ocr_flag")
            ocr = ocr_value in {"true", "1"}
            _check_ocr(ocr, snapshot, settings)
            conversion_id = uuid4().hex
            extension = safe_extension(upload.filename or "")
            try:
                input_bytes = await asyncio.to_thread(
                    save_upload,
                    upload.file,
                    input_path(settings, conversion_id),
                    max_file_bytes,
                )
            except ValueError as exc:
                if str(exc) == "file_too_large":
                    raise HTTPException(
                        status_code=413, detail="file_too_large"
                    ) from None
                raise
            return conversion_id, extension, input_bytes, ocr
        finally:
            await form.close()


@app.post(
    "/v1/conversions",
    status_code=202,
    response_model=ConversionAcceptedResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "ocr": {"type": "boolean", "default": False},
                        },
                    }
                }
            },
        }
    },
)
async def create_upload_conversion(
    request: Request,
    principal: Principal = Depends(require_api_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snapshot = get_quota_snapshot(db, principal.account_id, settings)
    if principal.role != "admin" and not snapshot.plan_configured:
        raise HTTPException(status_code=403, detail="plan_not_configured")
    await _enforce_rate_limit(
        _redis(request),
        principal.account_id,
        int(snapshot.limits["requests_per_minute"] or settings.rate_limit_per_minute),
    )
    if quota_exceeded(snapshot, "max_conversions_month", additional=1):
        raise HTTPException(status_code=429, detail="monthly_conversion_quota_exceeded")
    if quota_exceeded(snapshot, "max_pages_month", additional=1):
        raise HTTPException(status_code=429, detail="monthly_page_quota_exceeded")
    monthly_byte_limit = snapshot.limits["max_input_bytes_month"]
    if (
        monthly_byte_limit is not None
        and snapshot.usage["input_bytes"] >= monthly_byte_limit
    ):
        raise HTTPException(
            status_code=429, detail="monthly_input_bytes_quota_exceeded"
        )
    max_file_bytes = int(snapshot.limits["max_file_bytes"] or settings.max_upload_bytes)
    conversion_id, extension, input_bytes, ocr = await _receive_upload(
        request, settings, max_file_bytes, snapshot
    )

    conversion = Conversion(
        id=conversion_id,
        account_id=principal.account_id,
        status="queued",
        source_type="upload",
        input_extension=extension,
        input_bytes=input_bytes,
        ocr_requested=ocr,
        external_provider="azure_openai" if ocr else None,
    )
    db.add(conversion)
    db.flush()
    current_snapshot = get_quota_snapshot(db, principal.account_id, settings)
    current_byte_limit = current_snapshot.limits["max_input_bytes_month"]
    if (
        current_byte_limit is not None
        and current_snapshot.usage["input_bytes"] > current_byte_limit
    ):
        conversion.status = "failed"
        conversion.error_code = "monthly_input_bytes_quota_exceeded"
        conversion.completed_at = utcnow()
        conversion.duration_ms = 0
        db.commit()
        remove_input(settings, conversion_id)
        raise HTTPException(
            status_code=429, detail="monthly_input_bytes_quota_exceeded"
        )
    db.commit()
    await _enqueue_job(
        request,
        db,
        conversion,
        {"conversion_id": conversion_id, "source_type": "upload", "ocr": ocr},
        settings,
    )
    logger.info(
        "conversion_queued id=%s account_id=%s source=upload ocr=%s",
        conversion_id,
        principal.account_id,
        ocr,
    )
    return {
        "id": conversion_id,
        "status": "queued",
        "status_url": f"/v1/conversions/{conversion_id}",
    }


@app.post(
    "/v1/conversions/from-url",
    status_code=202,
    response_model=ConversionAcceptedResponse,
)
async def create_url_conversion(
    payload: URLConversionRequest,
    request: Request,
    principal: Principal = Depends(require_api_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snapshot = get_quota_snapshot(db, principal.account_id, settings)
    if principal.role != "admin" and not snapshot.plan_configured:
        raise HTTPException(status_code=403, detail="plan_not_configured")
    redis = _redis(request)
    await _enforce_rate_limit(
        redis,
        principal.account_id,
        int(snapshot.limits["requests_per_minute"] or settings.rate_limit_per_minute),
    )
    _check_ocr(payload.ocr, snapshot, settings)
    try:
        validate_url_syntax(payload.url)
    except UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if quota_exceeded(snapshot, "max_conversions_month", additional=1):
        raise HTTPException(status_code=429, detail="monthly_conversion_quota_exceeded")
    if quota_exceeded(snapshot, "max_pages_month", additional=1):
        raise HTTPException(status_code=429, detail="monthly_page_quota_exceeded")
    monthly_byte_limit = snapshot.limits["max_input_bytes_month"]
    if (
        monthly_byte_limit is not None
        and snapshot.usage["input_bytes"] >= monthly_byte_limit
    ):
        raise HTTPException(
            status_code=429, detail="monthly_input_bytes_quota_exceeded"
        )
    remaining_bytes = (
        int(monthly_byte_limit) - snapshot.usage["input_bytes"]
        if monthly_byte_limit is not None
        else settings.max_upload_bytes
    )
    max_file_bytes = int(snapshot.limits["max_file_bytes"] or settings.max_upload_bytes)
    conversion_id = uuid4().hex
    conversion = Conversion(
        id=conversion_id,
        account_id=principal.account_id,
        status="queued",
        source_type="url",
        ocr_requested=payload.ocr,
        external_provider="azure_openai" if payload.ocr else None,
    )
    db.add(conversion)
    db.commit()
    await _enqueue_job(
        request,
        db,
        conversion,
        {
            "conversion_id": conversion_id,
            "source_type": "url",
            "url": payload.url,
            "ocr": payload.ocr,
            "max_input_bytes": min(
                max_file_bytes, remaining_bytes, settings.max_upload_bytes
            ),
            "input_limit_error": (
                "monthly_input_bytes_quota_exceeded"
                if monthly_byte_limit is not None and remaining_bytes < max_file_bytes
                else "file_too_large"
            ),
        },
        settings,
    )
    logger.info(
        "conversion_queued id=%s account_id=%s source=url ocr=%s",
        conversion_id,
        principal.account_id,
        payload.ocr,
    )
    return {
        "id": conversion_id,
        "status": "queued",
        "status_url": f"/v1/conversions/{conversion_id}",
    }


@app.get("/v1/conversions/{conversion_id}", response_model=ConversionStatusResponse)
def conversion_status(
    conversion_id: str,
    principal: Principal = Depends(require_api_principal),
    db: Session = Depends(get_db),
):
    conversion = db.scalar(
        select(Conversion).where(
            Conversion.id == conversion_id,
            Conversion.account_id == principal.account_id,
        )
    )
    if conversion is None:
        raise HTTPException(status_code=404, detail="conversion_not_found")
    return {
        "id": conversion.id,
        "status": conversion.status,
        "error_code": conversion.error_code,
        "created_at": conversion.created_at,
        "started_at": conversion.started_at,
        "completed_at": conversion.completed_at,
        "result_expires_at": conversion.result_expires_at,
        "result_url": (
            f"/v1/conversions/{conversion.id}/result"
            if conversion.status == "succeeded" and conversion.downloaded_at is None
            else None
        ),
    }


class OneTimeResultResponse(StreamingResponse):
    def __init__(self, path: Path, callback):
        self._callback = callback
        super().__init__(
            self._stream_file(path),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="converted.md"'},
        )

    @staticmethod
    async def _stream_file(path: Path):
        with path.open("rb") as source:
            while chunk := await asyncio.to_thread(source.read, 64 * 1024):
                yield chunk

    async def __call__(self, scope, receive, send):
        completed = False

        async def tracked_send(message):
            nonlocal completed
            await send(message)
            if message.get("type") == "http.response.body" and not message.get(
                "more_body", False
            ):
                completed = True

        try:
            await super().__call__(scope, receive, tracked_send)
        finally:
            await asyncio.to_thread(self._callback, completed)


@app.get(
    "/v1/conversions/{conversion_id}/result",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Raw UTF-8 Markdown body (not a JSON object). The result is one-time.",
            "content": {
                "text/markdown": {
                    "schema": {
                        "type": "string",
                        "example": "# Converted document\n\nMarkdown content...",
                    }
                }
            },
        }
    },
)
def download_result(
    conversion_id: str,
    principal: Principal = Depends(require_api_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    now = utcnow()
    claimed = db.execute(
        Conversion.__table__.update()
        .where(
            Conversion.id == conversion_id,
            Conversion.account_id == principal.account_id,
            Conversion.status == "succeeded",
            Conversion.downloaded_at.is_(None),
            Conversion.download_started_at.is_(None),
            Conversion.result_expires_at > now,
        )
        .values(download_started_at=now)
    )
    db.commit()
    if claimed.rowcount != 1:
        conversion = db.scalar(
            select(Conversion).where(
                Conversion.id == conversion_id,
                Conversion.account_id == principal.account_id,
            )
        )
        if conversion is None:
            raise HTTPException(status_code=404, detail="conversion_not_found")
        if conversion.status != "succeeded":
            raise HTTPException(status_code=409, detail="conversion_not_ready")
        raise HTTPException(status_code=410, detail="result_unavailable")

    path = result_path(settings, conversion_id)
    if not path.is_file():
        conversion = db.get(Conversion, conversion_id)
        if conversion:
            conversion.download_started_at = None
            conversion.status = "expired"
            conversion.error_code = "result_missing"
            db.commit()
        raise HTTPException(status_code=410, detail="result_unavailable")

    def finish_download(completed: bool):
        with SessionLocal() as session:
            conversion = session.get(Conversion, conversion_id)
            if conversion is None:
                return
            if completed:
                conversion.downloaded_at = utcnow()
                conversion.download_started_at = None
                session.commit()
                remove_result(settings, conversion_id)
            else:
                conversion.download_started_at = None
                session.commit()

    return OneTimeResultResponse(path, finish_download)
