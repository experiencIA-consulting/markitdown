import asyncio
import json
import re
import smtplib
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .account_data import serialize_account, serialize_plan
from .auth import get_db, require_admin_account
from .config import Settings, get_settings
from .mail import send_account_email
from .models import (
    Account,
    AccountQuotaOverride,
    AdminAuditEvent,
    ApiKey,
    Invitation,
    Plan,
    PlanAssignment,
    utcnow,
)
from .security import generate_account_token

router = APIRouter(prefix="/v1/admin", tags=["admin"])
LIMIT_FIELDS = {
    "max_conversions_month",
    "max_input_bytes_month",
    "max_pages_month",
    "requests_per_minute",
    "max_file_bytes",
    "max_batch_files",
    "ocr_enabled",
    "max_ocr_units_month",
}


class AdminInvite(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display_name", "displayName", "name"),
    )
    role: Literal["user", "admin"] = "user"
    plan_id: str = Field(validation_alias=AliasChoices("plan_id", "planId"))


class AdminUserPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    display_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display_name", "displayName", "name"),
    )
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None
    status: str | None = None
    plan_id: str | None = Field(
        default=None, validation_alias=AliasChoices("plan_id", "planId")
    )
    limits: dict[str, Any] | None = None


class PlanWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    code: str | None = None
    name: str | None = None
    billing_period: str | None = Field(
        default=None,
        validation_alias=AliasChoices("billing_period", "billingPeriod", "interval"),
    )
    display_price: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display_price", "displayPrice", "price"),
    )
    description: str | None = None
    features: list[str] | None = None
    is_active: bool | None = Field(
        default=None, validation_alias=AliasChoices("is_active", "isActive", "active")
    )
    limits: dict[str, Any] | None = None
    max_conversions_month: int | None = None
    max_input_bytes_month: int | None = None
    max_pages_month: int | None = None
    requests_per_minute: int | None = None
    max_file_bytes: int | None = None
    max_batch_files: int | None = None
    ocr_enabled: bool | None = None
    max_ocr_units_month: int | None = None


def _audit(
    db: Session,
    actor: Account,
    action: str,
    target_type: str,
    target_id: str | None,
    changed_fields: list[str],
) -> None:
    db.add(
        AdminAuditEvent(
            id=uuid4().hex,
            actor_account_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details_json=json.dumps({"changed_fields": sorted(set(changed_fields))}),
        )
    )


def _serialize_user(
    db: Session, user: Account, settings: Settings, *, with_usage: bool
):
    return serialize_account(db, user, settings, include_usage=with_usage)


def _current_assignment(db: Session, account_id: str, now: datetime | None = None):
    now = now or utcnow()
    return db.scalar(
        select(PlanAssignment)
        .where(
            PlanAssignment.account_id == account_id,
            PlanAssignment.starts_at <= now,
            or_(PlanAssignment.ends_at.is_(None), PlanAssignment.ends_at > now),
        )
        .order_by(PlanAssignment.starts_at.desc())
        .limit(1)
    )


def _assign_plan(
    db: Session, account: Account, plan: Plan | None, admin: Account
) -> bool:
    now = utcnow()
    current = _current_assignment(db, account.id, now)
    if current and plan and current.plan_id == plan.id:
        return False
    if current:
        current.ends_at = now
    if plan:
        db.add(
            PlanAssignment(
                id=uuid4().hex,
                account_id=account.id,
                plan_id=plan.id,
                assigned_by_account_id=admin.id,
                starts_at=now,
            )
        )
    return True


def _normalized_status(payload: AdminUserPatch) -> bool | None:
    if payload.is_active is not None:
        return payload.is_active
    if payload.status is None:
        return None
    status = payload.status.strip().lower()
    if status in {"active", "verified"}:
        return True
    if status in {"inactive", "suspended", "disabled"}:
        return False
    raise HTTPException(status_code=422, detail="invalid_account_status")


def _plan_limits(payload: PlanWrite) -> dict[str, Any]:
    limits = dict(payload.limits or {})
    for field in LIMIT_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            limits[field] = value
    return {key: value for key, value in limits.items() if key in LIMIT_FIELDS}


def _apply_plan_limits(plan: Plan, limits: dict[str, Any]) -> None:
    for field, value in limits.items():
        if field == "ocr_enabled":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail="invalid_ocr_enabled")
        elif value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise HTTPException(status_code=422, detail=f"invalid_{field}")
        setattr(plan, field, value)


