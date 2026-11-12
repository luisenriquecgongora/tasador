"""Open the pages a search found and check them, the way price comparison sites do.

Most shops embed schema.org `Product` / `Offer` data in their pages (JSON-LD) so that
search engines can show prices. Reading it gives the exact price, currency, condition and
availability, and the page's own barcode number (GTIN) and model number, which tells us
whether the page is really about this product. No model is involved.

Politeness and safety: robots.txt is respected, only a handful of pages are fetched per
request, private and local addresses are refused (also after redirects), and pages are
size-capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import __version__
from .pricing import parse_number
from .product_key import normalize_code
from .schemas import Condition, Link, Links, PriceSource, ProductKey, Verification

USER_AGENT = f"TasadorBot/{__version__} (product lookup; respects robots.txt)"
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_PARALLEL_FETCHES = 4

_JSONLD_RE = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_META_RE = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR_RE = re.compile(r"([a-zA-Z:_-]+)\s*=\s*[\"']([^\"']*)[\"']")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "upc")
_CODE_KEYS = ("mpn", "sku", "model", "productID")


class BlockedAddress(Exception):
    pass


@dataclass
class Offer:
    price: float
    currency: str | None
    condition: Condition = "unknown"
    availability: str | None = None


@dataclass
class PageProduct:
    title: str = ""
    names: list[str] = field(default_factory=list)
    gtins: set[str] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)
    offers: list[Offer] = field(default_factory=list)

    @property
    def has_product_data(self) -> bool:
        return bool(self.names or self.gtins or self.codes or self.offers)


def _gtin14(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(14) if 8 <= len(digits) <= 14 else None


def _text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return str(value or "")


def _condition(value: object) -> Condition:
    text = str(value or "").lower()
    if "used" in text or "refurbished" in text or "damaged" in text:
        return "used"
    if "new" in text:
        return "new"
    return "unknown"


def _offers(value: object) -> list[Offer]:
    items = value if isinstance(value, list) else [value]
    found: list[Offer] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "offers" in item and item.get("@type") == "AggregateOffer" and isinstance(item["offers"], (list, dict)):
            found.extend(_offers(item["offers"]))
            continue
        raw = item.get("price", item.get("lowPrice"))
        price = parse_number(str(raw)) if raw not in (None, "") else None
        if price and price > 0:
            found.append(Offer(
                price=price,
                currency=(item.get("priceCurrency") or None),
                condition=_condition(item.get("itemCondition")),
                availability=str(item.get("availability") or "").rsplit("/", 1)[-1] or None,
            ))
    return found


def _is_product(node: dict) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(str(k) in ("Product", "ProductModel", "IndividualProduct", "ProductGroup") for k in kinds)


def _walk(node: object, out: PageProduct) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, out)
        return
    if not isinstance(node, dict):
        return
    if _is_product(node):
        if node.get("name"):
            out.names.append(_text(node["name"]))
        for key in _GTIN_KEYS:
            if node.get(key) and (gtin := _gtin14(node[key])):
                out.gtins.add(gtin)
        for key in _CODE_KEYS:
            if node.get(key) and (code := normalize_code(_text(node[key]))):
                out.codes.add(code)
        out.offers.extend(_offers(node.get("offers")))
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, out)


def parse_product_data(html: str) -> PageProduct:
    page = PageProduct()
    if match := _TITLE_RE.search(html):
        page.title = re.sub(r"\s+", " ", match.group(1)).strip()
    for block in _JSONLD_RE.findall(html):
        cleaned = block.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            _walk(json.loads(cleaned), page)
        except ValueError:
            continue
    if not page.offers:  # OpenGraph product tags, used by many smaller shops
        meta = {}
        for tag in _META_RE.findall(html):
            attrs = dict(_ATTR_RE.findall(tag))
            name = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if name and "content" in attrs:
                meta[name.lower()] = attrs["content"]
        amount = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
        price = parse_number(amount) if amount else None
        if price:
            currency = meta.get("product:price:currency") or meta.get("og:price:currency") or meta.get("pricecurrency")
            page.offers.append(Offer(price=price, currency=currency))
    return page


def verify(page: PageProduct, key: ProductKey | None, model: str | None) -> Verification:
    """Is this page about the product? Barcode number first, then model number."""
    if key and key.kind == "gtin" and _gtin14(key.value) in page.gtins:
        return "gtin"
    wanted = normalize_code(model) if model else ""
    if len(wanted) >= 4:
        haystack = " ".join([page.title, *page.names])
        if wanted in page.codes or any(wanted in code for code in page.codes) or wanted in normalize_code(haystack):
            return "model"
    return "unconfirmed"


def _check_public(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or host == "localhost" or host.endswith(".local"):
        raise BlockedAddress(url)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # a name; resolving it here would not stop DNS tricks, the IP check below covers literals
    if not address.is_global:
        raise BlockedAddress(url)


class PageFetcher:
    def __init__(self, timeout_seconds: float, client: httpx.AsyncClient | None = None):
        async def guard(request: httpx.Request) -> None:  # runs for every hop, redirects included
            _check_public(str(request.url))

        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        self._client.event_hooks["request"] = [guard]
        self._robots: dict[str, RobotFileParser | None] = {}

    async def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                response = await self._client.get(f"{origin}/robots.txt")
                if response.status_code in (401, 403):
                    parser.disallow_all = True
                elif response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser = None  # no robots.txt: everything allowed
            except (httpx.HTTPError, BlockedAddress):
                parser = None
            self._robots[origin] = parser
        robots = self._robots[origin]
        return robots is None or robots.can_fetch(USER_AGENT, url)

    async def fetch(self, url: str) -> str | None:
        try:
            _check_public(url)
            if not await self._allowed(url):
                return None
            async with self._client.stream("GET", url) as response:
                if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                    return None
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PAGE_BYTES:
                        break
                    chunks.append(chunk)
                return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        except (httpx.HTTPError, BlockedAddress, UnicodeDecodeError):
            return None


async def check_links(links: Links, key: ProductKey | None, model: str | None, fetcher: PageFetcher, limit: int) -> int:
    """Fetch up to `limit` pages (shops first, then the maker's pages), mark each with how it
    was verified, and take exact prices from the page's own offer data. Returns pages read."""
    targets: list[PriceSource | Link] = [*links.purchase, *links.official[:2]][:limit]
    slots = asyncio.Semaphore(MAX_PARALLEL_FETCHES)

    async def one(item: PriceSource | Link) -> bool:
        async with slots:
            html = await fetcher.fetch(item.url)
        if html is None:
            return False
        page = parse_product_data(html)
        item.verified = verify(page, key, model)
        if isinstance(item, PriceSource) and page.offers:
            offer = page.offers[0]
            item.price, item.currency = offer.price, (offer.currency or item.currency)
            item.availability = offer.availability
            if offer.condition != "unknown":
                item.condition = offer.condition
        return True

    results = await asyncio.gather(*(one(item) for item in targets))
    return sum(results)
