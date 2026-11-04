import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AccountQuotaOverride, Conversion, Plan, PlanAssignment, utcnow

SUCCESS_STATES = ("succeeded", "expired")


def plan_period_end(start: datetime, billing_period: str) -> datetime | None:
    if billing_period == "weekly":
        return start + timedelta(days=7)
    if billing_period == "monthly":
        year = start.year + (1 if start.month == 12 else 0)
        month = 1 if start.month == 12 else start.month + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)
    if billing_period == "annual":
        year = start.year + 1
        day = min(start.day, calendar.monthrange(year, start.month)[1])
        return start.replace(year=year, day=day)
    return None


@dataclass(frozen=True)
class QuotaSnapshot:
    limits: dict[str, int | bool | None]
    usage: dict[str, int]
    period_start: datetime
    period_end: datetime
    plan_configured: bool = False


def month_window(now: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    start_local = datetime(local_now.year, local_now.month, 1, tzinfo=zone)
    if local_now.month == 12:
        next_local = datetime(local_now.year + 1, 1, 1, tzinfo=zone)
    else:
        next_local = datetime(local_now.year, local_now.month + 1, 1, tzinfo=zone)
    return start_local.astimezone(timezone.utc), next_local.astimezone(timezone.utc)


def get_current_plan(
    db: Session, account_id: str, now: datetime | None = None
) -> Plan | None:
    now = now or utcnow()
    assignment = db.scalar(
        select(PlanAssignment)
        .where(
            PlanAssignment.account_id == account_id,
            PlanAssignment.starts_at <= now,
            or_(PlanAssignment.ends_at.is_(None), PlanAssignment.ends_at > now),
        )
        .order_by(PlanAssignment.starts_at.desc())
        .limit(1)
    )
    assigned = db.get(Plan, assignment.plan_id) if assignment else None
    if assigned is not None:
        return assigned
    return db.scalar(select(Plan).where(Plan.code == "free", Plan.is_active.is_(True)))


def effective_limits(
    db: Session, account_id: str, settings: Settings, now: datetime | None = None
) -> tuple[Plan | None, dict[str, int | bool | None]]:
    now = now or utcnow()
    plan = get_current_plan(db, account_id, now)
    override = db.get(AccountQuotaOverride, account_id)

    def effective(field: str, default=None):
        value = getattr(override, field, None) if override else None
        if value is not None:
            return value
        value = getattr(plan, field, None) if plan else None
        return default if value is None else value

    max_file_bytes = effective("max_file_bytes", settings.max_upload_bytes)
    max_batch_files = effective("max_batch_files", 1)
    requests_per_minute = effective(
        "requests_per_minute", settings.rate_limit_per_minute
    )
    return plan, {
        "max_conversions_month": effective("max_conversions_month"),
        "max_input_bytes_month": effective("max_input_bytes_month"),
        "max_pages_month": effective("max_pages_month"),
        "requests_per_minute": min(settings.rate_limit_per_minute, requests_per_minute),
        "max_file_bytes": min(settings.max_upload_bytes, max_file_bytes),
        "max_batch_files": min(settings.max_pending_jobs, max_batch_files),
        "ocr_enabled": effective("ocr_enabled", False),
        "max_ocr_units_month": effective("max_ocr_units_month"),
    }


def get_quota_snapshot(
    db: Session,
    account_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> QuotaSnapshot:
    now = now or utcnow()
    period_start, period_end = month_window(now, settings.quota_timezone)
    _plan, limits = effective_limits(db, account_id, settings, now)

    successful = Conversion.status.in_(SUCCESS_STATES)
    period = (Conversion.created_at >= period_start, Conversion.created_at < period_end)
    usage = {
        "conversions": int(
            db.scalar(
                select(func.count())
                .select_from(Conversion)
                .where(
                    Conversion.account_id == account_id,
                    *period,
                    successful,
                    Conversion.output_utf8_bytes.is_not(None),
                )
            )
            or 0
        ),
        # Bytes count once accepted into processing, including conversions that later fail.
        "input_bytes": int(
            db.scalar(
                select(func.coalesce(func.sum(Conversion.input_bytes), 0)).where(
                    Conversion.account_id == account_id,
                    *period,
                    Conversion.input_bytes.is_not(None),
                )
            )
            or 0
        ),
        "pages": int(
            db.scalar(
                select(func.coalesce(func.sum(Conversion.quota_page_units), 0)).where(
                    Conversion.account_id == account_id,
                    *period,
                    successful,
                )
            )
            or 0
        ),
        # Count external OCR requests actually sent, including one from a conversion that later fails.
        "ocr_units": int(
            db.scalar(
                select(func.coalesce(func.sum(Conversion.ocr_units), 0)).where(
                    Conversion.account_id == account_id,
                    *period,
                )
            )
            or 0
        ),
    }
    return QuotaSnapshot(
        limits=limits,
        usage=usage,
        period_start=period_start,
        period_end=period_end,
        plan_configured=bool(_plan and _plan.is_active),
    )


def quota_exceeded(snapshot: QuotaSnapshot, field: str, *, additional: int = 0) -> bool:
    limit = snapshot.limits.get(field)
    if limit is None:
        return False
    usage_key = {
        "max_conversions_month": "conversions",
        "max_input_bytes_month": "input_bytes",
        "max_pages_month": "pages",
        "max_ocr_units_month": "ocr_units",
    }.get(field)
    if usage_key is None:
        return False
    return snapshot.usage[usage_key] + additional > int(limit)
