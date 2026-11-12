from __future__ import annotations

import pytest

from tasador_engine.product_key import find_gtin, goal_queries, is_valid_gtin, product_key
from tasador_engine.schemas import Barcode


@pytest.mark.parametrize(
    "value, valid",
    [("5901234123457", True), ("5901234123458", False),  # EAN-13, wrong check digit
     ("036000291452", True), ("96385074", True),          # UPC-A, EAN-8
     ("12345", False), ("590123412345X", False)],
)
def test_gtin_check_digit(value, valid):
    assert is_valid_gtin(value) is valid


def test_find_gtin_prefers_barcodes_then_label_text():
    barcode = Barcode(format="EAN13", value="5901234123457", image_index=0)
    qr = Barcode(format="QRCode", value="5901234123457", image_index=0)
    assert find_gtin([barcode], []) == "5901234123457"
    assert find_gtin([qr], []) is None  # a QR code holding digits is not a product barcode
    assert find_gtin([], ["EAN 5901234 123457"]) == "5901234123457"
    assert find_gtin([], ["SERIAL 5901234123458"]) is None  # fails the checksum: probably a serial


def test_key_precedence():
    assert product_key("5901234123457", "Pioneer DJ", "DJM-900NXS2", "x").kind == "gtin"
    key = product_key(None, "Pioneer DJ", "DJM-900NXS2", "x")
    assert (key.kind, key.value) == ("brand_model", "Pioneer DJ DJM-900NXS2")
    assert product_key(None, "Sony", "Sony WH-1000XM5", None).value == "Sony WH-1000XM5"  # brand not repeated
    assert product_key(None, "IKEA", "X", "Black folding chair").kind == "name"  # model too short to trust
    assert product_key(None, None, None, None) is None


def test_goal_queries_per_language():
    key = product_key(None, "Pioneer DJ", "DJM-900NXS2", None)
    pl = {(q.goal, q.text) for q in goal_queries(key, "Pioneer DJ", "DJM-900NXS2", "pl-PL", ["Pioneer DJM mixer"])}
    assert pl == {
        ("official", "Pioneer DJ DJM-900NXS2"),
        ("manual", "Pioneer DJ DJM-900NXS2 instrukcja specyfikacja"),
        ("new", "Pioneer DJ DJM-900NXS2 cena sklep"),
        ("used", "Pioneer DJ DJM-900NXS2 używany"),
        ("new", "Pioneer DJM mixer"),
    }
    unknown = goal_queries(key, "Pioneer DJ", "DJM-900NXS2", "xx", None)
    assert ("used", "Pioneer DJ DJM-900NXS2 used") in {(q.goal, q.text) for q in unknown}


def test_goal_queries_for_barcode_and_name_keys():
    gtin = goal_queries(product_key("5901234123457", None, None, None), None, None, "en")
    assert gtin[0].text == "5901234123457"
    name = goal_queries(product_key(None, None, None, "Black folding chair"), None, None, "en")
    assert "official" not in {q.goal for q in name}  # no brand, no maker's page to look for
