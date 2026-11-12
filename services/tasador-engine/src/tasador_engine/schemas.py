"""The public contract: what /v1/identify accepts and returns.

Version "1". Adding optional fields is not breaking; renaming or removing one is.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CONTRACT_VERSION = "1"

Condition = Literal["new", "used", "unknown"]
# How a page was checked against the product key:
#   gtin / model: the page's own product data (or title) names the same barcode / model number
#   unconfirmed: the page was read but does not name the product's code
#   unchecked:    not fetched (limit reached, robots.txt, fetch failed)
Verification = Literal["gtin", "model", "unconfirmed", "unchecked"]


class KnownItem(BaseModel):
    """An item the caller already owns, used to flag likely duplicates."""

    id: str
    name: str | None = None
    brand: str | None = None
    model: str | None = None


class IdentifyContext(BaseModel):
    """Optional hints from the caller. The service knows nothing about the caller's app."""

    locale: str = "en"
    currency: str | None = Field(default=None, description="ISO 4217, e.g. PLN; prices in other currencies stay in sources only")
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2, e.g. PL; narrows the price search")
    allowed_types: list[str] = Field(default_factory=list, description="If set, `type` is one of these or null")
    hint: str | None = Field(default=None, max_length=500, description="Free text from the user, e.g. 'stage light'")
    known_items: list[KnownItem] = Field(default_factory=list, max_length=2000)
    search_prices: bool = Field(default=True, description="Search the web for prices and links (official page, manuals, shops)")


class ImageUrl(BaseModel):
    url: str


class IdentifyJsonRequest(BaseModel):
    """JSON form of the request: image URLs instead of uploaded files."""

    images: list[ImageUrl] = Field(min_length=1)
    context: IdentifyContext = Field(default_factory=IdentifyContext)


class Alternative(BaseModel):
    name: str
    brand: str | None = None
    model: str | None = None
    confidence: float = 0.0


KeyKind = Literal["gtin", "brand_model", "name"]


class ProductKey(BaseModel):
    """What the web search looks for. The strongest one available wins:
    a barcode number (GTIN), then brand + model number, then a descriptive name."""

    kind: KeyKind
    value: str


class Identification(BaseModel):
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    type: str | None = None
    gtin: str | None = Field(default=None, description="Barcode number (EAN/UPC), checksum-validated")
    serial_number: str | None = None
    description: str | None = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    key: ProductKey | None = None


class Barcode(BaseModel):
    format: str
    value: str
    image_index: int


class Extracted(BaseModel):
    """Raw text and codes read from the photos, before any model looked at them."""

    ocr_text: list[str] = Field(default_factory=list)
    barcodes: list[Barcode] = Field(default_factory=list)


class PriceSource(BaseModel):
    """A shop or marketplace page where the item can be bought."""

    title: str
    url: str
    shop: str | None = Field(default=None, description="Site domain, e.g. allegro.pl")
    price: float | None = None
    currency: str | None = None
    condition: Condition = "unknown"
    availability: str | None = Field(default=None, description="From the page's product data, e.g. InStock, OutOfStock")
    verified: Verification = "unchecked"
    seen_at: str | None = None


LinkKind = Literal["official", "manual", "specs", "review", "reference"]


class Link(BaseModel):
    """A page about the item that is not a shop listing."""

    title: str
    url: str
    kind: LinkKind = "reference"
    site: str | None = None
    verified: Verification = "unchecked"


class Links(BaseModel):
    """Everything the web search found, grouped by what the page is for."""

    official: list[Link] = Field(default_factory=list, description="The maker's own product pages")
    reference: list[Link] = Field(default_factory=list, description="Manuals, specifications, reviews, encyclopedia pages")
    purchase: list[PriceSource] = Field(default_factory=list, description="Shop and marketplace pages, with or without a visible price")


class PriceRange(BaseModel):
    min: float
    max: float
    median: float
    count: int


class Price(BaseModel):
    currency: str | None = None
    new: PriceRange | None = None
    used: PriceRange | None = None
    verified_only: bool = Field(default=False, description="True when the ranges use only pages confirmed to be this product")
    sources: list[PriceSource] = Field(default_factory=list)


class SimilarKnownItem(BaseModel):
    id: str
    similarity: float


class ProviderInfo(BaseModel):
    deployment: str
    vision: str | None = None
    search: str | None = None
    ocr: bool = False
    barcode: bool = False


class IdentifyResponse(BaseModel):
    version: str = CONTRACT_VERSION
    best: Identification
    alternatives: list[Alternative] = Field(default_factory=list)
    price: Price
    links: Links = Field(default_factory=Links)
    extracted: Extracted
    similar_known_items: list[SimilarKnownItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider: ProviderInfo
    timings_ms: dict[str, int] = Field(default_factory=dict)


class Capabilities(BaseModel):
    version: str = CONTRACT_VERSION
    engine_version: str
    provider: ProviderInfo
    heic: bool
    url_images: bool
    max_images: int
    max_image_bytes: int
