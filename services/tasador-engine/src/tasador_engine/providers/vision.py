"""Vision providers: photos + locally read evidence -> structured identification.

Two implementations behind one interface:
- AnthropicVision: Claude through the Anthropic SDK, output constrained by a JSON schema.
- OpenAICompatibleVision: any `/v1/chat/completions` server that accepts images
  (Ollama, LM Studio, vLLM, a desktop proxy). This is the local / offline path.
"""

from __future__ import annotations

import base64
import json
from typing import Protocol

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import DEFAULT_VISION_MODELS, VISION_PRESETS, Settings
from ..net import anthropic_client, tls_context
from ..schemas import Barcode, IdentifyContext
from .common import ProviderError, extract_json_object


class VisionAlternative(BaseModel):
    name: str
    brand: str | None = None
    model: str | None = None
    confidence: float = 0.0


class VisionResult(BaseModel):
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    type: str | None = None
    serial_number: str | None = None
    description: str | None = None
    confidence: float = 0.0
    alternatives: list[VisionAlternative] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    same_item: bool = True


class VisionProvider(Protocol):
    name: str
    # Most photos one call can take; None = no limit. The pipeline picks which ones to send.
    max_images: int | None

    async def identify(
        self, jpegs: list[bytes], context: IdentifyContext, ocr_text: list[str], barcodes: list[Barcode]
    ) -> VisionResult: ...


SYSTEM_PROMPT = """You identify physical items (equipment, devices, furniture, tools, instruments) from photos, for an inventory record.

All photos in one request show the same single item from different angles: for example the front, the back, a side, and a close-up of a label or nameplate. Combine what every photo shows into one identification; a label visible in only one photo still applies to the whole item. Set same_item to false only when the photos clearly show different items, and then identify the most prominent one.

Evidence comes in two kinds. Printed text and barcodes were read from the photos by local software; they are more reliable than a visual guess for brand, model number and serial number, but they can contain reading errors and unrelated text. The photos themselves show shape, colour, controls and logos.

Fill the fields as follows:
- name: a short catalog-style name, e.g. "Pioneer DJ DJM-900NXS2 4-channel DJ mixer". If the brand is unknown, describe the item generically, e.g. "Black folding wooden chair".
- brand, model: only when visible in the photos or in the read text, or when the design is unmistakable. Otherwise null. Never invent a model number.
- serial_number: only when it is read from a label. Otherwise null.
- type: one of the allowed types when any are given and one fits; otherwise null.
- description: one sentence on what the item is and its visible condition.
- confidence: from 0 to 1, how sure you are of brand and model together (or of the generic name when there is no brand).
- alternatives: up to 3 other plausible identifications, most likely first. Empty when the item is clearly identified.
- search_queries: 1 to 3 short queries that would find this exact product in online shops, written for the given locale."""

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def output_schema(allowed_types: list[str]) -> dict:
    type_schema = (
        {"anyOf": [{"type": "string", "enum": allowed_types}, {"type": "null"}]} if allowed_types else _NULLABLE_STRING
    )
    alternative = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "brand": _NULLABLE_STRING,
            "model": _NULLABLE_STRING,
            "confidence": {"type": "number"},
        },
        "required": ["name", "brand", "model", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "name": _NULLABLE_STRING,
            "brand": _NULLABLE_STRING,
            "model": _NULLABLE_STRING,
            "type": type_schema,
            "serial_number": _NULLABLE_STRING,
            "description": _NULLABLE_STRING,
            "confidence": {"type": "number"},
            "alternatives": {"type": "array", "items": alternative},
            "search_queries": {"type": "array", "items": {"type": "string"}},
            "same_item": {"type": "boolean"},
        },
        "required": [
            "name", "brand", "model", "type", "serial_number",
            "description", "confidence", "alternatives", "search_queries", "same_item",
        ],
        "additionalProperties": False,
    }


