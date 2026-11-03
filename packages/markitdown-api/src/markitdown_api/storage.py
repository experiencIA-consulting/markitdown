import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from .config import Settings


def ensure_storage(settings: Settings) -> None:
    (settings.storage_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "results").mkdir(parents=True, exist_ok=True)


def input_path(settings: Settings, conversion_id: str) -> Path:
    return settings.storage_dir / "inputs" / f"{conversion_id}.input"


def result_path(settings: Settings, conversion_id: str) -> Path:
    return settings.storage_dir / "results" / f"{conversion_id}.md"


def safe_extension(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith(".") and "/" not in value and "\\" not in value:
        suffix = value.lower()
    else:
        suffix = Path(value).suffix.lower()
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    if len(suffix) > 20 or not re.fullmatch(r"\.[a-z0-9]{1,16}", suffix):
        return None
    return suffix


def save_upload(source, destination: Path, max_bytes: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{uuid4().hex}.part")
    total = 0
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("file_too_large")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        return total
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def remove_input(settings: Settings, conversion_id: str) -> None:
    input_path(settings, conversion_id).unlink(missing_ok=True)


def remove_result(settings: Settings, conversion_id: str) -> None:
    result_path(settings, conversion_id).unlink(missing_ok=True)


def write_result(
    settings: Settings, conversion_id: str, markdown: str, max_bytes: int
) -> tuple[int, int]:
    encoded_size = len(markdown.encode("utf-8"))
    if encoded_size > max_bytes:
        raise ValueError("result_too_large")
    destination = result_path(settings, conversion_id)
    fd, temporary_name = tempfile.mkstemp(prefix=".result-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(markdown)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return len(markdown), encoded_size


def remove_stale_parts(settings: Settings, older_than_seconds: int = 3600) -> None:
    import time

    cutoff = time.time() - older_than_seconds
    for folder in (settings.storage_dir / "inputs", settings.storage_dir / "results"):
        for path in folder.glob(".*.part"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
