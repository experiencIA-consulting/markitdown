import asyncio
import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from .config import Settings
from .storage import input_path, safe_extension


class UnsafeURL(ValueError):
    pass


class DownloadTooLarge(ValueError):
    def __init__(self, error_code: str = "file_too_large", bytes_read: int = 0):
        super().__init__(error_code)
        self.error_code = error_code
        self.bytes_read = bytes_read


def validate_url_syntax(url: str) -> None:
    if len(url) > 4096 or any(ord(char) < 32 for char in url):
        raise UnsafeURL("invalid_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("invalid_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURL("invalid_url")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise UnsafeURL("invalid_url")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise UnsafeURL("port_not_allowed")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise UnsafeURL("destination_not_public")


class PublicResolver(AbstractResolver):
    """Resolve only globally routable IPs and pin the validated results to aiohttp."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        try:
            literal = ipaddress.ip_address(host)
            literal_family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
            addresses = [(literal.compressed, literal_family, 0, 0)]
        except ValueError:
            loop = asyncio.get_running_loop()
            try:
                records = await loop.getaddrinfo(
                    host, port, family=family, type=socket.SOCK_STREAM
                )
            except OSError as exc:
                raise OSError("host_resolution_failed") from exc
            addresses = []
            for record_family, _kind, proto, _canonname, sockaddr in records:
                address = ipaddress.ip_address(sockaddr[0])
                addresses.append((address.compressed, record_family, proto, 0))

        if not addresses or any(
            not ipaddress.ip_address(item[0]).is_global for item in addresses
        ):
            raise OSError("destination_not_public")

        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": record_family,
                "proto": proto,
                "flags": flags,
            }
            for address, record_family, proto, flags in dict.fromkeys(addresses)
        ]

    async def close(self):
        return None


async def download_public_url(
    settings: Settings,
    conversion_id: str,
    url: str,
    *,
    max_bytes: int | None = None,
    limit_error_code: str = "file_too_large",
) -> tuple[Path, str | None, int]:
    byte_limit = min(settings.max_upload_bytes, max_bytes or settings.max_upload_bytes)
    validate_url_syntax(url)
    destination = input_path(settings, conversion_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=15)
    connector = aiohttp.TCPConnector(
        resolver=PublicResolver(),
        use_dns_cache=False,
        limit=4,
        enable_cleanup_closed=True,
    )
    current_url = url
    temporary_name = None
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            auto_decompress=False,
            headers={"Accept-Encoding": "identity", "User-Agent": "MarkItDown-API/0.1"},
        ) as session:
            for redirect_count in range(6):
                validate_url_syntax(current_url)
                async with session.get(current_url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirect_count == 5:
                            raise UnsafeURL("too_many_redirects")
                        location = response.headers.get("Location")
                        if not location:
                            raise UnsafeURL("invalid_redirect")
                        next_url = urljoin(current_url, location)
                        if (
                            urlsplit(current_url).scheme == "https"
                            and urlsplit(next_url).scheme != "https"
                        ):
                            raise UnsafeURL("https_downgrade")
                        current_url = next_url
                        continue
                    if response.status < 200 or response.status >= 300:
                        raise ValueError("remote_http_error")
                    content_encoding = response.headers.get(
                        "Content-Encoding", "identity"
                    ).lower()
                    if content_encoding not in {"", "identity"}:
                        raise ValueError("remote_content_encoding_not_supported")
                    content_length = response.content_length
                    if content_length is not None and content_length > byte_limit:
                        raise DownloadTooLarge(limit_error_code)

                    fd, temporary_name = tempfile.mkstemp(
                        prefix=f".{conversion_id}.",
                        suffix=".part",
                        dir=destination.parent,
                    )
                    size = 0
                    with os.fdopen(fd, "wb") as output:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            size += len(chunk)
                            if size > byte_limit:
                                raise DownloadTooLarge(limit_error_code, size)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary_name, destination)
                    temporary_name = None
                    extension = safe_extension(Path(urlsplit(current_url).path).suffix)
                    return destination, extension, size
            raise UnsafeURL("too_many_redirects")
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ValueError("remote_fetch_failed") from exc
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
