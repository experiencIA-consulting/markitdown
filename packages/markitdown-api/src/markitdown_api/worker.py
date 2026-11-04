import asyncio
import codecs
import json
import logging
import multiprocessing
import os
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

import psutil
from markitdown import MarkItDown, StreamInfo, UnsupportedFormatException
from redis import Redis
from sqlalchemy import delete, func, or_, select, update

from .config import Settings, get_settings
from .db import SessionLocal, initialize_database
from .entitlements import get_quota_snapshot, quota_exceeded
from .input_validation import InputValidationError, validate_input_file
from .job_queue import HEARTBEAT_KEY, PROCESSING_NAME, QUEUE_NAME
from .models import Account, AccountToken, Conversion, utcnow
from .storage import (
    ensure_storage,
    input_path,
    remove_input,
    remove_result,
    remove_stale_parts,
    result_path,
)
from .url_fetcher import DownloadTooLarge, UnsafeURL, download_public_url

logger = logging.getLogger("markitdown_api.worker")


class _CountingCompletions:
    def __init__(self, completions, counter, limit_reached, limit: int | None):
        self._completions = completions
        self._counter = counter
        self._limit_reached = limit_reached
        self._limit = limit

    def create(self, *args, **kwargs):
        with self._counter.get_lock():
            if self._limit is not None and self._counter.value >= self._limit:
                self._limit_reached.value = True
                raise RuntimeError("ocr_quota_exceeded")
            self._counter.value += 1
        return self._completions.create(*args, **kwargs)


class _CountingClient:
    def __init__(self, client, counter, limit_reached, limit: int | None):
        self._client = client
        self.chat = type(
            "CountingChat",
            (),
            {
                "completions": _CountingCompletions(
                    client.chat.completions, counter, limit_reached, limit
                )
            },
        )()

    def __getattr__(self, name):
        return getattr(self._client, name)


