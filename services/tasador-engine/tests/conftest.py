from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tasador_engine.config import Settings
from tasador_engine.main import create_app
from tasador_engine.pages import PageFetcher
from tasador_engine.providers.common import ProviderError
from tasador_engine.providers.search import SearchHit
from tasador_engine.providers.vision import VisionAlternative, VisionResult


def jpeg_bytes(size: tuple[int, int] = (64, 48), color: str = "gray") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


def product_page(name: str, price: float | None = None, currency: str = "PLN", gtin: str | None = None,
                 mpn: str | None = None, condition: str = "NewCondition", availability: str = "InStock") -> str:
    """A shop page with schema.org JSON-LD, the way real shops publish it."""
    data: dict = {"@context": "https://schema.org", "@type": "Product", "name": name}
    if gtin:
        data["gtin13"] = gtin
    if mpn:
        data["mpn"] = mpn
    if price is not None:
        data["offers"] = {"@type": "Offer", "price": str(price), "priceCurrency": currency,
                          "itemCondition": f"https://schema.org/{condition}",
                          "availability": f"https://schema.org/{availability}"}
    return (f"<html><head><title>{name}</title>"
            f'<script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>')


class FakeVision:
    name = "fake-vision"

    def __init__(self, result: VisionResult | None = None, error: str | None = None):
        self.result = result or VisionResult(
            name="Pioneer DJ DJM-900NXS2 4-channel DJ mixer",
            brand="Pioneer DJ",
            model="DJM-900NXS2",
            type="mixer",
            confidence=0.91,
            alternatives=[VisionAlternative(name="Pioneer DJM-900NXS", model="DJM-900NXS", confidence=0.4)],
            search_queries=["Pioneer DJM-900NXS2 mixer"],
        )
        self.error = error
        self.calls: list[dict] = []

    async def identify(self, jpegs, context, ocr_text, barcodes):
        self.calls.append({"jpegs": jpegs, "context": context, "ocr_text": ocr_text, "barcodes": barcodes})
        if self.error:
            raise ProviderError(self.error)
        return self.result


DEFAULT_HITS = [
    SearchHit("https://www.pioneerdj.com/en/product/mixer/djm-900nxs2/", "DJM-900NXS2 - Pioneer DJ", "4-channel professional DJ mixer", "official"),
    SearchHit("https://www.manualslib.com/products/Pioneer-Djm-900nxs2.html", "Pioneer DJM-900NXS2 Manuals", "Operating instructions", "manual"),
    SearchHit("https://allegro.pl/oferta/1", "Pioneer DJM-900NXS2", "Mikser DJ, 10 499 zł", "new"),
    SearchHit("https://www.thomann.pl/djm900", "Pioneer DJ DJM-900NXS2", "In stock", "new"),
    SearchHit("https://olx.pl/d/oferta/djm", "Pioneer DJM-900NXS2 używany", "6 200 zł do negocjacji", "used"),
    SearchHit("https://forum.example.com/t/123", "Which mixer should I buy?", "Long thread", "new"),
]
DEFAULT_PAGES = {
    "https://allegro.pl/oferta/1": product_page("Pioneer DJ DJM-900NXS2", 10299, mpn="DJM-900NXS2"),
    "https://www.thomann.pl/djm900": product_page("Pioneer DJ DJM-900NXS2 Mixer", 9800),
    "https://olx.pl/d/oferta/djm": product_page("Mikser DJM 900 NXS2", 6200, condition="UsedCondition"),
    "https://www.pioneerdj.com/en/product/mixer/djm-900nxs2/": "<html><title>DJM-900NXS2 | Pioneer DJ</title></html>",
}


class FakeSearch:
    name = "fake-search"

    def __init__(self, hits: list[SearchHit] | None = None, error: str | None = None):
        self.hits = DEFAULT_HITS if hits is None else hits
        self.error = error
        self.calls: list[list] = []

    async def search(self, queries, context):
        self.calls.append(queries)
        if self.error:
            raise ProviderError(self.error)
        return [SearchHit(**vars(hit)) for hit in self.hits]


class FakeFetcher(PageFetcher):
    """Serves pages from a dict instead of the internet."""

    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = DEFAULT_PAGES if pages is None else pages
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> str | None:
        self.fetched.append(url)
        return self.pages.get(url)


@pytest.fixture
def settings() -> Settings:
    return Settings(vision_provider="none", search_provider="none")


def make_client(settings: Settings, vision=None, search=None, fetcher=None) -> TestClient:
    return TestClient(create_app(settings, vision=vision, search=search, fetcher=fetcher or FakeFetcher()))