def _validate_plan(plan: Plan, settings: Settings) -> None:
    if plan.billing_period not in {"free", "weekly", "monthly", "annual", "lifetime"}:
        raise HTTPException(status_code=422, detail="invalid_billing_period")
    if (
        plan.max_file_bytes is not None
        and not 0 < plan.max_file_bytes <= settings.max_upload_bytes
    ):
        raise HTTPException(
            status_code=422, detail="plan_file_limit_exceeds_global_limit"
        )
    if plan.max_batch_files != 1:
        raise HTTPException(status_code=422, detail="batch_processing_not_enabled")
    if plan.is_active:
        required = (
            "max_conversions_month",
            "max_input_bytes_month",
            "max_pages_month",
            "requests_per_minute",
            "max_file_bytes",
        )
        if any(getattr(plan, field) is None for field in required):
            raise HTTPException(status_code=422, detail="plan_quota_matrix_incomplete")
        if any(getattr(plan, field) <= 0 for field in required):
            raise HTTPException(
                status_code=422, detail="active_plan_quotas_must_be_positive"
            )
        if plan.requests_per_minute > settings.rate_limit_per_minute:
            raise HTTPException(
                status_code=422, detail="plan_rpm_exceeds_global_safety_limit"
            )
        if plan.ocr_enabled and (
            plan.max_ocr_units_month is None or plan.max_ocr_units_month <= 0
        ):
            raise HTTPException(status_code=422, detail="ocr_quota_required")


def _send_invitation(settings: Settings, email: str, token: str) -> None:
    link = f"{settings.public_base_url}/accept-invitation#token={token}"
    send_account_email(
        settings,
        email,
        "Invitación a MarkItDown",
        f"Un administrador te invitó a MarkItDown. Completa tu cuenta: {link}",
    )


