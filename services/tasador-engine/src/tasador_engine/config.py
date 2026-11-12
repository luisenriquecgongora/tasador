"""Settings, read once from environment variables.

Every knob has a default that works for a local run with no keys: the engine then
does only the local stages (barcode, OCR) and says in `warnings` what it skipped.

Providers are generic. Vision is either Claude (`anthropic`, through its own SDK) or
any server that speaks the OpenAI-style `/chat/completions` API with images, which
covers most providers and every common local model server; presets fill in their URL.
Web search is SearXNG, Brave Search or Claude's web search tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Vision presets for OpenAI-compatible servers: name -> base URL.
VISION_PRESETS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}
# Model used when TASADOR_VISION_MODEL is not set. Cloud presets have none on purpose:
# their model names change often, so the operator picks one.
DEFAULT_VISION_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-5-5",
    "ollama": "qwen2.5vl:7b",
}
SEARCH_PROVIDERS = ("searxng", "brave", "anthropic", "none")


def _csv(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def _env(*names: str, default: str = "") -> str:
    """First non-empty variable among `names` (later names are older spellings)."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _int(*names: str, default: int) -> int:
    raw = _env(*names)
    return int(raw) if raw else default


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "host.docker.internal") or host.endswith(".local")


@dataclass(frozen=True)
class Settings:
    # Where this copy runs; echoed in responses so a client can tell local from hosted.
    deployment: str = "self-hosted"
    # Bearer token for /v1/*. Empty means no auth (fine on localhost, never on a public host).
    api_token: str = ""
    cors_origins: list[str] = field(default_factory=list)

    # --- Vision: "anthropic", "openai-compatible", a preset name, "none" or "auto".
    vision_provider: str = "auto"
    vision_model: str = ""
    vision_base_url: str = ""  # openai-compatible only; presets fill it in
    vision_api_key: str = ""
    # Claude: output effort ("low" ... "max").
    vision_effort: str = "medium"
    # OpenAI-compatible: sent as `reasoning_effort`. Empty = automatic: "none" for local
    # servers (turns off "thinking", which makes small local models take minutes and often
    # leaves no answer), not sent for cloud APIs, which differ in what they accept.
    vision_reasoning_effort: str = ""
    # Photos sent per request. 0 = automatic: 4 for local servers (each photo costs GPU
    # memory and time), no limit for cloud APIs. Text and barcodes are read from all photos.
    vision_max_images: int = 0

    # --- Web search: "searxng", "brave", "anthropic", "none" or "auto".
    search_provider: str = "auto"
    searxng_url: str = ""
    search_api_key: str = ""  # Brave Search API key
    search_model: str = "claude-opus-5-5"  # for "anthropic" (Claude's web search tool)
    search_max_uses: int = 5
    # How many result pages to open and check per request (shops first). 0 = never open pages.
    verify_pages: int = 8
    page_timeout_seconds: int = 8

    # Shared key for both Claude roles, so one ANTHROPIC_API_KEY is enough.
    anthropic_api_key: str = ""

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
            deployment=_env("TASADOR_DEPLOYMENT", default="self-hosted"),
            api_token=_env("TASADOR_API_TOKEN"),
            cors_origins=_csv(_env("TASADOR_CORS_ORIGINS")),
            vision_provider=_env("TASADOR_VISION_PROVIDER", default="auto").lower(),
            vision_model=_env("TASADOR_VISION_MODEL", "TASADOR_OPENAI_MODEL", "TASADOR_ANTHROPIC_MODEL"),
            vision_base_url=_env("TASADOR_VISION_BASE_URL", "TASADOR_OPENAI_BASE_URL").rstrip("/"),
            vision_api_key=_env("TASADOR_VISION_API_KEY", "TASADOR_OPENAI_API_KEY"),
            vision_effort=_env("TASADOR_VISION_EFFORT", "TASADOR_ANTHROPIC_EFFORT", default="medium"),
            vision_reasoning_effort=_env("TASADOR_VISION_REASONING_EFFORT", "TASADOR_OPENAI_REASONING_EFFORT"),
            vision_max_images=_int("TASADOR_VISION_MAX_IMAGES", "TASADOR_OPENAI_MAX_IMAGES", default=0),
            search_provider=_env("TASADOR_SEARCH_PROVIDER", default="auto").lower(),
            searxng_url=_env("TASADOR_SEARXNG_URL").rstrip("/"),
            search_api_key=_env("TASADOR_SEARCH_API_KEY"),
            search_model=_env("TASADOR_SEARCH_MODEL", "TASADOR_ANTHROPIC_MODEL", default="claude-opus-5-5"),
            search_max_uses=_int("TASADOR_SEARCH_MAX_USES", default=5),
            verify_pages=_int("TASADOR_VERIFY_PAGES", default=8),
            page_timeout_seconds=_int("TASADOR_PAGE_TIMEOUT_SECONDS", default=8),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            max_images=_int("TASADOR_MAX_IMAGES", default=8),
            max_image_bytes=_int("TASADOR_MAX_IMAGE_BYTES", default=15 * 1024 * 1024),
            max_image_side=_int("TASADOR_MAX_IMAGE_SIDE", default=1568),
            allowed_image_hosts=_csv(_env("TASADOR_ALLOWED_IMAGE_HOSTS")),
            max_concurrency=_int("TASADOR_MAX_CONCURRENCY", default=2),
            request_timeout_seconds=_int("TASADOR_REQUEST_TIMEOUT_SECONDS", default=90),
        )

    # --- Resolution: what actually runs, after "auto" and presets are applied.

    def resolved_vision_provider(self) -> str:
        """"anthropic", "openai-compatible" or "none" (or an unknown name, rejected later)."""
        provider = self.vision_provider
        if provider == "auto":
            if self.vision_base_url:
                return "openai-compatible"
            return "anthropic" if self.anthropic_api_key else "none"
        return "openai-compatible" if provider in VISION_PRESETS else provider

    def vision_base(self) -> str:
        return self.vision_base_url or VISION_PRESETS.get(self.vision_provider, "")

    def resolved_vision_model(self) -> str:
        if self.vision_model:
            return self.vision_model
        if self.resolved_vision_provider() == "anthropic":
            return DEFAULT_VISION_MODELS["anthropic"]
        if self.vision_provider == "ollama" or ":11434" in self.vision_base():
            return DEFAULT_VISION_MODELS["ollama"]
        return ""

    def resolved_reasoning_effort(self) -> str:
        if self.vision_reasoning_effort:
            return "" if self.vision_reasoning_effort == "off" else self.vision_reasoning_effort
        return "none" if is_local_url(self.vision_base()) else ""

    def resolved_vision_max_images(self) -> int | None:
        if self.vision_max_images > 0:
            return self.vision_max_images
        return 4 if is_local_url(self.vision_base()) else None

    def resolved_search_provider(self) -> str:
        if self.search_provider != "auto":
            return self.search_provider
        if self.searxng_url:
            return "searxng"
        if self.search_api_key:
            return "brave"
        if self.anthropic_api_key:
            return "anthropic"
        return "none"
