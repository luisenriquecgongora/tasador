"""The identify pipeline.

    photos -> read locally (barcodes, label text) -> identify (vision model)
           -> product key (GTIN > brand + model > name) -> one web search per goal
           -> sort hits (official / reference / purchase) -> open top pages and verify
           -> price ranges -> match owned items

Each stage is independent: a stage that is not configured or fails adds a warning and
the response carries whatever the other stages found.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

from . import local_readers
from .config import Settings
from .images import PreparedImage
from .links import sort_hits
from .matching import similar_items
from .pages import PageFetcher, check_links
from .pricing import summarize
from .product_key import find_gtin, goal_queries, product_key
from .providers.common import ProviderError
from .providers.search import SearchProvider
from .providers.vision import VisionProvider, VisionResult
from .schemas import (
    Alternative,
    Barcode,
    Extracted,
    IdentifyContext,
    IdentifyResponse,
    Identification,
    Links,
    Price,
    ProviderInfo,
)


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 3)


def _read_locally(images: list[PreparedImage]) -> tuple[Extracted, list[list[str]]]:
    """Barcodes and text from every photo: the merged result, plus the text of each photo."""
    barcodes: list[Barcode] = []
    lines: list[str] = []
    per_photo: list[list[str]] = []
    for index, prepared in enumerate(images):
        barcodes.extend(local_readers.read_barcodes(prepared.image, index))
        photo_lines = local_readers.read_text(prepared.image)
        per_photo.append(photo_lines)
        for line in photo_lines:
            if line not in lines:
                lines.append(line)
    return Extracted(ocr_text=lines, barcodes=barcodes), per_photo


def choose_photos(per_photo_text: list[list[str]], barcodes: list[Barcode], limit: int | None) -> list[int]:
    """Which photos to send when the model takes fewer than were uploaded: the first one
    (usually the overall view), then those with a barcode, then those with the most text."""
    count = len(per_photo_text)
    if limit is None or count <= limit:
        return list(range(count))
    with_codes = {b.image_index for b in barcodes}
    rest = sorted(range(1, count), key=lambda i: (i not in with_codes, -len(per_photo_text[i]), i))
    return sorted([0, *rest[: limit - 1]])


def labelled_text(per_photo_text: list[list[str]], sent: list[int]) -> list[str]:
    """Text for the model, each line tagged with the photo number the model sees it as."""
    if len(sent) == 1 and len(per_photo_text) == 1:
        return list(per_photo_text[0])
    position = {photo: n + 1 for n, photo in enumerate(sent)}
    labelled = []
    for photo, lines in enumerate(per_photo_text):
        tag = f"photo {position[photo]}" if photo in position else "a photo not shown"
        labelled.extend(f"{tag}: {line}" for line in lines)
    return labelled


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        vision: VisionProvider | None,
        search: SearchProvider | None,
        fetcher: PageFetcher | None = None,
    ):
        self.settings = settings
        self.vision = vision
        self.search = search
        self.fetcher = fetcher or PageFetcher(settings.page_timeout_seconds)
        self._slots = asyncio.Semaphore(settings.max_concurrency)

    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            deployment=self.settings.deployment,
            vision=self.vision.name if self.vision else None,
            search=self.search.name if self.search else None,
            ocr=local_readers.OCR_AVAILABLE,
            barcode=local_readers.BARCODE_AVAILABLE,
        )

    async def run(self, images: list[PreparedImage], context: IdentifyContext) -> IdentifyResponse:
        warnings: list[str] = []
        timings: dict[str, int] = {}
        started = time.perf_counter()

        async with self._slots:
            t = time.perf_counter()
            extracted, per_photo_text = await asyncio.to_thread(_read_locally, images)
            timings["local_read"] = int((time.perf_counter() - t) * 1000)

            result = VisionResult()
            if self.vision is None:
                warnings.append("no vision provider configured: only barcodes and printed text were read")
            else:
                sent = choose_photos(per_photo_text, extracted.barcodes, getattr(self.vision, "max_images", None))
                if len(sent) < len(images):
                    warnings.append(
                        f"the vision model saw {len(sent)} of {len(images)} photos (photos "
                        f"{', '.join(str(i + 1) for i in sent)}); text and barcodes were read from all of them"
                    )
                position = {photo: n for n, photo in enumerate(sent)}
                barcodes = [
                    b.model_copy(update={"image_index": position.get(b.image_index, -1)})
                    for b in extracted.barcodes
                ]
                t = time.perf_counter()
                try:
                    result = await self.vision.identify(
                        [images[i].jpeg for i in sent], context, labelled_text(per_photo_text, sent), barcodes
                    )
                except ProviderError as exc:
                    warnings.append(str(exc))
                timings["vision"] = int((time.perf_counter() - t) * 1000)
                if not result.same_item:
                    warnings.append("the photos seem to show different items: the result describes the most prominent one")

        best = self._best(result, extracted, context, warnings)
        alternatives = [
            Alternative(name=a.name, brand=a.brand, model=a.model, confidence=_clamp(a.confidence))
            for a in result.alternatives[:3]
        ]

        links, price = Links(), Price(currency=context.currency)
        if context.search_prices and best.key:
            if self.search is None:
                warnings.append("no web search provider configured: no links or prices")
            else:
                links, price = await self._search_and_verify(best, result, context, warnings, timings)

        timings["total"] = int((time.perf_counter() - started) * 1000)
        return IdentifyResponse(
            best=best,
            alternatives=alternatives,
            price=price,
            links=links,
            extracted=extracted,
            similar_known_items=similar_items(best, context.known_items),
            warnings=warnings,
            provider=self.provider_info(),
            timings_ms=timings,
        )

    async def _search_and_verify(self, best, result, context, warnings, timings) -> tuple[Links, Price]:
        queries = goal_queries(best.key, best.brand, best.model, context.locale, result.search_queries)
        t = time.perf_counter()
        try:
            hits = await self.search.search(queries, context)
        except ProviderError as exc:
            warnings.append(str(exc))
            return Links(), Price(currency=context.currency)
        finally:
            timings["search"] = int((time.perf_counter() - t) * 1000)

        links = sort_hits(hits, best.brand)
        if self.settings.verify_pages > 0 and (links.purchase or links.official):
            t = time.perf_counter()
            await check_links(links, best.key, best.model, self.fetcher, self.settings.verify_pages)
            timings["verify"] = int((time.perf_counter() - t) * 1000)

        today = date.today().isoformat()
        for listing in links.purchase:
            listing.seen_at = today

        confirmed = [s for s in links.purchase if s.verified in ("gtin", "model") and s.price]
        if confirmed:
            price = summarize(confirmed, context.currency)
            price.verified_only = True
            price.sources = [s for s in links.purchase if s.price]
        else:
            price = summarize([s for s in links.purchase if s.price], context.currency)
            if price.sources:
                warnings.append("prices come from search snippets: no shop page confirmed this product")
        if not links.purchase:
            warnings.append("no shop listings found")
        return links, price

    @staticmethod
    def _best(result: VisionResult, extracted: Extracted, context: IdentifyContext, warnings: list[str]) -> Identification:
        item_type = result.type
        if item_type and context.allowed_types and item_type not in context.allowed_types:
            warnings.append(f"type '{item_type}' is not in allowed_types; left empty")
            item_type = None

        evidence: list[str] = []
        if extracted.barcodes:
            evidence.append(f"barcode:{len(extracted.barcodes)}")
        if extracted.ocr_text:
            evidence.append("ocr:printed-text")
        if result.name:
            evidence.append("vision")

        serial = result.serial_number
        # A serial the model "saw" but that the local reader never read is likely invented.
        if serial and extracted.ocr_text and not any(serial.replace(" ", "") in line.replace(" ", "") for line in extracted.ocr_text):
            evidence.append("serial:unconfirmed")

        gtin = find_gtin(extracted.barcodes, extracted.ocr_text)
        return Identification(
            name=result.name,
            brand=result.brand,
            model=result.model,
            type=item_type,
            gtin=gtin,
            serial_number=serial,
            description=result.description,
            confidence=_clamp(result.confidence),
            evidence=evidence,
            key=product_key(gtin, result.brand, result.model, result.name),
        )