@router.get("/users")
def list_users(
    search: str | None = None,
    status: str | None = None,
    plan_id: str | None = Query(default=None, alias="planId"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    query = select(Account)
    if search:
        term = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(Account.email).like(term)
            | func.lower(func.coalesce(Account.display_name, "")).like(term)
        )
    if status in {"active", "inactive", "suspended"}:
        query = query.where(Account.is_active.is_(status == "active"))
    if plan_id:
        assignments = select(PlanAssignment.account_id).where(
            PlanAssignment.plan_id == plan_id,
            PlanAssignment.starts_at <= utcnow(),
            or_(PlanAssignment.ends_at.is_(None), PlanAssignment.ends_at > utcnow()),
        )
        query = query.where(Account.id.in_(assignments))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = db.scalars(
        query.order_by(Account.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {
        "items": [
            _serialize_user(db, user, settings, with_usage=False) for user in users
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post("/users", status_code=202)
async def invite_user(
    payload: AdminInvite,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="invalid_email")
    if not settings.smtp_configured:
        raise HTTPException(status_code=503, detail="email_delivery_not_configured")
    if db.scalar(select(Account.id).where(Account.email == email)):
        raise HTTPException(status_code=409, detail="account_already_exists")
    plan = db.get(Plan, payload.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status_code=422, detail="plan_not_assignable")
    now = utcnow()
    db.query(Invitation).filter(
        func.lower(Invitation.email) == email,
        Invitation.accepted_at.is_(None),
        Invitation.expires_at > now,
    ).update({Invitation.expires_at: now}, synchronize_session=False)
    token, token_hash = generate_account_token()
    invitation = Invitation(
        id=uuid4().hex,
        email=email,
        display_name=payload.display_name,
        role=payload.role,
        plan_id=plan.id,
        token_hash=token_hash,
        invited_by_account_id=admin.id,
        expires_at=now + timedelta(days=7),
    )
    db.add(invitation)
    _audit(
        db,
        admin,
        "user.invited",
        "invitation",
        invitation.id,
        ["email", "role", "plan_id"],
    )
    db.commit()
    try:
        await asyncio.to_thread(_send_invitation, settings, email, token)
    except (OSError, TimeoutError, smtplib.SMTPException):
        raise HTTPException(status_code=503, detail="invitation_email_failed") from None
    return {
        "id": invitation.id,
        "email": invitation.email,
        "display_name": invitation.display_name,
        "role": invitation.role,
        "planId": invitation.plan_id,
        "status": "invited",
        "expiresAt": invitation.expires_at,
    }


@router.get("/users/{account_id}")
def get_user(
    account_id: str,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = db.get(Account, account_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return _serialize_user(db, user, settings, with_usage=True)


@router.patch("/users/{account_id}")
def update_user(
    account_id: str,
    payload: AdminUserPatch,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = db.get(Account, account_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    fields = payload.model_fields_set
    changed: list[str] = []
    if "display_name" in fields:
        user.display_name = (
            payload.display_name.strip()[:120] if payload.display_name else None
        )
        changed.append("display_name")
    if "role" in fields and payload.role is not None and payload.role != user.role:
        if user.role == "admin" and payload.role != "admin":
            admins = (
                db.scalar(
                    select(func.count())
                    .select_from(Account)
                    .where(Account.role == "admin", Account.is_active.is_(True))
                )
                or 0
            )
            if admins <= 1 and user.is_active:
                raise HTTPException(status_code=409, detail="cannot_disable_last_admin")
        user.role = payload.role
        user.session_version += 1
        changed.append("role")
    active = _normalized_status(payload)
    if active is not None and active != user.is_active:
        if not active and user.role == "admin":
            admins = (
                db.scalar(
                    select(func.count())
                    .select_from(Account)
                    .where(Account.role == "admin", Account.is_active.is_(True))
                )
                or 0
            )
            if admins <= 1:
                raise HTTPException(status_code=409, detail="cannot_suspend_last_admin")
        user.is_active = active
        user.session_version += 1
        changed.append("is_active")
        if not active:
            db.query(ApiKey).filter(
                ApiKey.account_id == user.id, ApiKey.revoked_at.is_(None)
            ).update({ApiKey.revoked_at: utcnow()}, synchronize_session=False)
    if "plan_id" in fields:
        plan = db.get(Plan, payload.plan_id) if payload.plan_id else None
        if payload.plan_id and (plan is None or not plan.is_active):
            raise HTTPException(status_code=422, detail="plan_not_assignable")
        if _assign_plan(db, user, plan, admin):
            changed.append("plan_id")
    if payload.limits is not None:
        override = db.get(AccountQuotaOverride, user.id)
        if override is None:
            override = AccountQuotaOverride(account_id=user.id)
            db.add(override)
        for field, value in payload.limits.items():
            if field not in LIMIT_FIELDS:
                raise HTTPException(status_code=422, detail="unknown_quota_field")
            if field == "ocr_enabled":
                if value is not None and not isinstance(value, bool):
                    raise HTTPException(status_code=422, detail=f"invalid_{field}")
            elif value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise HTTPException(status_code=422, detail=f"invalid_{field}")
            if (
                field in {"requests_per_minute", "max_file_bytes", "max_batch_files"}
                and value is not None
                and value <= 0
            ):
                raise HTTPException(status_code=422, detail=f"invalid_{field}")
            if (
                field == "max_file_bytes"
                and value is not None
                and value > settings.max_upload_bytes
            ):
                raise HTTPException(
                    status_code=422, detail="plan_file_limit_exceeds_global_limit"
                )
            if field == "max_batch_files" and value is not None and value != 1:
                raise HTTPException(
                    status_code=422, detail="batch_processing_not_enabled"
                )
            if (
                field == "requests_per_minute"
                and value is not None
                and value > settings.rate_limit_per_minute
            ):
                raise HTTPException(
                    status_code=422, detail="plan_rpm_exceeds_global_safety_limit"
                )
            setattr(override, field, value)
            changed.append(f"limits.{field}")
        override.updated_by_account_id = admin.id
    if changed:
        _audit(db, admin, "user.updated", "account", user.id, changed)
    db.commit()
    db.refresh(user)
    return _serialize_user(db, user, settings, with_usage=True)


@router.get("/plans")
def list_plans(
    admin: Account = Depends(require_admin_account), db: Session = Depends(get_db)
):
    return [
        serialize_plan(plan)
        for plan in db.scalars(select(Plan).order_by(Plan.created_at.asc())).all()
    ]


@router.post("/plans", status_code=201)
def create_plan(
    payload: PlanWrite,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="plan_name_required")
    code = payload.code or re.sub(
        r"[^a-z0-9]+", "_", payload.name.strip().lower()
    ).strip("_")
    if not code or db.scalar(select(Plan.id).where(Plan.code == code)):
        raise HTTPException(status_code=409, detail="plan_code_already_exists")
    plan = Plan(
        id=uuid4().hex,
        code=code,
        name=payload.name.strip()[:100],
        billing_period=payload.billing_period or "monthly",
        display_price=payload.display_price,
        description=payload.description,
        features_json=json.dumps(payload.features or [], ensure_ascii=False),
        is_active=bool(payload.is_active),
        max_batch_files=1,
        ocr_enabled=False,
    )
    _apply_plan_limits(plan, _plan_limits(payload))
    _validate_plan(plan, settings)
    db.add(plan)
    _audit(
        db,
        admin,
        "plan.created",
        "plan",
        plan.id,
        ["code", "name", "limits", "is_active"],
    )
    db.commit()
    db.refresh(plan)
    return serialize_plan(plan)


@router.patch("/plans/{plan_id}")
def update_plan(
    plan_id: str,
    payload: PlanWrite,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")
    changed: list[str] = []
    data = payload.model_dump(exclude_unset=True)
    for field in (
        "name",
        "billing_period",
        "display_price",
        "description",
        "is_active",
    ):
        if field in data and data[field] is not None:
            setattr(plan, field, data[field])
            changed.append(field)
    if "features" in data and data["features"] is not None:
        plan.features_json = json.dumps(data["features"], ensure_ascii=False)
        changed.append("features")
    limits = _plan_limits(payload)
    _apply_plan_limits(plan, limits)
    changed.extend(f"limits.{key}" for key in limits)
    _validate_plan(plan, settings)
    if changed:
        _audit(db, admin, "plan.updated", "plan", plan.id, changed)
    db.commit()
    db.refresh(plan)
    return serialize_plan(plan)
