from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from tasador_engine.images import (
    PDF_SUPPORTED,
    ImageError,
    check_url_allowed,
    fetch,
    is_pdf,
    prepare,
    prepare_many,
)
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


def _make_dummy_pdf(num_pages: int = 2) -> bytes:
    import io
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(num_pages):
        doc.new_page(200, 300)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def test_is_pdf_detection():
    assert is_pdf(b"%PDF-1.5 test")
    assert is_pdf(b"  \n%PDF-1.4...")
    assert not is_pdf(b"not a pdf")
    assert not is_pdf(jpeg_bytes())


@pytest.mark.skipif(not PDF_SUPPORTED, reason="pypdfium2 not installed")
def test_pdf_prepare_and_prepare_many(settings):
    pdf_bytes = _make_dummy_pdf(num_pages=3)
    assert is_pdf(pdf_bytes)

    # prepare() returns the first page
    first = prepare(pdf_bytes, settings, "invoice.pdf")
    assert first.jpeg.startswith(b"\xff\xd8")  # valid JPEG
    assert first.image.size[0] > 0 and first.image.size[1] > 0

    # prepare_many() returns all pages up to max_pages
    all_pages = prepare_many(pdf_bytes, settings, "invoice.pdf")
    assert len(all_pages) == 3
    for p in all_pages:
        assert p.jpeg.startswith(b"\xff\xd8")

    capped_pages = prepare_many(pdf_bytes, settings, "invoice.pdf", max_pages=2)
    assert len(capped_pages) == 2


@pytest.mark.skipif(not PDF_SUPPORTED, reason="pypdfium2 not installed")
def test_pdf_corrupted_raises_image_error(settings):
    bad_pdf = b"%PDF-corrupted-data-header-with-invalid-payload"
    with pytest.raises(ImageError, match="not a readable PDF"):
        prepare(bad_pdf, settings, "corrupt.pdf")
