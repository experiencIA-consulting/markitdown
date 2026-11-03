import zipfile
from pathlib import Path

import pytest

from markitdown_api.input_validation import InputValidationError, validate_input_file


def _validate(path: Path, extension: str | None, *, max_entries: int = 5000):
    return validate_input_file(
        path,
        extension,
        max_archive_entries=max_entries,
        max_archive_expanded_bytes=128 * 1024 * 1024,
    )


def test_accepts_text_and_infers_extension(tmp_path: Path):
    path = tmp_path / "input"
    path.write_text("A small document", encoding="utf-8")

    assert _validate(path, None) == ".txt"
    assert _validate(path, ".md") == ".md"


def test_rejects_clear_extension_content_mismatch(tmp_path: Path):
    path = tmp_path / "not-text.txt"
    path.write_bytes(b"%PDF-1.7\nnot really a text file")

    with pytest.raises(InputValidationError, match="file_type_mismatch"):
        _validate(path, ".txt")


def test_checks_pdf_signature_and_infers_pdf_without_extension(tmp_path: Path):
    path = tmp_path / "report"
    path.write_bytes(b"%PDF-1.7\nminimal pdf data")

    assert _validate(path, None) == ".pdf"
    with pytest.raises(InputValidationError, match="file_type_mismatch"):
        _validate(path, ".txt")


def test_rejects_unrecognized_extension(tmp_path: Path):
    path = tmp_path / "file.unsupported"
    path.write_text("content", encoding="utf-8")

    with pytest.raises(InputValidationError, match="unsupported_format"):
        _validate(path, ".unsupported")


def test_checks_zip_entry_count(tmp_path: Path):
    path = tmp_path / "many.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("one.txt", "1")
        archive.writestr("two.txt", "2")

    with pytest.raises(InputValidationError, match="archive_limits_exceeded"):
        _validate(path, ".zip", max_entries=1)


def test_checks_zip_expanded_size(tmp_path: Path):
    path = tmp_path / "expanded.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.txt", "x" * 1024)

    with pytest.raises(InputValidationError, match="archive_limits_exceeded"):
        validate_input_file(
            path,
            ".zip",
            max_archive_entries=5000,
            max_archive_expanded_bytes=100,
        )


def test_rejects_docx_extension_for_generic_zip(tmp_path: Path):
    path = tmp_path / "not-a-document.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "not a document package")

    with pytest.raises(InputValidationError, match="file_type_mismatch"):
        _validate(path, ".docx")
