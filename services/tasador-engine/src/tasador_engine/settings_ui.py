"""Local settings page: pick the recognition and search providers, models and keys,
see what is running, and try a photo, without editing files.

    GET  /                  the page (static/settings.html)
    GET  /ui/state          what is active, what is installed, current non-secret values
    POST /ui/settings       save changes to .env and switch providers immediately

Only enabled when TASADOR_SETTINGS_UI is on (the Windows launcher turns it on). Because
saving can change where photos are sent, every route is locked to this computer:
- the connection must come from the loopback address;
- the Host header must be localhost or 127.0.0.1 (stops DNS rebinding, where a website
  makes its own domain point at 127.0.0.1);
- saving needs a random key that is generated at start and only written into the page,
  which other websites cannot read (and the custom header forces a CORS preflight that
  only configured origins pass).
Saved keys are never sent back to the page; it only learns whether each one is set.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from dataclasses import replace
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .autodetect import searxng_reachable as _reachable
from .config import SEARCH_PROVIDERS, VISION_PRESETS, Settings
from .ollama_models import vision_models
from .pipeline import Pipeline
from .providers.search import build_search
from .providers.vision import build_vision

SECRETS = ("TASADOR_VISION_API_KEY", "ANTHROPIC_API_KEY", "TASADOR_SEARCH_API_KEY")
VALUES = (
    "TASADOR_VISION_PROVIDER", "TASADOR_VISION_MODEL", "TASADOR_VISION_BASE_URL", "TASADOR_VISION_EFFORT",
    "TASADOR_VISION_MAX_IMAGES", "TASADOR_SEARCH_PROVIDER", "TASADOR_SEARXNG_URL", "TASADOR_VERIFY_PAGES",
)
VISION_CHOICES = ("auto", "anthropic", "openai-compatible", *VISION_PRESETS, "none")
SEARCH_CHOICES = ("auto", *SEARCH_PROVIDERS)
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
DEFAULT_SEARXNG = "http://localhost:8888"
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "testclient"}  # "testclient": FastAPI's test client
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SettingsChange(BaseModel):
    """Variables to change. A missing key stays as it is; "" clears it."""

    changes: dict[str, str]


def _host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _require_local(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK_CLIENTS or _host_name(request.headers.get("host", "")) not in _LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="the settings page only answers this computer")


def validate(changes: dict[str, str]) -> dict[str, str]:
    """Reject anything the page should not be able to set. Returns cleaned values."""
    clean: dict[str, str] = {}
    for key, raw in changes.items():
        value = raw.strip()
        if key not in VALUES and key not in SECRETS:
            raise ValueError(f"{key} cannot be changed here")
        if len(value) > 400 or re.search(r"[\r\n\0]", value):
            raise ValueError(f"{key}: value is too long or has line breaks")
        if key == "TASADOR_VISION_PROVIDER" and value and value not in VISION_CHOICES:
            raise ValueError(f"vision provider must be one of: {', '.join(VISION_CHOICES)}")
        if key == "TASADOR_SEARCH_PROVIDER" and value and value not in SEARCH_CHOICES:
            raise ValueError(f"search provider must be one of: {', '.join(SEARCH_CHOICES)}")
        if key == "TASADOR_VISION_EFFORT" and value not in EFFORTS:
            raise ValueError(f"effort must be one of: {', '.join(e for e in EFFORTS if e)}")
        if key in ("TASADOR_VISION_BASE_URL", "TASADOR_SEARXNG_URL") and value and not value.startswith(("http://", "https://")):
            raise ValueError(f"{key} must start with http:// or https://")
        if key in ("TASADOR_VERIFY_PAGES", "TASADOR_VISION_MAX_IMAGES") and value:
            limit = 20 if key == "TASADOR_VERIFY_PAGES" else 8
            if not value.isdigit() or int(value) > limit:
                raise ValueError(f"{key} must be a whole number from 0 to {limit}")
        clean[key] = value
    return clean


def write_env_file(path: Path, changes: dict[str, str]) -> None:
    """Update KEY=value lines in place, keep comments and other lines, append new keys."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(changes)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and "=" in line and key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _state(app: FastAPI, settings: Settings) -> dict:
    try:
        ollama = {"running": True, "models": vision_models()}
    except OSError:
        ollama = {"running": False, "models": []}
    searxng_url = os.environ.get("TASADOR_SEARXNG_URL") or DEFAULT_SEARXNG
    return {
        "active": app.state.pipeline.provider_info().model_dump(),
        "values": {key: os.environ.get(key, "") for key in VALUES},
        "secrets": {key: bool(os.environ.get(key)) for key in SECRETS},
        "ollama": ollama,
        "searxng": {"url": searxng_url, "reachable": _reachable(searxng_url)},
        "presets": list(VISION_PRESETS),
        "env_file": settings.env_file or None,
    }


def add_settings_ui(app: FastAPI) -> None:
    ui_key = secrets.token_urlsafe(24)
    base_settings: Settings = app.state.pipeline.settings
    page = resources.files("tasador_engine").joinpath("static/settings.html").read_text(encoding="utf-8")

    @app.get("/", include_in_schema=False)
    async def settings_page(request: Request) -> HTMLResponse:
        _require_local(request)
        return HTMLResponse(page.replace("__UI_KEY__", ui_key), headers={"Cache-Control": "no-store"})

    @app.get("/ui/state", include_in_schema=False)
    async def ui_state(request: Request) -> dict:
        _require_local(request)
        return _state(app, base_settings)

    @app.post("/ui/settings", include_in_schema=False)
    async def ui_save(request: Request, body: SettingsChange) -> dict:
        _require_local(request)
        if not hmac.compare_digest(request.headers.get("x-tasador-ui", ""), ui_key):
            raise HTTPException(status_code=403, detail="reload the settings page and try again")
        try:
            changes = validate(body.changes)
            planned = {**os.environ, **changes}
            for key, value in changes.items():
                if not value:
                    planned.pop(key, None)
            # Same .env file and page switch as at start; everything else from the planned variables.
            settings = replace(Settings.from_env(planned), settings_ui=True, env_file=base_settings.env_file)
            vision, search = build_vision(settings), build_search(settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if settings.env_file:
            write_env_file(Path(settings.env_file), changes)
        for key, value in changes.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        app.state.pipeline = Pipeline(settings, vision, search, app.state.pipeline.fetcher)
        return _state(app, settings)
