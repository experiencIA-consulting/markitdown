from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountToken(Base):
    __tablename__ = "account_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    billing_period: Mapped[str] = mapped_column(String(20), default="monthly")
    display_price: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    features_json: Mapped[str] = mapped_column(Text, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    max_conversions_month: Mapped[int | None] = mapped_column(BigInteger)
    max_input_bytes_month: Mapped[int | None] = mapped_column(BigInteger)
    max_pages_month: Mapped[int | None] = mapped_column(BigInteger)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer)
    max_file_bytes: Mapped[int | None] = mapped_column(BigInteger)
    max_batch_files: Mapped[int] = mapped_column(Integer, default=1)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_ocr_units_month: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlanAssignment(Base):
    __tablename__ = "plan_assignments"
    __table_args__ = (
        Index(
            "ix_plan_assignments_account_period", "account_id", "starts_at", "ends_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), index=True)
    assigned_by_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AccountQuotaOverride(Base):
    __tablename__ = "account_quota_overrides"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    max_conversions_month: Mapped[int | None] = mapped_column(BigInteger)
    max_input_bytes_month: Mapped[int | None] = mapped_column(BigInteger)
    max_pages_month: Mapped[int | None] = mapped_column(BigInteger)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer)
    max_file_bytes: Mapped[int | None] = mapped_column(BigInteger)
    max_batch_files: Mapped[int | None] = mapped_column(Integer)
    ocr_enabled: Mapped[bool | None] = mapped_column(Boolean)
    max_ocr_units_month: Mapped[int | None] = mapped_column(BigInteger)
    updated_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="user")
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Conversion(Base):
    __tablename__ = "conversions"
    __table_args__ = (
        Index("ix_conversions_account_created", "account_id", "created_at"),
        Index("ix_conversions_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    input_extension: Mapped[str | None] = mapped_column(String(20))
    input_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    quota_page_units: Mapped[int] = mapped_column(Integer, default=0)
    ocr_units: Mapped[int] = mapped_column(Integer, default=0)
    output_char_count: Mapped[int | None] = mapped_column(Integer)
    output_utf8_bytes: Mapped[int | None] = mapped_column(Integer)
    ocr_requested: Mapped[bool] = mapped_column(default=False)
    external_provider: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    download_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
