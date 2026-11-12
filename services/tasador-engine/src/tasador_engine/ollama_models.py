"""Find an installed Ollama model that can read images.

Used by the launcher (`python -m tasador_engine.ollama_models`) so a one-click start needs
no model name: it prints the best installed vision model, or nothing. Exit code 0 when a
model was found, 1 when Ollama answers but has no vision model, 2 when Ollama is not running.
"""

from __future__ import annotations

import json
import sys
import urllib.request

OLLAMA = "http://localhost:11434"
# Preferred families, best first; anything else with the vision capability comes after.
PREFERRED = ("qwen2.5vl", "qwen3-vl", "qwen3.5", "gemma3", "minicpm-v", "llama3.2-vision", "llava")


def _post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _get(path: str) -> dict:
    with urllib.request.urlopen(OLLAMA + path, timeout=5) as response:
        return json.load(response)


def vision_models(get=_get, post=_post) -> list[str]:
    """Installed models whose capabilities include vision, preferred families first."""
    names = [m["name"] for m in get("/api/tags").get("models", []) if m.get("name")]
    found = [name for name in names if "vision" in post("/api/show", {"model": name}).get("capabilities", [])]

    def rank(name: str) -> tuple[int, str]:
        family = name.split(":")[0].lower()
        position = next((i for i, prefix in enumerate(PREFERRED) if family.startswith(prefix)), len(PREFERRED))
        return position, name

    return sorted(found, key=rank)


def main() -> int:
    try:
        models = vision_models()
    except OSError:
        return 2
    if not models:
        return 1
    print(models[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