def _heartbeat(redis: Redis, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            redis.set(HEARTBEAT_KEY, "ok", ex=30)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Worker heartbeat failed (%s)", type(exc).__name__)
        stop.wait(10)


def _convert_in_child(
    send_pipe,
    file_path: str,
    extension: str | None,
    result_file: str,
    max_output_bytes: int,
    max_archive_entries: int,
    max_archive_expanded_bytes: int,
    ocr: bool,
    max_ocr_units: int | None,
    ocr_counter,
    ocr_limit_reached,
    settings_values: dict,
) -> None:
    try:
        if os.name != "nt":
            os.setsid()
        extension = validate_input_file(
            Path(file_path),
            extension,
            max_archive_entries=max_archive_entries,
            max_archive_expanded_bytes=max_archive_expanded_bytes,
        )
        client = None
        if ocr:
            from openai import AzureOpenAI

            client = AzureOpenAI(
                api_key=settings_values["azure_openai_api_key"],
                azure_endpoint=settings_values["azure_openai_endpoint"],
                api_version=settings_values["azure_openai_api_version"],
            )
            client = _CountingClient(
                client, ocr_counter, ocr_limit_reached, max_ocr_units
            )
        converter = MarkItDown(
            enable_plugins=ocr,
            llm_client=client,
            llm_model=settings_values["azure_openai_model"] if ocr else None,
        )
        stream_info = StreamInfo(extension=extension, filename=Path(file_path).name)
        markdown = None
        if extension == ".pdf" and not ocr:
            try:
                import pymupdf4llm

                markdown = pymupdf4llm.to_markdown(file_path)
            except Exception:  # noqa: BLE001
                markdown = None
        if not markdown or not markdown.strip():
            result = converter.convert_local(file_path, stream_info=stream_info)
            markdown = result.markdown
        output_chars = len(markdown)
        page_count = None
        if extension == ".pdf":
            try:
                import pdfplumber

                with pdfplumber.open(file_path) as pdf:
                    page_count = len(pdf.pages)
            except Exception:  # noqa: BLE001
                page_count = None
        if ocr_limit_reached.value:
            send_pipe.send(
                {
                    "ok": False,
                    "error": "monthly_ocr_quota_exceeded",
                    "pages": page_count,
                    "ocr_units": ocr_counter.value,
                }
            )
            return
        output_bytes, result_too_large = _write_limited_markdown(
            markdown, Path(result_file), max_output_bytes
        )
        if result_too_large:
            send_pipe.send(
                {
                    "ok": False,
                    "error": "result_too_large",
                    "chars": output_chars,
                    "bytes": output_bytes,
                    "pages": page_count,
                    "extension": extension,
                    "ocr_units": ocr_counter.value,
                }
            )
            return
        send_pipe.send(
            {
                "ok": True,
                "chars": output_chars,
                "bytes": output_bytes,
                "pages": page_count,
                "extension": extension,
                "ocr_units": ocr_counter.value,
            }
        )
    except InputValidationError as exc:
        send_pipe.send({"ok": False, "error": exc.code, "ocr_units": ocr_counter.value})
    except UnsupportedFormatException:
        send_pipe.send(
            {"ok": False, "error": "unsupported_format", "ocr_units": ocr_counter.value}
        )
    except Exception as exc:  # noqa: BLE001
        # Error text may contain document names, paths, URLs, or extracted data.
        logger.warning("Conversion subprocess failed (%s)", type(exc).__name__)
        send_pipe.send(
            {"ok": False, "error": "conversion_failed", "ocr_units": ocr_counter.value}
        )
    finally:
        send_pipe.close()


def _write_limited_markdown(
    markdown: str, destination: Path, max_output_bytes: int
) -> tuple[int, bool]:
    """Write UTF-8 in bounded chunks without allocating a second full-size copy."""
    temporary = destination.with_name(f".{destination.stem}.part")
    total_bytes = 0
    too_large = False
    encoder = codecs.getincrementalencoder("utf-8")()
    try:
        with temporary.open("xb") as output:
            for start in range(0, len(markdown), 64 * 1024):
                encoded = encoder.encode(markdown[start : start + 64 * 1024])
                total_bytes += len(encoded)
                if not too_large and total_bytes <= max_output_bytes:
                    output.write(encoded)
                else:
                    too_large = True
            final_bytes = encoder.encode("", final=True)
            total_bytes += len(final_bytes)
            if not too_large and total_bytes <= max_output_bytes:
                output.write(final_bytes)
            else:
                too_large = True
            if too_large:
                return total_bytes, True
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
        return total_bytes, False
    finally:
        temporary.unlink(missing_ok=True)


def _run_conversion(
    settings: Settings,
    conversion: Conversion,
    file_path: Path,
    extension: str | None,
    ocr: bool,
    max_ocr_units: int | None,
) -> dict:
    context = multiprocessing.get_context("spawn")
    receive_pipe, send_pipe = context.Pipe(duplex=False)
    ocr_counter = context.Value("i", 0)
    ocr_limit_reached = context.Value("b", False)
    values = {
        "azure_openai_api_key": settings.azure_openai_api_key,
        "azure_openai_endpoint": settings.azure_openai_endpoint,
        "azure_openai_api_version": settings.azure_openai_api_version,
        "azure_openai_model": settings.azure_openai_model,
    }
    process = context.Process(
        target=_convert_in_child,
        args=(
            send_pipe,
            str(file_path),
            extension,
            str(result_path(settings, conversion.id)),
            settings.max_output_bytes,
            settings.max_archive_entries,
            settings.max_archive_expanded_bytes,
            ocr,
            max_ocr_units,
            ocr_counter,
            ocr_limit_reached,
            values,
        ),
        daemon=True,
    )
    temporary_result = result_path(settings, conversion.id).with_name(
        f".{conversion.id}.part"
    )
    temporary_result.unlink(missing_ok=True)
    result_path(settings, conversion.id).unlink(missing_ok=True)
    process.start()
    send_pipe.close()
    deadline = time.monotonic() + settings.conversion_timeout_seconds
    error = None
    while process.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = "conversion_timeout"
            break
        process.join(min(0.25, remaining))
        if process.is_alive():
            try:
                resident_bytes = psutil.Process(process.pid).memory_info().rss
            except psutil.NoSuchProcess:
                continue
            if resident_bytes > settings.max_conversion_memory_bytes:
                error = "resource_limit_exceeded"
                break
    if error:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        process.join(5)
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.is_alive():
            process.kill()
        if process.is_alive():
            process.join()
        receive_pipe.close()
        temporary_result.unlink(missing_ok=True)
        result_path(settings, conversion.id).unlink(missing_ok=True)
        return {"ok": False, "error": error, "ocr_units": int(ocr_counter.value)}
    if os.name != "nt" and process.exitcode == -getattr(signal, "SIGKILL", 9):
        temporary_result.unlink(missing_ok=True)
        result_path(settings, conversion.id).unlink(missing_ok=True)
        receive_pipe.close()
        return {
            "ok": False,
            "error": "resource_limit_exceeded",
            "ocr_units": int(ocr_counter.value),
        }
    try:
        if receive_pipe.poll(1):
            result = receive_pipe.recv()
        else:
            result = {"ok": False, "error": "conversion_failed"}
        result.setdefault("ocr_units", int(ocr_counter.value))
        if not result.get("ok"):
            temporary_result.unlink(missing_ok=True)
            result_path(settings, conversion.id).unlink(missing_ok=True)
        return result
    except EOFError:
        temporary_result.unlink(missing_ok=True)
        result_path(settings, conversion.id).unlink(missing_ok=True)
        error = (
            "resource_limit_exceeded"
            if os.name != "nt" and process.exitcode == -getattr(signal, "SIGKILL", 9)
            else "conversion_failed"
        )
        return {"ok": False, "error": error, "ocr_units": int(ocr_counter.value)}
    finally:
        receive_pipe.close()


def _update_conversion(conversion_id: str, **values) -> None:
    with SessionLocal() as db:
        conversion = db.get(Conversion, conversion_id)
        if conversion is None:
            return
        for key, value in values.items():
            setattr(conversion, key, value)
        db.commit()


async def _process_job(settings: Settings, job: dict) -> None:
    conversion_id = job.get("conversion_id")
    if not isinstance(conversion_id, str) or len(conversion_id) != 32:
        return

    with SessionLocal() as db:
        conversion = db.get(Conversion, conversion_id)
        if conversion is None or conversion.status not in {"queued", "running"}:
            return
        account_id = conversion.account_id
        account = db.get(Account, account_id)
        account_role = account.role if account else "user"
        conversion.status = "running"
        conversion.started_at = conversion.started_at or utcnow()
        db.commit()

    started = time.monotonic()
    file_path = input_path(settings, conversion_id)
    extension = None
    ocr_requested = bool(job.get("ocr"))
    ocr_units = 0
    try:
        snapshot = _quota_snapshot(account_id, settings)
        if account_role != "admin" and not snapshot.plan_configured:
            _fail(settings, conversion_id, "plan_not_configured", 0)
            return
        if quota_exceeded(snapshot, "max_conversions_month", additional=1):
            _fail(settings, conversion_id, "monthly_conversion_quota_exceeded", 0)
            return
        if quota_exceeded(snapshot, "max_pages_month", additional=1):
            _fail(settings, conversion_id, "monthly_page_quota_exceeded", 0)
            return
        byte_limit = snapshot.limits["max_input_bytes_month"]
        if byte_limit is not None:
            if snapshot.usage["input_bytes"] > byte_limit:
                _fail(settings, conversion_id, "monthly_input_bytes_quota_exceeded", 0)
                return
            remaining_bytes = max(0, int(byte_limit) - snapshot.usage["input_bytes"])
        else:
            remaining_bytes = settings.max_upload_bytes
        file_limit = int(snapshot.limits["max_file_bytes"] or settings.max_upload_bytes)
        if ocr_requested and not snapshot.limits["ocr_enabled"]:
            _fail(settings, conversion_id, "ocr_not_in_plan", 0)
            return
        ocr_limit = snapshot.limits["max_ocr_units_month"]
        max_ocr_units = None
        if ocr_requested and ocr_limit is not None:
            max_ocr_units = max(0, int(ocr_limit) - snapshot.usage["ocr_units"])
            if max_ocr_units == 0:
                _fail(settings, conversion_id, "monthly_ocr_quota_exceeded", 0)
                return

        source_type = job.get("source_type")
        if source_type == "url":
            url = job.get("url")
            if not isinstance(url, str):
                raise ValueError("invalid_url")
            if remaining_bytes <= 0:
                _fail(settings, conversion_id, "monthly_input_bytes_quota_exceeded", 0)
                return
            download_limit = min(
                int(job.get("max_input_bytes") or file_limit),
                file_limit,
                remaining_bytes,
                settings.max_upload_bytes,
            )
            limit_error = str(job.get("input_limit_error") or "file_too_large")
            _downloaded_path, extension, input_bytes = await download_public_url(
                settings,
                conversion_id,
                url,
                max_bytes=download_limit,
                limit_error_code=limit_error,
            )
            _update_conversion(
                conversion_id, input_extension=extension, input_bytes=input_bytes
            )
            if (
                byte_limit is not None
                and snapshot.usage["input_bytes"] + input_bytes > byte_limit
            ):
                _fail(settings, conversion_id, "monthly_input_bytes_quota_exceeded", 0)
                return
        elif source_type == "upload":
            with SessionLocal() as db:
                conversion = db.get(Conversion, conversion_id)
                extension = conversion.input_extension if conversion else None
                input_bytes = conversion.input_bytes if conversion else 0
            if input_bytes and input_bytes > file_limit:
                _fail(settings, conversion_id, "file_too_large", 0)
                return
            if byte_limit is not None and snapshot.usage["input_bytes"] > byte_limit:
                _fail(settings, conversion_id, "monthly_input_bytes_quota_exceeded", 0)
                return
        else:
            raise ValueError("invalid_source")

        if not file_path.is_file():
            raise ValueError("input_missing")
        result = await asyncio.to_thread(
            _run_conversion,
            settings,
            _get_conversion(conversion_id),
            file_path,
            extension,
            ocr_requested,
            max_ocr_units,
        )
        ocr_units = int(result.get("ocr_units", 0))
        duration_ms = int((time.monotonic() - started) * 1000)
        if not result.get("ok"):
            remove_result(settings, conversion_id)
            _update_conversion(
                conversion_id,
                status="failed",
                error_code=result.get("error", "conversion_failed"),
                page_count=result.get("pages"),
                quota_page_units=0,
                ocr_units=ocr_units,
                output_char_count=result.get("chars"),
                output_utf8_bytes=result.get("bytes"),
                duration_ms=duration_ms,
                completed_at=utcnow(),
            )
            logger.info(
                "conversion_completed id=%s status=failed error_code=%s duration_ms=%d",
                conversion_id,
                result.get("error", "conversion_failed"),
                duration_ms,
            )
            return

        if result.get("extension"):
            _update_conversion(conversion_id, input_extension=result["extension"])

        quota_page_units = max(1, int(result.get("pages") or 1))
        completed_snapshot = _quota_snapshot(account_id, settings)
        if quota_exceeded(
            completed_snapshot, "max_pages_month", additional=quota_page_units
        ):
            remove_result(settings, conversion_id)
            _update_conversion(
                conversion_id,
                status="failed",
                error_code="monthly_page_quota_exceeded",
                page_count=result.get("pages"),
                quota_page_units=0,
                ocr_units=ocr_units,
                output_char_count=result["chars"],
                output_utf8_bytes=result["bytes"],
                duration_ms=duration_ms,
                completed_at=utcnow(),
            )
            return

        with SessionLocal() as db:
            stored_bytes = db.scalar(
                select(func.coalesce(func.sum(Conversion.output_utf8_bytes), 0)).where(
                    Conversion.status == "succeeded",
                    Conversion.downloaded_at.is_(None),
                    Conversion.result_expires_at > utcnow(),
                )
            )
        if stored_bytes + result["bytes"] > settings.max_stored_result_bytes:
            remove_result(settings, conversion_id)
            _update_conversion(
                conversion_id,
                status="failed",
                error_code="temporary_storage_limit",
                page_count=result.get("pages"),
                quota_page_units=0,
                ocr_units=ocr_units,
                output_char_count=result["chars"],
                output_utf8_bytes=result["bytes"],
                duration_ms=int((time.monotonic() - started) * 1000),
                completed_at=utcnow(),
            )
            logger.info(
                "conversion_completed id=%s status=failed error_code=temporary_storage_limit",
                conversion_id,
            )
            return

        _update_conversion(
            conversion_id,
            status="succeeded",
            page_count=result.get("pages"),
            quota_page_units=quota_page_units,
            ocr_units=ocr_units,
            output_char_count=result["chars"],
            output_utf8_bytes=result["bytes"],
            duration_ms=duration_ms,
            completed_at=utcnow(),
            result_expires_at=utcnow() + timedelta(minutes=settings.result_ttl_minutes),
            error_code=None,
        )
        logger.info(
            "conversion_completed id=%s status=succeeded duration_ms=%d",
            conversion_id,
            duration_ms,
        )
    except UnsafeURL as exc:
        _fail(
            settings,
            conversion_id,
            str(exc),
            int((time.monotonic() - started) * 1000),
            ocr_units,
        )
    except DownloadTooLarge as exc:
        if exc.bytes_read:
            _update_conversion(conversion_id, input_bytes=exc.bytes_read)
        _fail(
            settings,
            conversion_id,
            exc.error_code,
            int((time.monotonic() - started) * 1000),
            ocr_units,
        )
    except ValueError as exc:
        code = (
            str(exc)
            if str(exc)
            in {
                "remote_http_error",
                "remote_content_encoding_not_supported",
                "remote_fetch_failed",
                "invalid_url",
                "input_missing",
                "invalid_source",
                "destination_not_public",
                "port_not_allowed",
                "too_many_redirects",
                "https_downgrade",
            }
            else "conversion_failed"
        )
        _fail(
            settings,
            conversion_id,
            code,
            int((time.monotonic() - started) * 1000),
            ocr_units,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Job %s failed (%s)", conversion_id, type(exc).__name__)
        _fail(
            settings,
            conversion_id,
            "conversion_failed",
            int((time.monotonic() - started) * 1000),
            ocr_units,
        )
    finally:
        remove_input(settings, conversion_id)


def _get_conversion(conversion_id: str) -> Conversion:
    with SessionLocal() as db:
        conversion = db.get(Conversion, conversion_id)
        if conversion is None:
            raise ValueError("conversion_not_found")
        # Return a detached copy with only the fields needed by the subprocess wrapper.
        return conversion


def _quota_snapshot(account_id: str, settings: Settings):
    with SessionLocal() as db:
        return get_quota_snapshot(db, account_id, settings)


def _fail(
    settings: Settings,
    conversion_id: str,
    error_code: str,
    duration_ms: int,
    ocr_units: int = 0,
) -> None:
    remove_result(settings, conversion_id)
    _update_conversion(
        conversion_id,
        status="failed",
        error_code=error_code,
        quota_page_units=0,
        ocr_units=ocr_units,
        duration_ms=duration_ms,
        completed_at=utcnow(),
    )
    logger.info(
        "conversion_completed id=%s status=failed error_code=%s duration_ms=%d",
        conversion_id,
        error_code,
        duration_ms,
    )


def _expire_results(settings: Settings) -> None:
    now = utcnow()
    with SessionLocal() as db:
        db.execute(
            update(Conversion)
            .where(
                Conversion.status == "succeeded",
                Conversion.downloaded_at.is_(None),
                Conversion.download_started_at < now - timedelta(hours=1),
            )
            .values(download_started_at=None)
        )
        expired = db.scalars(
            select(Conversion).where(
                Conversion.status == "succeeded",
                Conversion.result_expires_at <= now,
                Conversion.downloaded_at.is_(None),
            )
        ).all()
        for conversion in expired:
            remove_result(settings, conversion.id)
            conversion.status = "expired"
            conversion.error_code = "result_expired"
        db.execute(
            delete(AccountToken).where(
                or_(AccountToken.expires_at <= now, AccountToken.used_at.is_not(None))
            )
        )
        db.commit()
    remove_stale_parts(settings)


def run_worker() -> None:
    settings = get_settings()
    settings.validate_secrets(require_auth=False)
    ensure_storage(settings)
    initialize_database()
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    # A single initial worker is configured. Recover jobs interrupted by a worker restart.
    while redis.llen(PROCESSING_NAME):
        redis.rpoplpush(PROCESSING_NAME, QUEUE_NAME)
    queued_ids = set()
    for raw in redis.lrange(QUEUE_NAME, 0, -1):
        try:
            queued_ids.add(json.loads(raw).get("conversion_id"))
        except (TypeError, json.JSONDecodeError):
            continue
    with SessionLocal() as db:
        interrupted = db.scalars(
            select(Conversion).where(Conversion.status.in_(["queued", "running"]))
        ).all()
        for conversion in interrupted:
            if conversion.id not in queued_ids:
                conversion.status = "failed"
                conversion.error_code = "worker_interrupted"
                conversion.completed_at = utcnow()
                remove_input(settings, conversion.id)
                remove_result(settings, conversion.id)
        db.commit()
    logger.info("MarkItDown worker started")
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat, args=(redis, heartbeat_stop), daemon=True
    )
    heartbeat_thread.start()
    last_cleanup = 0.0
    try:
        while True:
            raw_job = redis.brpoplpush(QUEUE_NAME, PROCESSING_NAME, timeout=5)
            if raw_job is None:
                if time.monotonic() - last_cleanup > 60:
                    _expire_results(settings)
                    last_cleanup = time.monotonic()
                continue
            try:
                job = json.loads(raw_job)
                asyncio.run(_process_job(settings, job))
            except Exception as exc:  # noqa: BLE001 - keep consuming later queue items.
                logger.warning("Could not process queue item (%s)", type(exc).__name__)
            finally:
                redis.lrem(PROCESSING_NAME, 1, raw_job)
            if time.monotonic() - last_cleanup > 60:
                _expire_results(settings)
                last_cleanup = time.monotonic()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        redis.delete(HEARTBEAT_KEY)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run_worker()
