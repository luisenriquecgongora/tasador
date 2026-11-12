"""The product key and the searches built from it.

Price comparison sites group offers by identifier: a barcode number (GTIN) first, then
brand + manufacturer part number (model), then descriptive attributes. Tasador does the
same: it picks the strongest key it has and writes one search per goal around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .schemas import Barcode, ProductKey

Goal = Literal["official", "manual", "new", "used"]

_GTIN_FORMATS = {"EAN13", "EAN8", "UPCA", "UPCE", "ITF", "EAN-13", "EAN-8", "UPC-A", "UPC-E"}

# Words that steer a search engine to each goal, per language. Unknown languages use English.
GOAL_WORDS: dict[str, dict[Goal, str]] = {
    "en": {"official": "", "manual": "manual specifications", "new": "price buy", "used": "used"},
    "pl": {"official": "", "manual": "instrukcja specyfikacja", "new": "cena sklep", "used": "używany"},
    "es": {"official": "", "manual": "manual especificaciones", "new": "precio comprar", "used": "segunda mano"},
    "de": {"official": "", "manual": "Bedienungsanleitung technische Daten", "new": "Preis kaufen", "used": "gebraucht"},
    "fr": {"official": "", "manual": "notice caractéristiques", "new": "prix acheter", "used": "occasion"},
}


@dataclass(frozen=True)
class GoalQuery:
    goal: Goal
    text: str


def is_valid_gtin(value: str) -> bool:
    """GTIN-8/12/13/14 with a correct check digit (the last digit)."""
    if not value.isdigit() or len(value) not in (8, 12, 13, 14):
        return False
    digits = [int(d) for d in value]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def find_gtin(barcodes: list[Barcode], ocr_text: list[str]) -> str | None:
    """A barcode read from the photo, else a valid GTIN printed on the label."""
    for code in barcodes:
        value = code.value.strip()
        if code.format.replace("_", "").upper() in _GTIN_FORMATS and is_valid_gtin(value):
            return value
    for line in ocr_text:
        for candidate in re.findall(r"\d{8,14}", line.replace(" ", "")):
            if len(candidate) in (12, 13) and is_valid_gtin(candidate):
                return candidate
    return None


def normalize_code(text: str | None) -> str:
    """'DJM 900-NXS2' -> 'djm900nxs2': how model numbers are compared everywhere."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def product_key(gtin: str | None, brand: str | None, model: str | None, name: str | None) -> ProductKey | None:
    if gtin:
        return ProductKey(kind="gtin", value=gtin)
    if brand and model and len(normalize_code(model)) >= 3:
        value = model if normalize_code(brand) in normalize_code(model) else f"{brand} {model}"
        return ProductKey(kind="brand_model", value=value)
    if name:
        return ProductKey(kind="name", value=name)
    return None


def goal_queries(
    key: ProductKey, brand: str | None, model: str | None, locale: str, extra: list[str] | None = None
) -> list[GoalQuery]:
    """One query per goal. With only a barcode, the barcode is searched as-is (shops list
    it), and brand + model are searched too when known."""
    words = GOAL_WORDS.get(locale.split("-")[0].lower(), GOAL_WORDS["en"])
    label = f"{brand} {model}".strip() if brand and model else key.value
    queries: list[GoalQuery] = []
    if key.kind == "gtin":
        queries.append(GoalQuery("new", key.value))
    if key.kind != "name":
        queries.append(GoalQuery("official", label))
    queries.append(GoalQuery("manual", f"{label} {words['manual']}"))
    queries.append(GoalQuery("new", f"{label} {words['new']}"))
    queries.append(GoalQuery("used", f"{label} {words['used']}"))
    for text in (extra or [])[:1]:
        if text and all(text.lower() != q.text.lower() for q in queries):
            queries.append(GoalQuery("new", text))
    return queries
