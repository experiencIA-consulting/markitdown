import csv
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from .account_data import serialize_plan
from .auth import get_db, require_admin_account, require_session_account
from .config import Settings, get_settings
from .entitlements import get_quota_snapshot, month_window
from .models import Account, Conversion, Plan, utcnow

router = APIRouter(tags=["reports"])


@router.get("/v1/admin/overview")
async def admin_overview(
    request: Request,
    admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    start, end = month_window(utcnow(), settings.quota_timezone)
    success = and_(
        Conversion.status.in_(("succeeded", "expired")),
        Conversion.output_utf8_bytes.is_not(None),
    )
    totals = db.execute(
        select(
            func.count(Conversion.id),
            func.coalesce(func.sum(case((success, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((Conversion.status == "failed", 1), else_=0)), 0
            ),
            func.coalesce(func.sum(Conversion.input_bytes), 0),
            func.coalesce(
                func.sum(case((success, Conversion.quota_page_units), else_=0)), 0
            ),
            func.coalesce(func.sum(Conversion.ocr_units), 0),
            func.coalesce(func.sum(Conversion.output_char_count), 0),
        ).where(Conversion.created_at >= start, Conversion.created_at < end)
    ).one()
    users = (
        db.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.is_active.is_(True), Account.role == "user")
        )
        or 0
    )
    recent = db.execute(
        select(Conversion, Account.email)
        .join(Account, Account.id == Conversion.account_id)
        .order_by(Conversion.created_at.desc())
        .limit(8)
    ).all()
    redis = getattr(request.app.state, "redis", None)
    try:
        queue_depth = await redis.llen("markitdown:jobs") if redis else None
        worker_online = (
            bool(await redis.exists("markitdown:worker:heartbeat")) if redis else False
        )
    except RedisError:
        queue_depth = None
        worker_online = False
    return {
        "conversionCount": int(totals[0] or 0),
        "successCount": int(totals[1] or 0),
        "failedCount": int(totals[2] or 0),
        "userCount": int(users),
        "inputBytes": int(totals[3] or 0),
        "pageUnits": int(totals[4] or 0),
        "ocrUnits": int(totals[5] or 0),
        "outputCharCount": int(totals[6] or 0),
        "periodStart": start,
        "periodEnd": end,
        "queueDepth": queue_depth,
        "workerOnline": worker_online,
        "recentActivity": [
            {
                "id": item.id,
                "email": email,
                "type": item.source_type,
                "format": item.input_extension,
                "status": item.status,
                "createdAt": item.created_at,
            }
            for item, email in recent
        ],
        "adminAccountId": admin.id,
    }


@router.get("/v1/account/overview")
def account_overview(
    account: Account = Depends(require_session_account),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snapshot = get_quota_snapshot(db, account.id, settings)
    successful = (
        db.scalar(
            select(func.count())
            .select_from(Conversion)
            .where(
                Conversion.account_id == account.id,
                Conversion.created_at >= snapshot.period_start,
                Conversion.created_at < snapshot.period_end,
                Conversion.status.in_(("succeeded", "expired")),
                Conversion.output_utf8_bytes.is_not(None),
            )
        )
        or 0
    )
    return {
        "conversionCount": snapshot.usage["conversions"],
        "successCount": int(successful),
        "inputBytes": snapshot.usage["input_bytes"],
        "pageUnits": snapshot.usage["pages"],
        "ocrUnits": snapshot.usage["ocr_units"],
        "limits": snapshot.limits,
        "periodStart": snapshot.period_start,
        "periodEnd": snapshot.period_end,
    }


def _report_range(from_date, to_date, group_by, timezone_name):
    if group_by not in {"day", "week", "month"}:
        raise HTTPException(status_code=422, detail="invalid_group_by")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=422, detail="invalid_timezone") from None
    end = to_date or utcnow()
    start = from_date or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="report_dates_must_include_timezone"
        )
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start >= end:
        raise HTTPException(status_code=422, detail="invalid_report_range")
    if end - start > timedelta(days=3660):
        raise HTTPException(status_code=422, detail="report_range_too_large")
    return start, end


