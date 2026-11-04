"""Baseline schema shipped before the admin portal."""

import sqlalchemy as sa
from alembic import op

revision = "0001_base"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("accounts"):
        op.create_table(
            "accounts",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(256), nullable=False),
            sa.Column(
                "session_version", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)

    if not _has_table("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("prefix", sa.String(24), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_api_keys_account_id", "api_keys", ["account_id"])
        op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
        op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    if not _has_table("account_tokens"):
        op.create_table(
            "account_tokens",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("purpose", sa.String(32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index(
            "ix_account_tokens_account_id", "account_tokens", ["account_id"]
        )
        op.create_index(
            "ix_account_tokens_token_hash",
            "account_tokens",
            ["token_hash"],
            unique=True,
        )
        op.create_index("ix_account_tokens_purpose", "account_tokens", ["purpose"])
        op.create_index(
            "ix_account_tokens_expires_at", "account_tokens", ["expires_at"]
        )

    if not _has_table("conversions"):
        op.create_table(
            "conversions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "account_id",
                sa.String(32),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("source_type", sa.String(16), nullable=False),
            sa.Column("input_extension", sa.String(20), nullable=True),
            sa.Column("input_bytes", sa.Integer(), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("output_char_count", sa.Integer(), nullable=True),
            sa.Column("output_utf8_bytes", sa.Integer(), nullable=True),
            sa.Column(
                "ocr_requested", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("external_provider", sa.String(64), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("download_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
        op.create_index("ix_conversions_account_id", "conversions", ["account_id"])
        op.create_index("ix_conversions_status", "conversions", ["status"])
        op.create_index(
            "ix_conversions_result_expires_at", "conversions", ["result_expires_at"]
        )
        op.create_index(
            "ix_conversions_account_created",
            "conversions",
            ["account_id", "created_at"],
        )
        op.create_index(
            "ix_conversions_status_created", "conversions", ["status", "created_at"]
        )


def downgrade() -> None:
    # The baseline may be stamped onto an existing production DB; do not drop it.
    pass
