"""HTTP API.

    GET  /                  local settings page (only when TASADOR_SETTINGS_UI is on)
    GET  /health            liveness, no auth
    GET  /v1/capabilities   which stages and providers this copy has
    POST /v1/identify       photos in, IdentifyResponse out

/v1/identify takes either multipart/form-data (`images` file fields, optional `context`
JSON string field) or application/json (IdentifyJsonRequest, image URLs).
"""

from __future__ import annotations

import hmac
import json
import os

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from . import __version__
from .autodetect import autodetect_local, load_env_file
from .config import Settings
from .images import HEIC_SUPPORTED, ImageError, PreparedImage, fetch, prepare
from .net import tls_context
from .pages import PageFetcher
from .pipeline import Pipeline
from .providers.search import SearchProvider, build_search
from .providers.vision import VisionProvider, build_vision
from .schemas import Capabilities, IdentifyContext, IdentifyJsonRequest, IdentifyResponse
from .settings_ui import add_settings_ui

_FROM_SETTINGS = object()

# The handler reads the raw request (it accepts two content types), so FastAPI cannot
# infer the body. Describing it here gives /docs an upload form for trying the engine.
_IDENTIFY_REQUEST_DOCS = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["images"],
                    "properties": {
                        "images": {"type": "array", "items": {"type": "string", "format": "binary"}},
                        "context": {
                            "type": "string",
                            "description": 'Optional JSON, e.g. {"currency": "PLN", "country": "PL"}',
                        },
                    },
                }
            },
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": "Image URLs instead of files (only from TASADOR_ALLOWED_IMAGE_HOSTS)",
                    "example": {"images": [{"url": "https://images.example.com/a.jpg"}], "context": {"currency": "PLN"}},
                }
            },
        },
    }
}


def create_app(
    settings: Settings | None = None,
    vision: VisionProvider | None | object = _FROM_SETTINGS,
    search: SearchProvider | None | object = _FROM_SETTINGS,
    http_client: httpx.AsyncClient | None = None,
    fetcher: PageFetcher | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    pipeline = Pipeline(
        settings,
        build_vision(settings) if vision is _FROM_SETTINGS else vision,
        build_search(settings) if search is _FROM_SETTINGS else search,
        fetcher,
    )
    image_client = http_client or httpx.AsyncClient(timeout=30, verify=tls_context())

    app = FastAPI(title="Tasador engine", version=__version__)
    # The settings page can swap providers while the engine runs, so handlers read the
    # current pipeline from app.state instead of keeping their own copy.
    app.state.pipeline = pipeline
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    def require_token(request: Request) -> None:
        if not settings.api_token:
            return
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(token.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/capabilities", response_model=Capabilities, dependencies=[Depends(require_token)])
    async def capabilities() -> Capabilities:
        return Capabilities(
            engine_version=__version__,
            provider=app.state.pipeline.provider_info(),
            heic=HEIC_SUPPORTED,
            url_images=bool(settings.allowed_image_hosts),
            max_images=settings.max_images,
            max_image_bytes=settings.max_image_bytes,
        )

    @app.post(
        "/v1/identify",
        response_model=IdentifyResponse,
        dependencies=[Depends(require_token)],
        openapi_extra=_IDENTIFY_REQUEST_DOCS,
    )
    async def identify(request: Request) -> IdentifyResponse:
        content_type = request.headers.get("content-type", "")
        try:
            if content_type.startswith("multipart/form-data"):
                images, context = await _from_multipart(request, settings)
            elif content_type.startswith("application/json"):
                images, context = await _from_json(request, settings, image_client)
            else:
                raise HTTPException(status_code=415, detail="send multipart/form-data or application/json")
        except ImageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=json.loads(exc.json(include_url=False))) from exc
        return await app.state.pipeline.run(images, context)

    if settings.settings_ui:
        add_settings_ui(app)
    return app


def _check_count(count: int, settings: Settings) -> None:
    if count == 0:
        raise ImageError("send at least one image")
    if count > settings.max_images:
        raise ImageError(f"send at most {settings.max_images} images")


async def _from_multipart(request: Request, settings: Settings) -> tuple[list[PreparedImage], IdentifyContext]:
    form = await request.form(max_files=settings.max_images + 1)
    files = [value for key, value in form.multi_items() if key == "images" and isinstance(value, UploadFile)]
    _check_count(len(files), settings)
    raw_context = form.get("context")
    if isinstance(raw_context, str) and raw_context.strip():
        context = IdentifyContext.model_validate_json(raw_context)
    else:
        context = IdentifyContext()
    images = []
    for index, upload in enumerate(files):
        data = await upload.read(settings.max_image_bytes + 1)
        images.append(prepare(data, settings, upload.filename or f"image {index + 1}"))
    return images, context


async def _from_json(
    request: Request, settings: Settings, client: httpx.AsyncClient
) -> tuple[list[PreparedImage], IdentifyContext]:
    body = IdentifyJsonRequest.model_validate_json(await request.body())
    _check_count(len(body.images), settings)
    images = []
    for item in body.images:
        images.append(prepare(await fetch(item.url, settings, client), settings, item.url))
    return images, body.context


def app_from_env() -> FastAPI:
    """Entry point for uvicorn: `uvicorn tasador_engine.main:app_from_env --factory`.

    Reads TASADOR_ENV_FILE first (the file the settings page saves to). With
    TASADOR_AUTODETECT on (desktop app, Tasador.bat), unset providers are filled from
    what runs on this computer: an Ollama vision model and a local SearXNG."""
    load_env_file(os.environ.get("TASADOR_ENV_FILE"))
    if os.environ.get("TASADOR_AUTODETECT", "").lower() in ("1", "true", "yes"):
        for choice in autodetect_local():
            print(f"[tasador] using {choice}", flush=True)
    return create_app()
