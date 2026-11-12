"""Start-up helpers for running on your own computer (the desktop app and Tasador.bat).

- `load_env_file`: read the .env the settings page saves to, so every launcher starts the
  engine with the same saved choices. Variables already set in the process win.
- `autodetect_local`: when nothing is configured, use what is running on this computer:
  an Ollama model that can read images, and SearXNG on its usual local address.
Both only fill gaps; anything set explicitly is left alone.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from .config import Settings
from .ollama_models import vision_models

LOCAL_SEARXNG = "http://localhost:8888"


def load_env_file(path: str | None) -> int:
    """KEY=value lines into os.environ (comments and empty values skipped). Returns how many were set."""
    if not path or not Path(path).is_file():
        return 0
    count = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key and value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def searxng_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def autodetect_local() -> list[str]:
    """Fill unset providers from local services. Returns what was chosen, for the log."""
    chosen: list[str] = []
    settings = Settings.from_env()

    vision_unset = settings.vision_provider == "auto" and not settings.vision_base_url and not settings.anthropic_api_key
    if vision_unset or (settings.vision_provider == "ollama" and not settings.vision_model):
        try:
            models = vision_models()
        except OSError:
            models = []
        if models:
            os.environ["TASADOR_VISION_PROVIDER"] = "ollama"
            os.environ.setdefault("TASADOR_VISION_MODEL", models[0])
            chosen.append(f"vision: ollama {os.environ['TASADOR_VISION_MODEL']}")

    search_unset = settings.search_provider == "auto" and not (
        settings.searxng_url or settings.search_api_key or settings.anthropic_api_key
    )
    if search_unset and searxng_reachable(LOCAL_SEARXNG):
        os.environ["TASADOR_SEARXNG_URL"] = LOCAL_SEARXNG
        chosen.append(f"search: searxng {LOCAL_SEARXNG}")
    return chosen
