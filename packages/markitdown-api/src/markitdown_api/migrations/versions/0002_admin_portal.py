"""Add roles, quota plans, invitations, audit events and quota counters."""

import sqlalchemy as sa
from alembic import op

revision = "0002_admin_portal"
down_revision = "0001_base"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _add_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _create_index_if_missing(
    table: str, name: str, columns: list[str], *, unique: bool = False
) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns, unique=unique)


def _seed_plan_catalog() -> None:
    plans = (
        ("free", "Free", "free", "Gratis", 300, 10 * 1024 * 1024),
        (
            "pro_weekly",
            "Pro semanal",
            "weekly",
            "$5.90 / semana",
            None,
            100 * 1024 * 1024,
        ),
        (
            "pro_monthly",
            "Pro mensual",
            "monthly",
            "$9.90 / mes",
            None,
            100 * 1024 * 1024,
        ),
        ("pro_annual", "Pro anual", "annual", "$69.90 / año", None, 100 * 1024 * 1024),
        (
            "lifetime",
            "Lifetime",
            "lifetime",
            "$99 promo / referencia $169",
            None,
            100 * 1024 * 1024,
        ),
        (
            "business",
            "Business individual",
            "lifetime",
            "$999 promo / referencia $1,999",
            None,
            100 * 1024 * 1024,
        ),
    )
    bind = op.get_bind()
    for code, name, period, price, conversion_limit, file_limit in plans:
        exists = bind.execute(
            sa.text("SELECT 1 FROM plans WHERE code = :code"), {"code": code}
        ).first()
        if exists:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO plans (id, code, name, billing_period, display_price, description, "
                "features_json, is_active, max_conversions_month, max_file_bytes, max_batch_files, "
                "ocr_enabled, created_at, updated_at) VALUES (:id, :code, :name, :period, :price, "
                ":description, '[]', false, :conversion_limit, :file_limit, 1, false, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": f"plan-{code}",
                "code": code,
                "name": name,
                "period": period,
                "price": price,
                "description": "Catálogo informativo; completar y activar cuotas desde administración. Sin cobros.",
                "conversion_limit": conversion_limit,
                "file_limit": file_limit,
            },
        )


