from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from tasador_engine.images import ImageError, check_url_allowed, fetch
from conftest import jpeg_bytes


def test_url_allowlist(settings):
    allowed = replace(settings, allowed_image_hosts=["r2.cloudflarestorage.com"])
    check_url_allowed("https://bucket.r2.cloudflarestorage.com/a.jpg", allowed)
    check_url_allowed("https://r2.cloudflarestorage.com/a.jpg", allowed)
    for bad in [
        "http://bucket.r2.cloudflarestorage.com/a.jpg",       # not https
        "https://evilr2.cloudflarestorage.com.attacker.io/a",  # suffix trick
        "https://169.254.169.254/latest/meta-data",            # metadata endpoint
        "https:///nohost",
    ]:
        with pytest.raises(ImageError):
            check_url_allowed(bad, allowed)


async def test_fetch_does_not_follow_redirects_and_caps_size(settings):
    allowed = replace(settings, allowed_image_hosts=["img.example.com"], max_image_bytes=1000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000)
        return httpx.Response(200, content=jpeg_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch("https://img.example.com/ok.jpg", replace(allowed, max_image_bytes=10**6), client)
        with pytest.raises(ImageError, match="HTTP 302"):
            await fetch("https://img.example.com/redirect", allowed, client)
        with pytest.raises(ImageError, match="larger than"):
            await fetch("https://img.example.com/big", allowed, client)
