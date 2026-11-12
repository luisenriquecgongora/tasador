from __future__ import annotations

import io
import json
from dataclasses import replace

import zxingcpp
from PIL import Image

from conftest import FakeFetcher, FakeSearch, FakeVision, jpeg_bytes, make_client, product_page
from tasador_engine.providers.search import SearchHit


def _post(client, images: list[bytes], context: dict | None = None, headers: dict | None = None):
    files = [("images", (f"p{i}.jpg", data, "image/jpeg")) for i, data in enumerate(images)]
    data = {"context": json.dumps(context)} if context is not None else {}
    return client.post("/v1/identify", files=files, data=data, headers=headers or {})


def test_health_is_open(settings):
    client = make_client(replace(settings, api_token="secret"))
    assert client.get("/health").json()["status"] == "ok"


def test_token_is_required_when_configured(settings):
    client = make_client(replace(settings, api_token="secret"), FakeVision())
    assert _post(client, [jpeg_bytes()]).status_code == 401
    assert _post(client, [jpeg_bytes()], headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert _post(client, [jpeg_bytes()], headers={"Authorization": "Bearer secret"}).status_code == 200


def test_identify_full_response(settings):
    vision, search, fetcher = FakeVision(), FakeSearch(), FakeFetcher()
    client = make_client(settings, vision, search, fetcher)
    context = {
        "locale": "pl", "currency": "PLN", "country": "PL",
        "allowed_types": ["mixer", "speaker"],
        "known_items": [{"id": "e1", "brand": "Pioneer", "model": "DJM 900 NXS2"}, {"id": "e2", "name": "Chair"}],
    }
    response = _post(client, [jpeg_bytes(), jpeg_bytes()], context)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["version"] == "1"
    best = body["best"]
    assert (best["model"], best["type"]) == ("DJM-900NXS2", "mixer")
    assert best["key"] == {"kind": "brand_model", "value": "Pioneer DJ DJM-900NXS2"}
    assert body["alternatives"][0]["model"] == "DJM-900NXS"

    # one search per goal, in Polish
    goals = [(q.goal, q.text) for q in search.calls[0]]
    assert ("official", "Pioneer DJ DJM-900NXS2") in goals
    assert ("used", "Pioneer DJ DJM-900NXS2 używany") in goals

    links = body["links"]
    assert [link["site"] for link in links["official"]] == ["pioneerdj.com"]
    assert links["official"][0]["verified"] == "model"
    assert [link["kind"] for link in links["reference"]] == ["manual"]
    shops = {link["shop"]: link for link in links["purchase"]}
    assert set(shops) == {"allegro.pl", "thomann.pl", "olx.pl"}  # the forum thread is dropped
    assert shops["allegro.pl"]["price"] == 10299  # exact price from the page, not the snippet's 10 499
    assert shops["allegro.pl"]["verified"] == "model"
    assert shops["thomann.pl"]["availability"] == "InStock"
    assert shops["olx.pl"]["condition"] == "used"

    price = body["price"]
    assert price["verified_only"] is True
    assert price["new"] == {"min": 9800.0, "max": 10299.0, "median": 10049.5, "count": 2}
    assert price["used"]["min"] == 6200
    assert body["similar_known_items"] == [{"id": "e1", "similarity": 1.0}]
    assert body["warnings"] == []
    assert len(vision.calls[0]["jpegs"]) == 2
    assert "verify" in body["timings_ms"]


def test_unconfirmed_pages_fall_back_to_snippet_prices_with_a_warning(settings):
    fetcher = FakeFetcher(pages={})  # every page fetch fails
    body = _post(make_client(settings, FakeVision(), FakeSearch(), fetcher), [jpeg_bytes()], {"currency": "PLN"}).json()
    assert body["price"]["verified_only"] is False
    assert body["price"]["new"]["min"] == 10499  # from the allegro snippet
    assert all(link["verified"] == "unchecked" for link in body["links"]["purchase"])
    assert any("search snippets" in w for w in body["warnings"])


def test_page_of_a_different_product_does_not_count(settings):
    pages = {"https://allegro.pl/oferta/1": product_page("Pioneer DJM-750MK2", 4999, mpn="DJM-750MK2")}
    hits = [SearchHit("https://allegro.pl/oferta/1", "Pioneer DJM-900NXS2 mixer", "4 999 zł", "new")]
    body = _post(make_client(settings, FakeVision(), FakeSearch(hits), FakeFetcher(pages)), [jpeg_bytes()],
                 {"currency": "PLN"}).json()
    assert body["links"]["purchase"][0]["verified"] == "unconfirmed"
    assert body["price"]["verified_only"] is False


def test_type_outside_allowed_types_is_dropped(settings):
    client = make_client(settings, FakeVision(), FakeSearch())
    body = _post(client, [jpeg_bytes()], {"allowed_types": ["speaker"]}).json()
    assert body["best"]["type"] is None
    assert any("allowed_types" in w for w in body["warnings"])


def test_without_providers_returns_warnings_not_errors(settings):
    body = _post(make_client(settings), [jpeg_bytes()]).json()
    assert body["best"]["name"] is None
    assert body["best"]["key"] is None
    assert body["warnings"] == ["no vision provider configured: only barcodes and printed text were read"]


def test_provider_failures_become_warnings(settings):
    client = make_client(settings, FakeVision(), FakeSearch(error="web search failed: HTTP 529"))
    body = _post(client, [jpeg_bytes()]).json()
    assert body["best"]["brand"] == "Pioneer DJ"
    assert body["price"]["new"] is None
    assert body["links"]["purchase"] == []
    assert "web search failed: HTTP 529" in body["warnings"]

    body = _post(make_client(settings, FakeVision(error="vision model declined the request")), [jpeg_bytes()]).json()
    assert body["warnings"][0] == "vision model declined the request"


def test_search_can_be_skipped(settings):
    search = FakeSearch()
    _post(make_client(settings, FakeVision(), search), [jpeg_bytes()], {"search_prices": False})
    assert search.calls == []


def test_barcode_becomes_the_product_key(settings):
    barcode = zxingcpp.create_barcode("5901234123457", zxingcpp.BarcodeFormat.EAN13).to_image(scale=4)
    canvas = Image.new("RGB", (barcode.shape[1] + 80, barcode.shape[0] + 80), "white")
    canvas.paste(Image.frombytes("L", (barcode.shape[1], barcode.shape[0]), bytes(memoryview(barcode))), (40, 40))
    out = io.BytesIO()
    canvas.save(out, format="PNG")

    vision, search = FakeVision(), FakeSearch(hits=[])
    body = _post(make_client(settings, vision, search), [out.getvalue()]).json()
    assert body["extracted"]["barcodes"][0]["value"] == "5901234123457"
    assert vision.calls[0]["barcodes"][0].value == "5901234123457"
    assert "barcode:1" in body["best"]["evidence"]
    assert body["best"]["gtin"] == "5901234123457"
    assert body["best"]["key"] == {"kind": "gtin", "value": "5901234123457"}
    assert ("new", "5901234123457") in [(q.goal, q.text) for q in search.calls[0]]
    assert "no shop listings found" in body["warnings"]


def test_rejects_bad_input(settings):
    client = make_client(settings, FakeVision())
    assert _post(client, [b"not an image"]).status_code == 422
    only_context = client.post("/v1/identify", files={"context": (None, "{}")})
    assert only_context.status_code == 422 and "at least one" in only_context.json()["detail"]
    assert _post(client, [jpeg_bytes()] * 9).status_code == 422
    assert _post(client, [jpeg_bytes()], {"currency": 5, "known_items": "x"}).status_code == 422
    assert client.post("/v1/identify", content=b"x", headers={"content-type": "text/plain"}).status_code == 415


def test_large_photos_are_downscaled_and_metadata_dropped(settings):
    vision = FakeVision()
    _post(make_client(settings, vision), [jpeg_bytes((4000, 3000))])
    sent = Image.open(io.BytesIO(vision.calls[0]["jpegs"][0]))
    assert max(sent.size) == settings.max_image_side
    assert not sent.getexif()


def test_url_images_disabled_by_default(settings):
    client = make_client(settings, FakeVision())
    response = client.post("/v1/identify", json={"images": [{"url": "https://cdn.example.com/a.jpg"}]})
    assert response.status_code == 422
    assert "disabled" in response.json()["detail"]


def test_docs_page_offers_a_photo_upload_form(settings):
    spec = make_client(settings).get("/openapi.json").json()
    body = spec["paths"]["/v1/identify"]["post"]["requestBody"]["content"]
    images = body["multipart/form-data"]["schema"]["properties"]["images"]
    assert images["items"]["format"] == "binary"
    assert "application/json" in body


def test_capabilities(settings):
    body = make_client(settings, FakeVision()).get("/v1/capabilities").json()
    assert body["provider"]["vision"] == "fake-vision"
    assert body["provider"]["search"] is None
    assert body["url_images"] is False


def test_real_world_search_results(settings):
    """Shapes seen in a real run: search-results pages, tracking parameters, other models."""
    hits = [
        SearchHit("https://www.olx.pl/elektronika/q-djm-900nxs2/?srsltid=abc", "djm 900nxs2 - OLX", "4 400 zł", "used"),
        SearchHit("https://muzyczny.pl/163089_Pioneer-DJM900NXS-2.html?srsltid=one", "Pioneer DJM900NXS 2", "", "new"),
        SearchHit("https://muzyczny.pl/163089_Pioneer-DJM900NXS-2.html?srsltid=two", "Pioneer DJM900NXS 2", "", "new"),
        SearchHit("https://veramusic.pl/print_product/12506", "Pioneer DJM-A9 - specyfikacja", "", "manual"),
        SearchHit("https://www.musicaudio.pl/sklep-muzyczny/pioneer-djm-900nxs2-8169.html", "Pioneer DJM-900NXS2, cena, opinie", "", "new"),
    ]
    pages = {"https://muzyczny.pl/163089_Pioneer-DJM900NXS-2.html":
             product_page("Pioneer DJM900NXS 2 DJ mikser", 9490, gtin="4573201240224")}
    fetcher = FakeFetcher(pages)
    body = _post(make_client(settings, FakeVision(), FakeSearch(hits), fetcher), [jpeg_bytes()], {"currency": "PLN"}).json()

    purchase = {link["url"]: link for link in body["links"]["purchase"]}
    olx = "https://www.olx.pl/elektronika/q-djm-900nxs2/"
    assert purchase[olx]["page"] == "search" and purchase[olx]["price"] is None  # browse link, price ignored
    assert olx not in fetcher.fetched  # no page check spent on a search page
    assert list(purchase).count("https://muzyczny.pl/163089_Pioneer-DJM900NXS-2.html") == 1  # deduplicated
    assert "https://www.musicaudio.pl/sklep-muzyczny/pioneer-djm-900nxs2-8169.html" in purchase  # "sklep" = shop
    assert all("DJM-A9" not in link["title"] for link in body["links"]["reference"])  # another model, dropped

    assert body["price"]["new"] == {"min": 9490.0, "max": 9490.0, "median": 9490.0, "count": 1}
    assert body["price"]["used"] is None
    assert body["best"]["gtin"] == "4573201240224"  # learned from the verified shop page
    assert "gtin:from-shop-page" in body["best"]["evidence"]

