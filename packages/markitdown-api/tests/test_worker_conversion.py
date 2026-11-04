from pathlib import Path

from markitdown_api.config import Settings
from markitdown_api.models import Conversion
from markitdown_api.worker import _run_conversion, _write_limited_markdown


def test_conversion_runs_in_bounded_child_process(tmp_path: Path):
    conversion_id = "a" * 32
    input_file = tmp_path / "input.txt"
    input_file.write_text("hello worker", encoding="utf-8")
    settings = Settings(
        database_url="sqlite://",
        redis_url="redis://localhost",
        storage_dir=tmp_path,
        jwt_secret="j" * 32,
        api_key_pepper="p" * 32,
        public_base_url="http://localhost",
        max_upload_bytes=1024,
        max_pending_jobs=5,
        max_output_bytes=1024,
        max_stored_result_bytes=1024,
        max_conversion_memory_bytes=512 * 1024 * 1024,
        max_archive_entries=5000,
        max_archive_expanded_bytes=128 * 1024 * 1024,
        result_ttl_minutes=1440,
        conversion_timeout_seconds=60,
        rate_limit_per_minute=30,
        quota_timezone="America/Mazatlan",
        public_signup_enabled=False,
        email_verification_required=False,
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from="",
        smtp_starttls=True,
        azure_openai_api_key="",
        azure_openai_endpoint="",
        azure_openai_api_version="2024-02-01",
        azure_openai_model="",
    )
    (tmp_path / "results").mkdir()

    result = _run_conversion(
        settings,
        Conversion(id=conversion_id),
        input_file,
        ".txt",
        False,
        None,
    )

    assert result["ok"] is True
    assert result["chars"] > 0
    assert (tmp_path / "results" / f"{conversion_id}.md").read_text(
        encoding="utf-8"
    ) == "hello worker"


def test_conversion_memory_budget_terminates_child(tmp_path: Path):
    input_file = tmp_path / "input.txt"
    input_file.write_text("hello worker", encoding="utf-8")
    settings = _settings(tmp_path, max_conversion_memory_bytes=1)
    (tmp_path / "results").mkdir()

    result = _run_conversion(
        settings,
        Conversion(id="b" * 32),
        input_file,
        ".txt",
        False,
        None,
    )

    assert result == {
        "ok": False,
        "error": "resource_limit_exceeded",
        "ocr_units": 0,
    }


def test_limited_writer_counts_utf8_and_discards_oversized_result(tmp_path: Path):
    result = tmp_path / "result.md"
    text = "á🙂" * 20

    byte_count, too_large = _write_limited_markdown(text, result, 10)

    assert byte_count == len(text.encode("utf-8"))
    assert too_large
    assert not result.exists()
    assert not (tmp_path / ".result.part").exists()


def _settings(tmp_path: Path, *, max_conversion_memory_bytes: int) -> Settings:
    return Settings(
        database_url="sqlite://",
        redis_url="redis://localhost",
        storage_dir=tmp_path,
        jwt_secret="j" * 32,
        api_key_pepper="p" * 32,
        public_base_url="http://localhost",
        max_upload_bytes=1024,
        max_pending_jobs=5,
        max_output_bytes=1024,
        max_stored_result_bytes=1024,
        max_conversion_memory_bytes=max_conversion_memory_bytes,
        max_archive_entries=5000,
        max_archive_expanded_bytes=128 * 1024 * 1024,
        result_ttl_minutes=1440,
        conversion_timeout_seconds=60,
        rate_limit_per_minute=30,
        quota_timezone="America/Mazatlan",
        public_signup_enabled=False,
        email_verification_required=False,
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from="",
        smtp_starttls=True,
        azure_openai_api_key="",
        azure_openai_endpoint="",
        azure_openai_api_version="2024-02-01",
        azure_openai_model="",
    )