def upgrade() -> None:
    _add_column(
        "accounts",
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
    )
    _add_column(
        "accounts",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    _add_column("accounts", sa.Column("display_name", sa.String(120), nullable=True))
    _create_index_if_missing("accounts", "ix_accounts_role", ["role"])
    _create_index_if_missing("accounts", "ix_accounts_is_active", ["is_active"])
    _add_column(
        "conversions",
        sa.Column("quota_page_units", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column(
        "conversions",
        sa.Column("ocr_units", sa.Integer(), nullable=False, server_default="0"),
    )

    if "plans" not in _tables():
        op.create_table(
            "plans",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("code", sa.String(32), nullable=False, unique=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column(
                "billing_period",
                sa.String(20),
                nullable=False,
                server_default="monthly",
            ),
            sa.Column("display_price", sa.String(80), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("features_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("max_conversions_month", sa.BigInteger(), nullable=True),
            sa.Column("max_input_bytes_month", sa.BigInteger(), nullable=True),
            sa.Column("max_pages_month", sa.BigInteger(), nullable=True),
            sa.Column("requests_per_minute", sa.Integer(), nullable=True),
            sa.Column("max_file_bytes", sa.BigInteger(), nullable=True),
            sa.Column(
                "max_batch_files", sa.Integer(), nullable=False, server_default="1"
            ),
            sa.Column(
                "ocr_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("max_ocr_units_month", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
    _create_index_if_missing("plans", "ix_plans_code", ["code"], unique=True)
    _create_index_if_missing("plans", "ix_plans_is_active", ["is_active"])

    if "plan_assignments" not in _tables():
        op.create_table(
            "plan_assignments",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column(
                "plan_id", sa.String(32), sa.ForeignKey("plans.id"), nullable=False
            ),
            sa.Column(
                "assigned_by_account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=True,
            ),
            sa.Column(
                "starts_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index(
            "ix_plan_assignments_account_id", "plan_assignments", ["account_id"]
        )
        op.create_index("ix_plan_assignments_plan_id", "plan_assignments", ["plan_id"])
        op.create_index(
            "ix_plan_assignments_account_period",
            "plan_assignments",
            ["account_id", "starts_at", "ends_at"],
        )

    if "account_quota_overrides" not in _tables():
        op.create_table(
            "account_quota_overrides",
            sa.Column(
                "account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                primary_key=True,
            ),
            sa.Column("max_conversions_month", sa.BigInteger(), nullable=True),
            sa.Column("max_input_bytes_month", sa.BigInteger(), nullable=True),
            sa.Column("max_pages_month", sa.BigInteger(), nullable=True),
            sa.Column("requests_per_minute", sa.Integer(), nullable=True),
            sa.Column("max_file_bytes", sa.BigInteger(), nullable=True),
            sa.Column("max_batch_files", sa.Integer(), nullable=True),
            sa.Column("ocr_enabled", sa.Boolean(), nullable=True),
            sa.Column("max_ocr_units_month", sa.BigInteger(), nullable=True),
            sa.Column(
                "updated_by_account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    if "invitations" not in _tables():
        op.create_table(
            "invitations",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("display_name", sa.String(120), nullable=True),
            sa.Column("role", sa.String(16), nullable=False, server_default="user"),
            sa.Column(
                "plan_id", sa.String(32), sa.ForeignKey("plans.id"), nullable=True
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column(
                "invited_by_account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=True,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index("ix_invitations_email", "invitations", ["email"])
        op.create_index(
            "ix_invitations_token_hash", "invitations", ["token_hash"], unique=True
        )
        op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"])

    if "admin_audit_events" not in _tables():
        op.create_table(
            "admin_audit_events",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "actor_account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column("action", sa.String(80), nullable=False),
            sa.Column("target_type", sa.String(40), nullable=False),
            sa.Column("target_id", sa.String(64), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index(
            "ix_admin_audit_events_actor_account_id",
            "admin_audit_events",
            ["actor_account_id"],
        )
        op.create_index(
            "ix_admin_audit_events_action", "admin_audit_events", ["action"]
        )
        op.create_index(
            "ix_admin_audit_events_target_id", "admin_audit_events", ["target_id"]
        )
        op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at"])

    _seed_plan_catalog()


def _seed_plan_catalog() -> None:
    plans = (
        ("free", "Free", "free", "Gratis", 300, 10 * 1024 * 1024),
        (
            "pro_weekly",
            "Pro semanal",
            "weekly",
            "$5.90 / semana",
            None,
            100 * 1024 * 1024,
        ),
        (
            "pro_monthly",
            "Pro mensual",
            "monthly",
            "$9.90 / mes",
            None,
            100 * 1024 * 1024,
        ),
        ("pro_annual", "Pro anual", "annual", "$69.90 / año", None, 100 * 1024 * 1024),
        (
            "lifetime",
            "Lifetime",
            "lifetime",
            "$99 promo / referencia $169",
            None,
            100 * 1024 * 1024,
        ),
        (
            "business",
            "Business individual",
            "lifetime",
            "$999 promo / referencia $1,999",
            None,
            100 * 1024 * 1024,
        ),
    )
    bind = op.get_bind()
    for code, name, period, price, conversion_limit, file_limit in plans:
        if bind.execute(
            sa.text("SELECT 1 FROM plans WHERE code=:code"), {"code": code}
        ).first():
            continue
        bind.execute(
            sa.text(
                "INSERT INTO plans (id, code, name, billing_period, display_price, description, "
                "features_json, is_active, max_conversions_month, max_file_bytes, max_batch_files, "
                "ocr_enabled, created_at, updated_at) VALUES (:id, :code, :name, :period, :price, "
                ":description, '[]', false, :conversion_limit, :file_limit, 1, false, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": f"plan-{code}",
                "code": code,
                "name": name,
                "period": period,
                "price": price,
                "description": "Catálogo informativo; completar y activar cuotas desde administración. Sin cobros.",
                "conversion_limit": conversion_limit,
                "file_limit": file_limit,
            },
        )


def downgrade() -> None:
    raise RuntimeError(
        "Admin portal migration is forward-only; restore a verified backup to roll back."
    )