def evidence_text(context: IdentifyContext, ocr_text: list[str], barcodes: list[Barcode], photo_count: int = 1) -> str:
    lines = [f"Photos: {photo_count}, all of the same item" if photo_count > 1 else "Photos: 1", f"Locale: {context.locale}"]
    if context.hint:
        lines.append(f"User hint: {context.hint}")
    if context.allowed_types:
        lines.append("Allowed types: " + ", ".join(context.allowed_types))
    if barcodes:
        lines.append("Barcodes read: " + "; ".join(f"{b.format} {b.value} ({f'photo {b.image_index + 1}' if b.image_index >= 0 else 'a photo not shown'})" for b in barcodes))
    if ocr_text:
        lines.append("Printed text read from the photos (one line each, prefixed with the photo it came from):")
        lines.extend(f"- {line}" for line in ocr_text[:80])
    else:
        lines.append("No printed text was read from the photos.")
    lines.append("Identify the one item shown in the photos.")
    return "\n".join(lines)


def _parse(raw: str) -> VisionResult:
    try:
        return VisionResult.model_validate(extract_json_object(raw))
    except (ValueError, ValidationError) as exc:
        raise ProviderError(f"vision model returned unusable output ({type(exc).__name__})") from exc


class AnthropicVision:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.model = settings.vision_model or DEFAULT_VISION_MODELS["anthropic"]
        self.effort = settings.vision_effort
        self.name = f"anthropic:{self.model}"
        self.max_images = settings.vision_max_images or None
        self._client = client or anthropic_client(
            settings.vision_api_key or settings.anthropic_api_key or None, settings.request_timeout_seconds
        )

    async def identify(self, jpegs, context, ocr_text, barcodes) -> VisionResult:
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(j).decode()}}
            for j in jpegs
        ]
        content.append({"type": "text", "text": evidence_text(context, ocr_text, barcodes, len(jpegs))})
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": output_schema(context.allowed_types)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"vision request failed: HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("vision request failed: cannot reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            raise ProviderError("vision model declined the request")
        if response.stop_reason == "max_tokens":
            raise ProviderError("vision model output was cut off")
        text = "".join(block.text for block in response.content if block.type == "text")
        return _parse(text)


class OpenAICompatibleVision:
    """Any `/chat/completions` server that accepts images: OpenAI, Gemini, OpenRouter, Groq,
    Mistral, Together, xAI, Ollama, LM Studio, vLLM... (see VISION_PRESETS)."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.base_url = settings.vision_base()
        self.model = settings.resolved_vision_model()
        self.reasoning_effort = settings.resolved_reasoning_effort()
        self.max_images = settings.resolved_vision_max_images()
        label = settings.vision_provider if settings.vision_provider in VISION_PRESETS else "openai-compatible"
        self.name = f"{label}:{self.model}"
        headers = {"Authorization": f"Bearer {settings.vision_api_key}"} if settings.vision_api_key else {}
        self._client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds, headers=headers, verify=tls_context()
        )

    async def identify(self, jpegs, context, ocr_text, barcodes) -> VisionResult:
        schema = json.dumps(output_schema(context.allowed_types))
        user_content: list[dict] = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(j).decode()}}
            for j in jpegs
        ]
        user_content.append({
            "type": "text",
            "text": evidence_text(context, ocr_text, barcodes, len(jpegs))
            + "\n\nAnswer with only a JSON object that matches this JSON schema:\n" + schema,
        })
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        try:
            response = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"vision request failed: cannot reach {self.base_url}") from exc
        if response.status_code != 200:
            raise ProviderError(f"vision request failed: HTTP {response.status_code}")
        try:
            text = response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("vision server returned an unexpected response shape") from exc
        return _parse(text)


def build_vision(settings: Settings) -> VisionProvider | None:
    provider = settings.resolved_vision_provider()
    if provider == "anthropic":
        return AnthropicVision(settings)
    if provider == "openai-compatible":
        if not settings.vision_base():
            raise ValueError("TASADOR_VISION_PROVIDER=openai-compatible needs TASADOR_VISION_BASE_URL")
        if not settings.resolved_vision_model():
            raise ValueError(f"set TASADOR_VISION_MODEL to the vision model to use with {settings.vision_provider}")
        return OpenAICompatibleVision(settings)
    if provider == "none":
        return None
    choices = ", ".join(["anthropic", "openai-compatible", *VISION_PRESETS, "none", "auto"])
    raise ValueError(f"unknown TASADOR_VISION_PROVIDER: {provider} (choose one of: {choices})")
