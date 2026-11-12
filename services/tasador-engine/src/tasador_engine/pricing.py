"""Turn price sources into new/used ranges, and read prices out of search snippets."""

from __future__ import annotations

import re
import statistics

from .schemas import Condition, Price, PriceRange, PriceSource

# Currency marker -> ISO code. Order matters: longer markers first.
_CURRENCY_MARKERS = [
    ("PLN", "PLN"), ("zł", "PLN"), ("zl", "PLN"),
    ("EUR", "EUR"), ("€", "EUR"),
    ("USD", "USD"), ("US$", "USD"), ("$", "USD"),
    ("GBP", "GBP"), ("£", "GBP"),
    ("CZK", "CZK"), ("Kč", "CZK"),
]
_MARKER_RE = "|".join(re.escape(marker) for marker, _ in _CURRENCY_MARKERS)
_NUMBER_RE = r"\d{1,3}(?:[  .,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_PRICE_RE = re.compile(
    rf"(?P<pre>{_MARKER_RE})\s?(?P<n1>{_NUMBER_RE})|(?P<n2>{_NUMBER_RE})\s?(?P<post>{_MARKER_RE})",
    re.IGNORECASE,
)
_USED_RE = re.compile(r"\b(used|second[- ]hand|pre-owned|refurbished|używan\w*|b/u|uzywan\w*|odnowion\w*)\b", re.IGNORECASE)
_NEW_RE = re.compile(r"\b(new|brand new|nowy|nowa|nowe|sealed)\b", re.IGNORECASE)


def _to_iso(marker: str) -> str:
    lowered = marker.lower()
    for candidate, iso in _CURRENCY_MARKERS:
        if candidate.lower() == lowered:
            return iso
    return marker.upper()


def parse_number(text: str) -> float | None:
    """'1 299,99' / '1.299,99' / '1,299.99' / '1299' -> 1299.99 / 1299.0."""
    cleaned = text.replace(" ", " ").strip()
    last_sep = max(cleaned.rfind(","), cleaned.rfind("."))
    decimals = ""
    if last_sep != -1 and len(cleaned) - last_sep - 1 in (1, 2):
        decimals = cleaned[last_sep + 1:]
        cleaned = cleaned[:last_sep]
    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return None
    return float(f"{digits}.{decimals}" if decimals else digits)


def find_price(text: str) -> tuple[float, str] | None:
    """First price with an explicit currency in free text."""
    for match in _PRICE_RE.finditer(text):
        number = match.group("n1") or match.group("n2")
        marker = match.group("pre") or match.group("post")
        value = parse_number(number)
        if value and value > 0:
            return value, _to_iso(marker)
    return None


def guess_condition(text: str) -> Condition:
    if _USED_RE.search(text):
        return "used"
    if _NEW_RE.search(text):
        return "new"
    return "unknown"


def _trim_outliers(values: list[float]) -> list[float]:
    """Drop values outside 1.5 IQR once there are enough to judge (a 'case only' listing, a typo)."""
    if len(values) < 4:
        return values
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    spread = q3 - q1
    low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
    return [v for v in values if low <= v <= high]


def _range(values: list[float]) -> PriceRange | None:
    values = _trim_outliers(sorted(values))
    if not values:
        return None
    return PriceRange(
        min=round(min(values), 2),
        max=round(max(values), 2),
        median=round(statistics.median(values), 2),
        count=len(values),
    )


def summarize(sources: list[PriceSource], currency: str | None) -> Price:
    """Ranges use only sources in the requested currency; all sources are returned.

    Without a requested currency, the most common one among the sources is used.
    Currencies are never converted: a converted price would look more exact than it is.
    """
    priced = [s for s in sources if s.price and s.price > 0 and s.currency]
    if currency is None and priced:
        currency = statistics.mode(s.currency for s in priced)
    in_currency = [s for s in priced if currency and s.currency.upper() == currency.upper()]
    new = [s.price for s in in_currency if s.condition == "new"]
    used = [s.price for s in in_currency if s.condition == "used"]
    return Price(currency=currency, new=_range(new), used=_range(used), sources=sources)