def _report_rows(
    db, start, end, group_by, timezone_name, account_id, status, source_type
):
    filters = [Conversion.created_at >= start, Conversion.created_at < end]
    if account_id:
        filters.append(Conversion.account_id == account_id)
    if status:
        if status not in {"queued", "running", "succeeded", "failed", "expired"}:
            raise HTTPException(status_code=422, detail="invalid_conversion_status")
        filters.append(Conversion.status == status)
    if source_type:
        if source_type not in {"upload", "url"}:
            raise HTTPException(status_code=422, detail="invalid_source_type")
        filters.append(Conversion.source_type == source_type)
    success = and_(
        Conversion.status.in_(("succeeded", "expired")),
        Conversion.output_utf8_bytes.is_not(None),
    )
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        bucket = func.date_trunc(
            group_by, func.timezone(timezone_name, Conversion.created_at)
        ).label("bucket")
    elif dialect == "sqlite":
        fmt = {"day": "%Y-%m-%d", "week": "%Y-%W", "month": "%Y-%m"}[group_by]
        bucket = func.strftime(fmt, Conversion.created_at).label("bucket")
    else:
        raise HTTPException(
            status_code=501, detail="report_database_dialect_unsupported"
        )

    grouped = db.execute(
        select(
            bucket,
            func.count(Conversion.id).label("conversion_count"),
            func.coalesce(func.sum(case((success, 1), else_=0)), 0).label(
                "success_count"
            ),
            func.coalesce(
                func.sum(case((Conversion.status == "failed", 1), else_=0)), 0
            ).label("failed_count"),
            func.coalesce(func.sum(Conversion.input_bytes), 0).label("input_bytes"),
            func.coalesce(
                func.sum(case((success, Conversion.quota_page_units), else_=0)), 0
            ).label("page_units"),
            func.coalesce(func.sum(Conversion.ocr_units), 0).label("ocr_units"),
            func.coalesce(func.sum(Conversion.output_char_count), 0).label(
                "output_char_count"
            ),
            func.coalesce(func.sum(Conversion.output_utf8_bytes), 0).label(
                "output_utf8_bytes"
            ),
            func.coalesce(func.avg(Conversion.duration_ms), 0).label("avg_duration_ms"),
        )
        .where(*filters)
        .group_by(bucket)
        .order_by(bucket)
        .limit(4000)
    ).all()
    totals = db.execute(
        select(
            func.count(Conversion.id),
            func.coalesce(func.sum(case((success, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((Conversion.status == "failed", 1), else_=0)), 0
            ),
            func.coalesce(func.sum(Conversion.input_bytes), 0),
            func.coalesce(
                func.sum(case((success, Conversion.quota_page_units), else_=0)), 0
            ),
            func.coalesce(func.sum(Conversion.ocr_units), 0),
            func.coalesce(func.sum(Conversion.output_char_count), 0),
            func.coalesce(func.sum(Conversion.output_utf8_bytes), 0),
        ).where(*filters)
    ).one()
    series = [
        {
            "bucket": (
                row.bucket.isoformat()
                if hasattr(row.bucket, "isoformat")
                else str(row.bucket)
            ),
            "conversionCount": int(row.conversion_count or 0),
            "successCount": int(row.success_count or 0),
            "failedCount": int(row.failed_count or 0),
            "inputBytes": int(row.input_bytes or 0),
            "pageUnits": int(row.page_units or 0),
            "ocrUnits": int(row.ocr_units or 0),
            "outputCharCount": int(row.output_char_count or 0),
            "outputUtf8Bytes": int(row.output_utf8_bytes or 0),
            "avgDurationMs": float(row.avg_duration_ms or 0),
        }
        for row in grouped
    ]
    names = (
        "totalConversions",
        "successCount",
        "failedCount",
        "inputBytes",
        "pageUnits",
        "ocrUnits",
        "outputCharCount",
        "outputUtf8Bytes",
    )
    return series, dict(zip(names, (int(value or 0) for value in totals)))


@router.get("/v1/admin/reports/consumption")
def consumption_report(
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    group_by: str = Query(default="day", alias="group_by"),
    timezone_name: str = Query(default="America/Mazatlan", alias="timezone"),
    account_id: str | None = Query(default=None, alias="accountId"),
    status: str | None = None,
    source_type: str | None = Query(default=None, alias="sourceType"),
    _admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
):
    start, end = _report_range(from_date, to_date, group_by, timezone_name)
    series, totals = _report_rows(
        db, start, end, group_by, timezone_name, account_id, status, source_type
    )
    return {
        **totals,
        "series": series,
        "groupBy": group_by,
        "timezone": timezone_name,
        "from": start,
        "to": end,
    }


@router.get("/v1/admin/reports/consumption.csv")
def export_consumption_report(
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    group_by: str = Query(default="day", alias="group_by"),
    timezone_name: str = Query(default="America/Mazatlan", alias="timezone"),
    account_id: str | None = Query(default=None, alias="accountId"),
    status: str | None = None,
    source_type: str | None = Query(default=None, alias="sourceType"),
    _admin: Account = Depends(require_admin_account),
    db: Session = Depends(get_db),
):
    start, end = _report_range(from_date, to_date, group_by, timezone_name)
    series, _totals = _report_rows(
        db, start, end, group_by, timezone_name, account_id, status, source_type
    )
    columns = [
        "bucket",
        "conversionCount",
        "successCount",
        "failedCount",
        "inputBytes",
        "pageUnits",
        "ocrUnits",
        "outputCharCount",
        "outputUtf8Bytes",
        "avgDurationMs",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(series)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="markitdown-consumption.csv"'
        },
    )


public_router = APIRouter(prefix="/v1", tags=["plans"])


@public_router.get("/plans")
def public_plans(db: Session = Depends(get_db)):
    plans = db.scalars(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.created_at.asc())
    ).all()
    return [serialize_plan(plan) for plan in plans]
