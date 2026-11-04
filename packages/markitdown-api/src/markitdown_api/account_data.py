from sqlalchemy.orm import Session

from .entitlements import (
    effective_limits,
    get_current_plan,
    get_quota_snapshot,
    month_window,
)
from .models import Account, Plan, utcnow


def current_plan(db: Session, account_id: str) -> Plan | None:
    return get_current_plan(db, account_id)


def serialize_account(
    db: Session, account: Account, settings, *, include_usage: bool = True
) -> dict:
    plan = current_plan(db, account.id)
    limits = effective_limits(db, account.id, settings)[1]
    period_start, period_end = month_window(utcnow(), settings.quota_timezone)
    usage = (
        get_quota_snapshot(db, account.id, settings).usage if include_usage else None
    )
    plan_data = serialize_plan(plan) if plan else None
    if plan_data is not None:
        plan_data["limits"] = limits
    return {
        "id": account.id,
        "email": account.email,
        "display_name": account.display_name,
        "role": account.role,
        "is_active": account.is_active,
        "verified_at": account.verified_at,
        "created_at": account.created_at,
        "plan": plan_data,
        "limits": limits,
        "usage": usage,
        "period_start": period_start,
        "period_end": period_end,
    }


def serialize_plan(plan: Plan) -> dict:
    import json

    try:
        features = json.loads(plan.features_json or "[]")
    except (TypeError, ValueError):
        features = []
    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "price": plan.display_price,
        "interval": plan.billing_period,
        "active": plan.is_active,
        "billing_period": plan.billing_period,
        "display_price": plan.display_price,
        "description": plan.description,
        "features": features if isinstance(features, list) else [],
        "is_active": plan.is_active,
        "limits": {
            "max_conversions_month": plan.max_conversions_month,
            "max_input_bytes_month": plan.max_input_bytes_month,
            "max_pages_month": plan.max_pages_month,
            "requests_per_minute": plan.requests_per_minute,
            "max_file_bytes": plan.max_file_bytes,
            "max_batch_files": plan.max_batch_files,
            "ocr_enabled": plan.ocr_enabled,
            "max_ocr_units_month": plan.max_ocr_units_month,
        },
    }
