"""Several photos of one item: how they are read, chosen and described to the model."""

from __future__ import annotations

import json

from conftest import FakeSearch, FakeVision, jpeg_bytes, make_client
from tasador_engine.pipeline import choose_photos, labelled_text
from tasador_engine.providers.vision import VisionResult, evidence_text
from tasador_engine.schemas import Barcode, IdentifyContext


def _post(client, count: int):
    files = [("images", (f"p{i}.jpg", jpeg_bytes(), "image/jpeg")) for i in range(count)]
    return client.post("/v1/identify", files=files, data={"context": json.dumps({"search_prices": False})}).json()


def test_all_photos_go_to_the_model_by_default(settings):
    vision = FakeVision()
    body = _post(make_client(settings, vision, FakeSearch()), 5)
    assert len(vision.calls[0]["jpegs"]) == 5
    assert body["warnings"] == []


def test_a_model_with_a_photo_limit_gets_the_most_useful_photos(settings):
    vision = FakeVision()
    vision.max_images = 2
    body = _post(make_client(settings, vision), 5)
    assert len(vision.calls[0]["jpegs"]) == 2
    assert any("saw 2 of 5 photos" in w for w in body["warnings"])


def test_choose_photos_keeps_the_first_then_barcodes_then_most_text():
    text = [["front"], [], ["MODEL X", "SERIAL 1", "220V"], ["logo"], []]
    barcode = Barcode(format="EAN13", value="5901234123457", image_index=4)
    assert choose_photos(text, [barcode], 3) == [0, 2, 4]
    assert choose_photos(text, [], 2) == [0, 2]
    assert choose_photos(text, [], None) == [0, 1, 2, 3, 4]


def test_text_is_tagged_with_the_photo_it_came_from():
    text = [["Pioneer DJ"], ["MODEL DJM-900NXS2"], ["SERIAL AB1"]]
    assert labelled_text(text, [0, 2]) == ["photo 1: Pioneer DJ", "a photo not shown: MODEL DJM-900NXS2", "photo 2: SERIAL AB1"]
    assert labelled_text([["only"]], [0]) == ["only"]


def test_model_is_told_the_photos_are_one_item():
    prompt = evidence_text(IdentifyContext(), ["photo 2: SERIAL AB1"],
                           [Barcode(format="EAN13", value="590", image_index=1),
                            Barcode(format="EAN13", value="591", image_index=-1)], photo_count=3)
    assert "Photos: 3, all of the same item" in prompt
    assert "EAN13 590 (photo 2)" in prompt and "EAN13 591 (a photo not shown)" in prompt


def test_photos_of_different_items_give_a_warning(settings):
    vision = FakeVision(VisionResult(name="Chair", confidence=0.5, same_item=False))
    body = _post(make_client(settings, vision), 2)
    assert any("different items" in w for w in body["warnings"])
