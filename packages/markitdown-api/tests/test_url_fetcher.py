import asyncio
import socket

import pytest

from markitdown_api.url_fetcher import PublicResolver, UnsafeURL, validate_url_syntax


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.5/",
        "http://[::1]/",
        "file:///etc/passwd",
        "https://user:password@example.com/file.pdf",
        "https://example.com:8443/file.pdf",
        "https://example.com/#fragment",
    ],
)
def test_rejects_non_public_or_unsupported_urls(url):
    with pytest.raises(UnsafeURL):
        validate_url_syntax(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/report.pdf",
        "http://example.com:80/document.docx",
        "https://8.8.8.8/public.pdf",
    ],
)
def test_accepts_public_http_urls(url):
    validate_url_syntax(url)


def test_dns_resolution_rejects_private_addresses(monkeypatch):
    class PrivateAddressLoop:
        async def getaddrinfo(self, host, port, family, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: PrivateAddressLoop())
    with pytest.raises(OSError, match="destination_not_public"):
        asyncio.run(
            PublicResolver().resolve("public-looking.example", 443, socket.AF_UNSPEC)
        )
