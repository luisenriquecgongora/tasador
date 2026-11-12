from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from tasador_engine import local_readers


def _nameplate() -> Image.Image:
    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    draw.text((30, 40), "MODEL DJM-900NXS2", fill="black", font=font)
    draw.text((30, 140), "SERIAL AB1234567", fill="black", font=font)
    return image


@pytest.mark.skipif(not local_readers.OCR_AVAILABLE, reason="ocr extra not installed")
def test_ocr_reads_a_nameplate():
    text = " ".join(local_readers.read_text(_nameplate())).replace(" ", "")
    assert "DJM-900NXS2" in text
    assert "AB1234567" in text


def test_readers_return_nothing_on_a_blank_photo():
    blank = Image.new("RGB", (200, 200), "gray")
    assert local_readers.read_text(blank) == []
    assert local_readers.read_barcodes(blank, 0) == []
