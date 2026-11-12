"""Sort web search results into what a person needs: the maker's page, reference
material (manuals, specs, reviews), and places to buy.

Rules only, no model: a result is a purchase link when it shows a price or comes from
a known shop, official when its domain carries the brand name, reference when its
title or URL says manual, specifications, review or similar.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .pricing import find_price, guess_condition
from .providers.search import SearchHit
from .schemas import Link, LinkKind, Links, PriceSource

MAX_OFFICIAL = 3
MAX_REFERENCE = 8
MAX_PURCHASE = 15

# Shops and marketplaces (host suffixes). Poland first, then pan-European and global.
SHOP_DOMAINS = (
    "allegro.pl", "olx.pl", "ceneo.pl", "x-kom.pl", "mediaexpert.pl", "mediamarkt.pl", "euro.com.pl",
    "morele.net", "komputronik.pl", "empik.com", "castorama.pl", "leroymerlin.pl", "obi.pl",
    "muzyczny.pl", "gear4music.pl", "sklep.", "thomann.de", "thomann.pl", "musicstore.de",
    "amazon.", "ebay.", "kleinanzeigen.de", "idealo.", "reverb.com", "bhphotovideo.com",
    "sweetwater.com", "ikea.com", "aliexpress.", "alibaba.com", "temu.com",
)
# Marketplaces where private sellers list used items next to new ones: a listing's
# condition there is never assumed from the query.
MARKETPLACES = ("olx.", "ebay.", "kleinanzeigen.de", "vinted.", "reverb.com", "facebook.com", "sprzedajemy.pl")
# Encyclopedias and manual libraries count as reference even without a keyword.
REFERENCE_DOMAINS = ("wikipedia.org", "manualslib.com", "manua.ls", "manualzz.com", "youtube.com", "rtings.com")

_PURCHASE_WORDS = re.compile(
    r"\b(buy|price|in stock|add to cart|shop|kup|kupię|cena|sklep|dostawa|w magazynie|"
    r"precio|comprar|preis|kaufen)\b",
    re.IGNORECASE,
)
_KIND_WORDS: list[tuple[LinkKind, re.Pattern[str]]] = [
    ("manual", re.compile(r"\b(manual\w*|user guide|instructions|instrukcj\w*|bedienungsanleitung|manual de usuario|\.pdf)\b|\.pdf$", re.IGNORECASE)),
    ("specs", re.compile(r"\b(spec|specs|specification\w*|datasheet|dane techniczne|specyfikacj\w*|technische daten)\b", re.IGNORECASE)),
    ("review", re.compile(r"\b(review\w*|recenzj\w*|test|opinie|hands-on)\b", re.IGNORECASE)),
]


def site_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    for suffix in suffixes:
        if suffix.endswith("."):  # prefix-style entries: "amazon." matches amazon.pl, amazon.de...
            if host.startswith(suffix) or f".{suffix}" in f".{host}":
                return True
        elif host == suffix or host.endswith("." + suffix):
            return True
    return False


def _brand_tokens(brand: str | None) -> list[str]:
    """'Pioneer DJ' -> ['pioneerdj', 'pioneer']: the joined name and its first word (3+ letters)."""
    words = re.findall(r"[a-z0-9]+", (brand or "").lower())
    tokens = ["".join(words)] + words[:1]
    return [t for t in dict.fromkeys(tokens) if len(t) >= 3]


def is_official(host: str, brand: str | None) -> bool:
    if _matches(host, SHOP_DOMAINS):  # "amazon" is never the maker's site, even for Amazon devices
        return False
    labels = host.split(".")
    registrable = labels[-2] if len(labels) >= 2 else host
    return any(token in registrable.replace("-", "") for token in _brand_tokens(brand))


def classify(hit: SearchHit, brand: str | None) -> Link | PriceSource | None:
    """One search hit -> a purchase listing, an official/reference link, or None (unrelated).

    The goal of the query that found the hit is only a hint: a shop found by a "manual"
    query is still a shop, and a manual found by a "new" query is still a manual.
    """
    url, title = hit.url, hit.title
    host = site_of(url)
    if not host:
        return None
    text = f"{title} {hit.snippet}"
    price = (hit.price, hit.currency) if hit.price and hit.currency else find_price(text)

    def listing() -> PriceSource:
        condition = guess_condition(text)
        if condition == "unknown" and hit.goal == "used":
            condition = "used"
        elif condition == "unknown" and hit.goal == "new" and not _matches(host, MARKETPLACES):
            condition = "new"  # a shop found by a "buy new" query sells it new unless it says otherwise
        return PriceSource(title=title, url=url, shop=host, price=price[0] if price else None,
                           currency=price[1] if price else None, condition=condition)

    if _matches(host, SHOP_DOMAINS) or (price and _PURCHASE_WORDS.search(text)):
        return listing()

    kind: LinkKind | None = None
    for candidate, pattern in _KIND_WORDS:
        if pattern.search(text) or pattern.search(url):
            kind = candidate
            break
    if is_official(host, brand):
        return Link(title=title, url=url, kind="official", site=host)
    if kind or _matches(host, REFERENCE_DOMAINS):
        return Link(title=title, url=url, kind=kind or "reference", site=host)
    if price or (hit.goal in ("new", "used") and hit.price is not None):
        return listing()  # a price on an unknown site is most likely a small shop
    return None  # unrelated: a forum post, a news item, a page that only ranked for the name


def sort_hits(hits: list[SearchHit], brand: str | None) -> Links:
    links = Links()
    for hit in hits:
        item = classify(hit, brand)
        if isinstance(item, PriceSource):
            links.purchase.append(item)
        elif isinstance(item, Link):
            (links.official if item.kind == "official" else links.reference).append(item)
    links.official = links.official[:MAX_OFFICIAL]
    links.reference = links.reference[:MAX_REFERENCE]
    links.purchase = links.purchase[:MAX_PURCHASE]
    return links
