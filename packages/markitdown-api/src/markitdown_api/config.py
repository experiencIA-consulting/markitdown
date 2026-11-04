import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    storage_dir: Path
    jwt_secret: str
    api_key_pepper: str
    public_base_url: str
    max_upload_bytes: int
    max_pending_jobs: int
    max_output_bytes: int
    max_stored_result_bytes: int
    max_conversion_memory_bytes: int
    max_archive_entries: int
    max_archive_expanded_bytes: int
    result_ttl_minutes: int
    conversion_timeout_seconds: int
    rate_limit_per_minute: int
    quota_timezone: str
    public_signup_enabled: bool
    email_verification_required: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_starttls: bool
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_api_version: str
    azure_openai_model: str

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def ocr_configured(self) -> bool:
        return bool(
            self.azure_openai_api_key
            and self.azure_openai_endpoint
            and self.azure_openai_model
        )

    def validate_secrets(self, *, require_auth: bool = True) -> None:
        if require_auth:
            if len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must contain at least 32 characters")
            if len(self.api_key_pepper) < 32:
                raise RuntimeError("API_KEY_PEPPER must contain at least 32 characters")
            public_url = urlsplit(self.public_base_url)
            local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
            if (
                not public_url.hostname
                or public_url.username
                or public_url.password
                or public_url.query
                or public_url.fragment
                or public_url.path not in {"", "/"}
                or public_url.scheme not in {"http", "https"}
                or (
                    public_url.scheme != "https"
                    and public_url.hostname not in local_hosts
                )
            ):
                raise RuntimeError(
                    "PUBLIC_BASE_URL must be an HTTPS origin (HTTP is allowed only for local development)"
                )
            if self.email_verification_required and not self.smtp_configured:
                raise RuntimeError(
                    "SMTP_HOST and SMTP_FROM are required when email verification is enabled"
                )
        if not 0 < self.max_upload_bytes <= 100 * 1024 * 1024:
            raise RuntimeError("MAX_UPLOAD_BYTES must be between 1 byte and 100 MiB")
        if not 0 < self.result_ttl_minutes <= 1440:
            raise RuntimeError("RESULT_TTL_MINUTES must be between 1 and 1440")
        if not 1 <= self.max_pending_jobs <= 100:
            raise RuntimeError("MAX_PENDING_JOBS must be between 1 and 100")
        if (
            self.max_output_bytes <= 0
            or self.max_stored_result_bytes <= 0
            or self.max_conversion_memory_bytes <= 0
            or self.max_archive_entries <= 0
            or self.max_archive_expanded_bytes <= 0
            or self.conversion_timeout_seconds <= 0
        ):
            raise RuntimeError(
                "Output, archive, memory and timeout limits must be positive"
            )
        if self.rate_limit_per_minute <= 0:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE must be positive")
        try:
            ZoneInfo(self.quota_timezone)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError("QUOTA_TIMEZONE must be a valid IANA time zone") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./markitdown_api.db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        storage_dir=Path(os.getenv("STORAGE_DIR", "./data")),
        jwt_secret=os.getenv("JWT_SECRET", ""),
        api_key_pepper=os.getenv("API_KEY_PEPPER", ""),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip(
            "/"
        ),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
        max_pending_jobs=int(os.getenv("MAX_PENDING_JOBS", "5")),
        max_output_bytes=int(os.getenv("MAX_OUTPUT_BYTES", str(50 * 1024 * 1024))),
        max_stored_result_bytes=int(
            os.getenv("MAX_STORED_RESULT_BYTES", str(2 * 1024 * 1024 * 1024))
        ),
        max_conversion_memory_bytes=int(
            os.getenv("MAX_CONVERSION_MEMORY_BYTES", str(512 * 1024 * 1024))
        ),
        max_archive_entries=int(os.getenv("MAX_ARCHIVE_ENTRIES", "5000")),
        max_archive_expanded_bytes=int(
            os.getenv("MAX_ARCHIVE_EXPANDED_BYTES", str(128 * 1024 * 1024))
        ),
        result_ttl_minutes=int(os.getenv("RESULT_TTL_MINUTES", "1440")),
        conversion_timeout_seconds=int(os.getenv("CONVERSION_TIMEOUT_SECONDS", "900")),
        rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")),
        quota_timezone=os.getenv("QUOTA_TIMEZONE", "America/Mazatlan"),
        public_signup_enabled=_bool("PUBLIC_SIGNUP_ENABLED", False),
        email_verification_required=_bool("EMAIL_VERIFICATION_REQUIRED", True),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", ""),
        smtp_starttls=_bool("SMTP_STARTTLS", True),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_openai_model=os.getenv("AZURE_OPENAI_MODEL", ""),
    )
