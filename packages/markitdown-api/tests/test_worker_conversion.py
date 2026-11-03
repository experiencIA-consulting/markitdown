from pathlib import Path

from markitdown_api.config import Settings
from markitdown_api.models import Conversion
from markitdown_api.worker import _run_conversion


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
        result_ttl_minutes=1440,
        conversion_timeout_seconds=60,
        rate_limit_per_minute=30,
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
    )

    assert result["ok"] is True
    assert result["chars"] > 0
    assert (tmp_path / "results" / f"{conversion_id}.md").read_text(
        encoding="utf-8"
    ) == "hello worker"
