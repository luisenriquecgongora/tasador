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
import re
import time
from datetime import date

from . import local_readers
from .activity import GLOBAL_ACTIVITY
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
    PriceRange,
    ProviderInfo,
)


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 3)


def _extract_fallback_name_brand(ocr_text: list[str]) -> tuple[str | None, str | None, str | None, float]:
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    conf = 0.0

    potential_names: list[str] = []
    found_table = False

    for line in ocr_text:
        s = line.strip()
        if not s:
            continue
        # Check brand
        if not brand:
            m = re.search(r"(?:producent|marka|brand|maker|manufacturer)[:\s]+(.+)", s, re.I)
            if m and m.group(1).strip():
                brand = m.group(1).strip()
            elif re.search(r"\bikea\b", s, re.I):
                brand = "IKEA"
            elif re.search(r"\bpioneer(?:\s*dj)?\b", s, re.I):
                brand = "Pioneer DJ"

        # Check model
        if not model:
            m = re.search(r"(?:model|typ|model\s*nr)[:\s]+(.+)", s, re.I)
            if m and m.group(1).strip():
                model = m.group(1).strip()

        # Check explicit item name
        if not name:
            m = re.search(r"(?:nazwa\s*(?:towaru|przedmiotu|uslugi)?|item|product|przedmiot|towar|artykul)[:\s]+(.+)", s, re.I)
            if m and m.group(1).strip():
                name = m.group(1).strip()

        if re.match(r"^produkt$", s, re.I):
            found_table = True
        elif found_table and not name and len(s) > 5:
            if (
                not re.match(r"^(?:nr:)?f/\d+", s, re.I)
                and not re.match(r"^(?:cena|brutto|netto|vat|stawka|polska|suma|ilosc|wartosc|szt|podatki|twoje|nabywca|sprzedawca)", s, re.I)
                and not re.search(r"sp[.\s]*z[.\s]*o[.\s]*o|s\.a\.|nabywca|buyer", s, re.I)
                and not re.match(r"^\d+([.,]\d+)?$", s)
            ):
                potential_names.append(s)

    if not name and potential_names:
        name = potential_names[0]
    if name:
        conf = 0.5
    return name, brand, model, conf


