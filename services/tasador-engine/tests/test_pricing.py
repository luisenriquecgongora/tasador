from __future__ import annotations

import pytest

from tasador_engine.matching import similar_items
from tasador_engine.pricing import find_price, guess_condition, parse_number, summarize
from tasador_engine.schemas import Identification, KnownItem, PriceSource


@pytest.mark.parametrize(
    "text, expected",
    [("1 299,99", 1299.99), ("1.299,99", 1299.99), ("1,299.99", 1299.99), ("1299", 1299.0),
     ("12 500", 12500.0), ("49,9", 49.9), ("abc", None)],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("Mikser Pioneer DJM-900NXS2 - 10 499,00 zł", (10499.0, "PLN")),
     ("Only €2,299.00 today", (2299.0, "EUR")),
     ("Price: 1299 PLN (used)", (1299.0, "PLN")),
     ("$899.99 free shipping", (899.99, "USD")),
     ("DJM-900NXS2 mixer, 4 channels", None)],
)
def test_find_price(text, expected):
    assert find_price(text) == expected


def test_guess_condition():
    assert guess_condition("Pioneer DJM używany stan bdb") == "used"
    assert guess_condition("Refurbished mixer") == "used"
    assert guess_condition("NOWY Pioneer DJM") == "new"
    assert guess_condition("Pioneer DJM") == "unknown"


def _src(price, currency="PLN", condition="new"):
    return PriceSource(title="t", url=f"https://x/{price}", price=price, currency=currency, condition=condition)


def test_summarize_trims_outliers_and_keeps_currency_separate():
    sources = [_src(p) for p in (9800, 10200, 10499, 10900, 99)] + [_src(2300, "EUR")]
    price = summarize(sources, "PLN")
    assert price.new.min == 9800 and price.new.count == 4  # the 99 PLN accessory is dropped
    assert price.used is None
    assert len(price.sources) == 6


def test_summarize_picks_most_common_currency_when_none_requested():
    price = summarize([_src(10, "EUR"), _src(12, "EUR"), _src(40, "PLN")], None)
    assert price.currency == "EUR" and price.new.count == 2


def test_similar_items():
    best = Identification(name="Pioneer DJM-900NXS2", brand="Pioneer DJ", model="DJM-900NXS2")
    known = [
        KnownItem(id="same-model", model="djm 900 nxs2"),
        KnownItem(id="close", brand="Pioneer DJ", model="DJM-900NXS"),
        KnownItem(id="other", name="Folding chair"),
    ]
    result = similar_items(best, known)
    assert [r.id for r in result] == ["same-model", "close"]
    assert similar_items(Identification(), known) == []
