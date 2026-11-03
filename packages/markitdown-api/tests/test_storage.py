from pathlib import Path

import pytest

from markitdown_api.storage import safe_extension, save_upload


def test_safe_extension_extracts_only_suffix():
    assert safe_extension("quarterly-report.PDF") == ".pdf"
    assert safe_extension(".docx") == ".docx"
    assert safe_extension("../../secret") is None
    assert safe_extension("report.bad extension") is None


def test_upload_is_streamed_and_limited(tmp_path: Path):
    import io

    destination = tmp_path / "inputs" / "safe.input"
    assert save_upload(io.BytesIO(b"small"), destination, 5) == 5
    assert destination.read_bytes() == b"small"
    with pytest.raises(ValueError, match="file_too_large"):
        save_upload(io.BytesIO(b"too large"), tmp_path / "inputs" / "large.input", 4)
    assert not (tmp_path / "inputs" / "large.input").exists()