def _extract_fallback_price(ocr_text: list[str], default_currency: str = "PLN") -> Price | None:
    for i, line in enumerate(ocr_text):
        s = line.strip()
        m = re.search(
            r"(?:wartosc\s*brutto|kwota\s*brutto|suma\s*brutto|brutto|gross|razem(?:\s*brutto)?|total|zap[lłt]acono|zaplacono|do\s*zap[lłt]aty)[:\s]+([0-9\s.,]+)\s*([A-Za-z]{3})?",
            s,
            re.I,
        )
        if m:
            raw_num = m.group(1).replace(" ", "").replace(",", ".")
            try:
                num = float(raw_num)
                curr = m.group(2).upper() if m.group(2) else default_currency
                return Price(currency=curr, new=PriceRange(min=num, median=num, max=num, count=1))
            except ValueError:
                pass
        elif re.search(r"(?:suma\s*brutto|wartosc\s*brutto|razem\s*brutto|zap[lłt]acono)", s, re.I):
            for j in range(i + 1, min(i + 4, len(ocr_text))):
                nxt = ocr_text[j].strip()
                if "%" in nxt:
                    continue
                nxt_m = re.match(r"^([0-9\s.,]+)(?:\s*([A-Za-z]{3}))?$", nxt)
                if nxt_m:
                    try:
                        num = float(nxt_m.group(1).replace(" ", "").replace(",", "."))
                        curr = nxt_m.group(2).upper() if nxt_m.group(2) else default_currency
                        return Price(currency=curr, new=PriceRange(min=num, median=num, max=num, count=1))
                    except ValueError:
                        pass
    return None


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
        run_record = GLOBAL_ACTIVITY.start_run(images, context)

        GLOBAL_ACTIVITY.add_step(
            run_id=run_record.id,
            step_id="input",
            title="Input Photos & Parameters",
            status="ok",
            duration_ms=0,
            inputs={"image_count": len(images), "context": context.model_dump()},
            outputs={"images": run_record.input_summary.get("images", [])},
        )

        try:
            async with self._slots:
                t = time.perf_counter()
                extracted, per_photo_text = await asyncio.to_thread(_read_locally, images)
                local_read_time = int((time.perf_counter() - t) * 1000)
                timings["local_read"] = local_read_time

                # Step 2: Barcodes
                GLOBAL_ACTIVITY.add_step(
                    run_id=run_record.id,
                    step_id="barcodes",
                    title="ZXing Barcode Detection",
                    status="ok" if extracted.barcodes else "skipped",
                    duration_ms=local_read_time // 2,
                    inputs={"images_scanned": len(images)},
                    outputs={
                        "count": len(extracted.barcodes),
                        "barcodes": [b.model_dump() for b in extracted.barcodes],
                    },
                    details=f"Found {len(extracted.barcodes)} barcode(s)" if extracted.barcodes else "No barcodes detected",
                )

                # Step 3: OCR
                GLOBAL_ACTIVITY.add_step(
                    run_id=run_record.id,
                    step_id="ocr",
                    title="RapidOCR Text Extraction",
                    status="ok" if extracted.ocr_text else "skipped",
                    duration_ms=local_read_time - (local_read_time // 2),
                    inputs={"images_scanned": len(images)},
                    outputs={
                        "total_lines": len(extracted.ocr_text),
                        "lines": extracted.ocr_text,
                        "per_photo": per_photo_text,
                    },
                    details=f"Extracted {len(extracted.ocr_text)} text line(s)" if extracted.ocr_text else "No text detected",
                )

                result = VisionResult()
                if self.vision is None:
                    warnings.append("no vision provider configured: only barcodes and printed text were read")
                    GLOBAL_ACTIVITY.add_step(
                        run_id=run_record.id,
                        step_id="vision",
                        title="Multimodal Vision AI",
                        status="skipped",
                        duration_ms=0,
                        inputs={"provider": None},
                        outputs={},
                        warnings=["no vision provider configured"],
                    )
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
                    ocr_lines = labelled_text(per_photo_text, sent)
                    t = time.perf_counter()
                    vision_warns = []
                    try:
                        result = await self.vision.identify(
                            [images[i].jpeg for i in sent], context, ocr_lines, barcodes
                        )
                    except ProviderError as exc:
                        warnings.append(str(exc))
                        vision_warns.append(str(exc))
                    vision_time = int((time.perf_counter() - t) * 1000)
                    timings["vision"] = vision_time
                    if not result.same_item:
                        warnings.append("the photos seem to show different items: the result describes the most prominent one")

                    GLOBAL_ACTIVITY.add_step(
                        run_id=run_record.id,
                        step_id="vision",
                        title=f"Multimodal Vision AI ({self.vision.name})",
                        status="error" if vision_warns else ("ok" if result.name else "warning"),
                        duration_ms=vision_time,
                        inputs={
                            "provider": self.vision.name,
                            "photos_sent": sent,
                            "labelled_ocr_count": len(ocr_lines),
                            "barcodes_count": len(barcodes),
                        },
                        outputs=result.model_dump(),
                        warnings=vision_warns,
                        details=f"Identified '{result.name}' ({int(result.confidence * 100)}% conf)" if result.name else "Identification uncertain",
                    )

            best = self._best(result, extracted, context, warnings)
            alternatives = [
                Alternative(name=a.name, brand=a.brand, model=a.model, confidence=_clamp(a.confidence))
                for a in result.alternatives[:3]
            ]

            links, price = Links(), Price(currency=context.currency)
            if not price.new and extracted.ocr_text:
                fb_price = _extract_fallback_price(extracted.ocr_text, context.currency or "PLN")
                if fb_price:
                    price = fb_price
            if context.search_prices and best.key:
                if self.search is None:
                    warnings.append("no web search provider configured: no links or prices")
                    GLOBAL_ACTIVITY.add_step(
                        run_id=run_record.id,
                        step_id="search",
                        title="Web Search & Market Pricing",
                        status="skipped",
                        duration_ms=0,
                        inputs={"key": best.key.model_dump() if best.key else None},
                        outputs={},
                        warnings=["no search provider configured"],
                    )
                else:
                    links, price = await self._search_and_verify(best, result, context, warnings, timings)
                    search_time = timings.get("search", 0) + timings.get("verify", 0)
                    GLOBAL_ACTIVITY.add_step(
                        run_id=run_record.id,
                        step_id="search",
                        title=f"Web Search & Market Pricing ({self.search.name})",
                        status="ok" if (links.purchase or links.official) else "warning",
                        duration_ms=search_time,
                        inputs={
                            "provider": self.search.name,
                            "product_key": best.key.model_dump() if best.key else None,
                            "currency": context.currency,
                        },
                        outputs={
                            "official_links_count": len(links.official),
                            "reference_links_count": len(links.reference),
                            "purchase_offers_count": len(links.purchase),
                            "price": price.model_dump(),
                        },
                        details=f"{len(links.purchase)} offer(s) found" if links.purchase else "No shop listings found",
                    )
            elif not context.search_prices:
                GLOBAL_ACTIVITY.add_step(
                    run_id=run_record.id,
                    step_id="search",
                    title="Web Search & Market Pricing",
                    status="skipped",
                    duration_ms=0,
                    inputs={"search_prices": False},
                    outputs={},
                    details="Web search disabled in request context",
                )

            total_ms = int((time.perf_counter() - started) * 1000)
            timings["total"] = total_ms

            response = IdentifyResponse(
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

            GLOBAL_ACTIVITY.add_step(
                run_id=run_record.id,
                step_id="output",
                title="Synthesized Output Entity",
                status="ok",
                duration_ms=0,
                inputs={},
                outputs={
                    "name": best.name,
                    "brand": best.brand,
                    "model": best.model,
                    "confidence": best.confidence,
                    "gtin": best.gtin,
                    "evidence": best.evidence,
                    "price_summary": price.model_dump(),
                },
                details=f"Finished in {total_ms}ms with {int(best.confidence * 100)}% confidence",
            )
            GLOBAL_ACTIVITY.complete_run(run_record.id, response, total_ms, self.provider_info().model_dump())
            return response

        except Exception as exc:
            total_ms = int((time.perf_counter() - started) * 1000)
            GLOBAL_ACTIVITY.fail_run(run_record.id, str(exc), total_ms)
            raise

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

        links = sort_hits(hits, best.brand, best.model if best.key.kind != "name" else None)
        if self.settings.verify_pages > 0 and (links.purchase or links.official):
            t = time.perf_counter()
            _, page_gtins = await check_links(links, best.key, best.model, self.fetcher, self.settings.verify_pages)
            timings["verify"] = int((time.perf_counter() - t) * 1000)
            # A barcode number listed by the pages that matched this model fills a missing GTIN,
            # but only when they all agree (a variant's page could list a different one).
            if not best.gtin and len(page_gtins) == 1:
                best.gtin = page_gtins.pop().lstrip("0").zfill(13)
                best.evidence.append("gtin:from-shop-page")

        today = date.today().isoformat()
        for listing in links.purchase:
            listing.seen_at = today

        offers = [s for s in links.purchase if s.page == "offer" and s.price]
        confirmed = [s for s in offers if s.verified in ("gtin", "model")]
        if confirmed:
            price = summarize(confirmed, context.currency)
            price.verified_only = True
            price.sources = offers
        else:
            price = summarize(offers, context.currency)
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

        name = result.name
        brand = result.brand
        model = result.model
        conf = result.confidence

        evidence: list[str] = []
        if extracted.barcodes:
            evidence.append(f"barcode:{len(extracted.barcodes)}")
        if extracted.ocr_text:
            evidence.append("ocr:printed-text")
        if result.name:
            evidence.append("vision")
        elif extracted.ocr_text:
            fb_name, fb_brand, fb_model, fb_conf = _extract_fallback_name_brand(extracted.ocr_text)
            if fb_name:
                name = fb_name
                brand = brand or fb_brand
                model = model or fb_model
                conf = fb_conf
                evidence.append("ocr:document-heuristics")

        serial = result.serial_number
        # A serial the model "saw" but that the local reader never read is likely invented.
        if serial and extracted.ocr_text and not any(serial.replace(" ", "") in line.replace(" ", "") for line in extracted.ocr_text):
            evidence.append("serial:unconfirmed")

        gtin = find_gtin(extracted.barcodes, extracted.ocr_text)
        return Identification(
            name=name,
            brand=brand,
            model=model,
            type=item_type,
            gtin=gtin,
            serial_number=serial,
            description=result.description,
            confidence=_clamp(conf),
            evidence=evidence,
            key=product_key(gtin, brand, model, name),
        )
