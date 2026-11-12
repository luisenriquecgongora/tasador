from __future__ import annotations

import json

import httpx
import pytest

from tasador_engine.pages import PageFetcher, check_links, parse_product_data, verify
from tasador_engine.schemas import Links, PriceSource, ProductKey
from conftest import product_page


def _jsonld(data) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head></html>'


def test_parse_graph_brand_codes_and_aggregate_offer():
    html = _jsonld({"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "ignored"},
        {"@type": ["Product"], "name": "Pioneer DJ DJM-900NXS2", "brand": {"@type": "Brand", "name": "Pioneer DJ"},
         "gtin12": "036000291452", "mpn": "DJM-900NXS2", "sku": "PIO-123",
         "offers": {"@type": "AggregateOffer", "lowPrice": "9 799,00", "priceCurrency": "PLN"}},
    ]})
    page = parse_product_data(html)
    assert page.names == ["Pioneer DJ DJM-900NXS2"]
    assert page.gtins == {"00036000291452"}  # stored as GTIN-14, so UPC and EAN compare equal
    assert {"djm900nxs2", "pio123"} <= page.codes
    assert (page.offers[0].price, page.offers[0].currency) == (9799.0, "PLN")


def test_parse_opengraph_fallback_and_broken_json():
    html = ('<html><head><title>Mixer</title><script type="application/ld+json">{broken</script>'
            '<meta property="product:price:amount" content="1299.99">'
            '<meta property="product:price:currency" content="EUR"></head></html>')
    page = parse_product_data(html)
    assert page.title == "Mixer"
    assert (page.offers[0].price, page.offers[0].currency) == (1299.99, "EUR")


def test_verify():
    page = parse_product_data(product_page("Pioneer DJ DJM-900NXS2", 100, gtin="0036000291452", mpn="DJM-900NXS2"))
    assert verify(page, ProductKey(kind="gtin", value="036000291452"), None) == "gtin"
    assert verify(page, ProductKey(kind="brand_model", value="x"), "DJM 900 NXS2") == "model"
    assert verify(page, ProductKey(kind="brand_model", value="x"), "DJM-750MK2") == "unconfirmed"
    assert verify(page, None, "DJM") == "unconfirmed"  # too short to be a reliable match


async def test_check_links_reads_exact_prices():
    class Pages(PageFetcher):
        def __init__(self):
            pass

        async def fetch(self, url):
            return {"https://a.example/1": product_page("DJM-900NXS2", 9800, condition="UsedCondition",
                                                        availability="OutOfStock")}.get(url)

    links = Links(purchase=[PriceSource(title="t", url="https://a.example/1", price=1.0, currency="PLN"),
                            PriceSource(title="t", url="https://b.example/2")])
    read = await check_links(links, ProductKey(kind="brand_model", value="x"), "DJM-900NXS2", Pages(), limit=8)
    first, second = links.purchase
    assert read == 1
    assert (first.price, first.condition, first.availability, first.verified) == (9800.0, "used", "OutOfStock", "model")
    assert second.verified == "unchecked"


def _fetcher(handler) -> PageFetcher:
    return PageFetcher(5, client=httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True))


async def test_fetcher_respects_robots_txt():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, html="<html>ok</html>")

    fetcher = _fetcher(handler)
    assert await fetcher.fetch("https://shop.example/p/1") == "<html>ok</html>"
    assert await fetcher.fetch("https://shop.example/private/1") is None


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.5/x", "http://localhost:8000/x",
                                 "http://[::1]/x", "file:///etc/passwd", "http://router.local/"])
async def test_fetcher_refuses_private_addresses(url):
    fetcher = _fetcher(lambda request: httpx.Response(200, html="secret"))
    assert await fetcher.fetch(url) is None


async def test_fetcher_refuses_redirect_to_private_address_and_non_html():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        if request.url.path == "/file.zip":
            return httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
        return httpx.Response(200, text="metadata")

    fetcher = _fetcher(handler)
    assert await fetcher.fetch("https://shop.example/go") is None
    assert await fetcher.fetch("https://shop.example/file.zip") is None
