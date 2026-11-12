"""Local, model-free readers: barcodes and printed text (nameplates, labels).

Both are optional extras. They run before the vision model because a barcode or a
nameplate often gives the exact model and serial number for free, and their output is
passed to the model as evidence.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from PIL import Image

from .schemas import Barcode

try:
    import zxingcpp

    BARCODE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on installed extras
    zxingcpp = None
    BARCODE_AVAILABLE = False

try:
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    OCR_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on installed extras
    np = None
    RapidOCR = None
    OCR_AVAILABLE = False

MIN_OCR_SCORE = 0.6


def read_barcodes(image: Image.Image, image_index: int) -> list[Barcode]:
    if not BARCODE_AVAILABLE:
        return []
    found = zxingcpp.read_barcodes(image)
    return [
        Barcode(format=str(code.format).split(".")[-1], value=code.text, image_index=image_index)
        for code in found
        if code.text
    ]


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    return RapidOCR()


def read_text(image: Image.Image) -> list[str]:
    """Lines of printed text, highest-confidence first, low-confidence noise dropped."""
    if not OCR_AVAILABLE:
        return []
    result, _ = _ocr_engine()(np.asarray(image))
    lines = [(text.strip(), float(score)) for _box, text, score in (result or []) if text.strip()]
    lines = [line for line in lines if line[1] >= MIN_OCR_SCORE]
    lines.sort(key=lambda line: line[1], reverse=True)
    return [text for text, _ in lines]
