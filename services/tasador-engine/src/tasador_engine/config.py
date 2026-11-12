"""Settings, read once from environment variables.

Every knob has a default that works for a local run with no keys: the engine then
does only the local stages (barcode, OCR) and says in `warnings` what it skipped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _csv(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # Where this copy runs; echoed in responses so a client can tell local from hosted.
    deployment: str = "self-hosted"
    # Bearer token for /v1/*. Empty means no auth (fine on localhost, never on a public host).
    api_token: str = ""
    cors_origins: list[str] = field(default_factory=list)

    # Vision provider: "anthropic", "openai-compatible" (Ollama, LM Studio, vLLM...), "none" or "auto".
    vision_provider: str = "auto"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "qwen2.5vl:7b"
    # Sent as `reasoning_effort`. "none" turns off the "thinking" of models like Qwen3.5,
    # which otherwise makes small local models slow (minutes) and often leaves no answer.
    # Empty string: not sent, for servers that reject the field.
    openai_reasoning_effort: str = "none"
    # Photos sent to a local model per request (each one costs GPU memory and time).
    # Barcodes and printed text are still read from every photo. 0 = no limit.
    openai_max_images: int = 4

    # Price search provider: "anthropic" (web search tool), "searxng", "none" or "auto".
    search_provider: str = "auto"
    searxng_url: str = ""
    search_max_uses: int = 5
    # How many result pages to open and check per request (shops first). 0 = never open pages.
    verify_pages: int = 8
    page_timeout_seconds: int = 8

    # Image limits.
    max_images: int = 8
    max_image_bytes: int = 15 * 1024 * 1024
    max_image_side: int = 1568
    # Host suffixes that image URLs may come from. Empty disables URL input entirely.
    allowed_image_hosts: list[str] = field(default_factory=list)

    max_concurrency: int = 2
    request_timeout_seconds: int = 90

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            deployment=os.getenv("TASADOR_DEPLOYMENT", "self-hosted").strip() or "self-hosted",
            api_token=os.getenv("TASADOR_API_TOKEN", "").strip(),
            cors_origins=_csv(os.getenv("TASADOR_CORS_ORIGINS", "")),
            vision_provider=os.getenv("TASADOR_VISION_PROVIDER", "auto").strip().lower(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            anthropic_model=os.getenv("TASADOR_ANTHROPIC_MODEL", "claude-opus-5-5").strip(),
            anthropic_effort=os.getenv("TASADOR_ANTHROPIC_EFFORT", "medium").strip(),
            openai_base_url=os.getenv("TASADOR_OPENAI_BASE_URL", "").strip().rstrip("/"),
            openai_api_key=os.getenv("TASADOR_OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("TASADOR_OPENAI_MODEL", "qwen2.5vl:7b").strip(),
            openai_reasoning_effort=os.getenv("TASADOR_OPENAI_REASONING_EFFORT", "none").strip(),
            openai_max_images=_int("TASADOR_OPENAI_MAX_IMAGES", 4),
            search_provider=os.getenv("TASADOR_SEARCH_PROVIDER", "auto").strip().lower(),
            searxng_url=os.getenv("TASADOR_SEARXNG_URL", "").strip().rstrip("/"),
            search_max_uses=_int("TASADOR_SEARCH_MAX_USES", 5),
            verify_pages=_int("TASADOR_VERIFY_PAGES", 8),
            page_timeout_seconds=_int("TASADOR_PAGE_TIMEOUT_SECONDS", 8),
            max_images=_int("TASADOR_MAX_IMAGES", 8),
            max_image_bytes=_int("TASADOR_MAX_IMAGE_BYTES", 15 * 1024 * 1024),
            max_image_side=_int("TASADOR_MAX_IMAGE_SIDE", 1568),
            allowed_image_hosts=_csv(os.getenv("TASADOR_ALLOWED_IMAGE_HOSTS", "")),
            max_concurrency=_int("TASADOR_MAX_CONCURRENCY", 2),
            request_timeout_seconds=_int("TASADOR_REQUEST_TIMEOUT_SECONDS", 90),
        )

    def resolved_vision_provider(self) -> str:
        if self.vision_provider != "auto":
            return self.vision_provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_base_url:
            return "openai-compatible"
        return "none"

    def resolved_search_provider(self) -> str:
        if self.search_provider != "auto":
            return self.search_provider
        if self.searxng_url:
            return "searxng"
        if self.anthropic_api_key:
            return "anthropic"
        return "none"
