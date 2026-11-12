"""Decode, check and normalise incoming photos.

Every photo is re-encoded to a JPEG no larger than `max_image_side`: that rotates it
upright (EXIF orientation), drops all metadata (GPS position, camera serial) and keeps
the request to the vision model small.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings

Image.MAX_IMAGE_PIXELS = 60_000_000  # ~60 MP; larger raises DecompressionBombError

try:  # optional extra: iPhone photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on installed extras
    HEIC_SUPPORTED = False


class ImageError(ValueError):
    """A photo could not be used; the message is safe to show to the caller."""


@dataclass(frozen=True)
class PreparedImage:
    jpeg: bytes
    image: Image.Image  # RGB, already resized; for local stages (OCR, barcodes)


def prepare(data: bytes, settings: Settings, label: str) -> PreparedImage:
    if not data:
        raise ImageError(f"{label}: empty file")
    if len(data) > settings.max_image_bytes:
        raise ImageError(f"{label}: larger than {settings.max_image_bytes // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.load()
            img = ImageOps.exif_transpose(raw).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ImageError(f"{label}: not a readable image ({type(exc).__name__})") from exc

    img.thumbnail((settings.max_image_side, settings.max_image_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return PreparedImage(jpeg=out.getvalue(), image=img)


def check_url_allowed(url: str, settings: Settings) -> None:
    if not settings.allowed_image_hosts:
        raise ImageError("image URLs are disabled on this server; upload the files instead")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ImageError(f"{url}: only https URLs are accepted")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in settings.allowed_image_hosts):
        raise ImageError(f"{host}: not an allowed image host")


async def fetch(url: str, settings: Settings, client: httpx.AsyncClient) -> bytes:
    """Download one image URL. Redirects are not followed: they could leave the allowlist."""
    check_url_allowed(url, settings)
    try:
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ImageError(f"{url}: HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > settings.max_image_bytes:
                    raise ImageError(f"{url}: larger than {settings.max_image_bytes // (1024 * 1024)} MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise ImageError(f"{url}: download failed ({type(exc).__name__})") from exc
