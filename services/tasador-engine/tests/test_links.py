from __future__ import annotations

import pytest

from tasador_engine.links import classify, is_official, site_of, sort_hits
from tasador_engine.providers.search import SearchHit
from tasador_engine.schemas import Link, PriceSource


def _hit(url, title="t", snippet="", goal="new", **kw):
    return SearchHit(url=url, title=title, snippet=snippet, goal=goal, **kw)


def test_site_and_official():
    assert site_of("https://www.PioneerDJ.com/en/x") == "pioneerdj.com"
    assert is_official("pioneerdj.com", "Pioneer DJ")
    assert is_official("global.pioneer.com", "Pioneer DJ")
    assert not is_official("amazon.de", "Amazon")  # a shop is never the maker's site
    assert not is_official("pioneer-fans.example.org", None)


@pytest.mark.parametrize(
    "hit, expected_type, detail",
    [
        (_hit("https://allegro.pl/oferta/1", snippet="10 499 zł"), PriceSource, ("allegro.pl", 10499.0, "new")),
        (_hit("https://www.olx.pl/d/x", snippet="6 200 zł", goal="new"), PriceSource, ("olx.pl", 6200.0, "unknown")),
        (_hit("https://www.olx.pl/d/x", goal="used"), PriceSource, ("olx.pl", None, "used")),
        (_hit("https://smallshop.example.pl/p/1", snippet="Cena: 9 999 zł, dostawa 24h"), PriceSource, ("smallshop.example.pl", 9999.0, "new")),
        (_hit("https://x.example/manual.pdf", title="DJM-900NXS2"), Link, "manual"),
        (_hit("https://blog.example/post", title="DJM-900NXS2 review"), Link, "review"),
        (_hit("https://en.wikipedia.org/wiki/Pioneer_DJ", title="Pioneer DJ"), Link, "reference"),
        (_hit("https://www.pioneerdj.com/x", title="DJM-900NXS2"), Link, "official"),
        (_hit("https://forum.example/t/1", title="Which mixer should I buy?"), type(None), None),
    ],
)
def test_classify(hit, expected_type, detail):
    item = classify(hit, "Pioneer DJ")
    assert type(item) is expected_type
    if isinstance(item, PriceSource):
        assert (item.shop, item.price, item.condition) == detail
    elif isinstance(item, Link):
        assert item.kind == detail


def test_price_from_provider_wins_over_snippet():
    item = classify(_hit("https://shop.example/x", snippet="", price=120.0, currency="EUR"), None)
    assert isinstance(item, PriceSource) and (item.price, item.currency) == (120.0, "EUR")


def test_sort_hits_caps_each_group():
    hits = [_hit(f"https://allegro.pl/oferta/{i}", snippet=f"{100 + i} zł") for i in range(30)]
    hits += [_hit(f"https://www.pioneerdj.com/{i}", title="DJM") for i in range(5)]
    links = sort_hits(hits, "Pioneer DJ")
    assert len(links.purchase) == 15 and len(links.official) == 3 and links.reference == []
