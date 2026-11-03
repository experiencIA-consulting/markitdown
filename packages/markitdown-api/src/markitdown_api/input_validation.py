import zipfile
from pathlib import Path

from magika import Magika

from .storage import safe_extension

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".csv",
    ".html",
    ".htm",
    ".xhtml",
    ".xht",
    ".xml",
    ".rss",
    ".atom",
    ".ipynb",
    ".pdf",
    ".docx",
    ".xlsx",
    ".xls",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".msg",
    ".zip",
    ".epub",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".csv",
    ".html",
    ".htm",
    ".xhtml",
    ".xht",
    ".xml",
    ".rss",
    ".atom",
    ".ipynb",
}

ZIP_EXTENSIONS = {".zip", ".docx", ".xlsx", ".pptx", ".epub"}
EXTENSION_ALIASES = {
    ".jpg": {".jpeg"},
    ".jpeg": {".jpg"},
    ".html": {".htm"},
    ".htm": {".html"},
}


class InputValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _archive_kind(path: Path, max_entries: int, max_expanded_bytes: int) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                raise InputValidationError("archive_limits_exceeded")
            if sum(entry.file_size for entry in entries) > max_expanded_bytes:
                raise InputValidationError("archive_limits_exceeded")

            names = {entry.filename for entry in entries}
            if "word/document.xml" in names and "[Content_Types].xml" in names:
                return ".docx"
            if "xl/workbook.xml" in names and "[Content_Types].xml" in names:
                return ".xlsx"
            if "ppt/presentation.xml" in names and "[Content_Types].xml" in names:
                return ".pptx"
            mimetype = next(
                (entry for entry in entries if entry.filename == "mimetype"), None
            )
            if (
                mimetype is not None
                and mimetype.file_size <= 64
                and archive.read(mimetype) == b"application/epub+zip"
            ):
                return ".epub"
            return ".zip"
    except InputValidationError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        ValueError,
        NotImplementedError,
    ) as exc:
        raise InputValidationError("invalid_file_content") from exc


def validate_input_file(
    path: Path,
    extension: str | None,
    *,
    max_archive_entries: int,
    max_archive_expanded_bytes: int,
) -> str:
    """Validate the declared format against a bounded content classification."""
    extension = safe_extension(extension)
    if extension is not None and extension not in SUPPORTED_EXTENSIONS:
        raise InputValidationError("unsupported_format")

    if path.stat().st_size == 0:
        if extension in TEXT_EXTENSIONS:
            return extension
        raise InputValidationError("invalid_file_content")

    with path.open("rb") as source:
        header = source.read(1024)
    has_pdf_header = b"%PDF-" in header
    if has_pdf_header:
        if extension not in {None, ".pdf"}:
            raise InputValidationError("file_type_mismatch")
        return ".pdf"
    if extension == ".pdf":
        raise InputValidationError("file_type_mismatch")

    try:
        classifier = Magika()
        prediction = classifier.identify_path(path)
        del classifier
    except Exception as exc:
        raise InputValidationError("invalid_file_content") from exc

    if prediction.status != "ok":
        raise InputValidationError("invalid_file_content")

    output = prediction.prediction.output
    detected_extensions = {
        safe_extension(value if value.startswith(".") else f".{value}")
        for value in output.extensions
        if safe_extension(value if value.startswith(".") else f".{value}")
    }
    if output.label == "unknown":
        detected_extensions = set()

    if extension in ZIP_EXTENSIONS or ".zip" in detected_extensions:
        archive_kind = _archive_kind(
            path, max_archive_entries, max_archive_expanded_bytes
        )
        if extension is None:
            extension = archive_kind
        elif extension not in {".zip", archive_kind}:
            raise InputValidationError("file_type_mismatch")
        return extension

    if extension is None:
        candidates = detected_extensions & SUPPORTED_EXTENSIONS
        if not candidates:
            raise InputValidationError("unsupported_format")
        extension = min(candidates)

    if output.label == "unknown":
        raise InputValidationError("invalid_file_content")

    if extension not in detected_extensions and extension not in TEXT_EXTENSIONS:
        aliases = EXTENSION_ALIASES.get(extension, set())
        if not detected_extensions.intersection(aliases):
            raise InputValidationError("file_type_mismatch")

    if extension in TEXT_EXTENSIONS and not (output.is_text or output.group == "code"):
        raise InputValidationError("file_type_mismatch")

    return extension
