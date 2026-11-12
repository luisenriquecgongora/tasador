"""The local settings page: locked to this computer, saves .env, switches providers live."""

from __future__ import annotations

import os
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tasador_engine import settings_ui
from tasador_engine.main import create_app
from tasador_engine.settings_ui import validate, write_env_file
from conftest import FakeFetcher, FakeVision


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Saving changes os.environ; put it back afterwards. No real Ollama or SearXNG calls."""
    saved = dict(os.environ)
    monkeypatch.setattr(settings_ui, "vision_models", lambda: ["qwen3.5:2b", "llava:7b"])
    monkeypatch.setattr(settings_ui, "_reachable", lambda url: True)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _client(settings, tmp_path, host="localhost"):
    env_file = tmp_path / ".env"
    env_file.write_text("# my notes\nTASADOR_VERIFY_PAGES=8\nOTHER=keep\n", encoding="utf-8")
    ui = replace(settings, settings_ui=True, env_file=str(env_file))
    app = create_app(ui, vision=FakeVision(), search=None, fetcher=FakeFetcher())
    return TestClient(app, base_url=f"http://{host}:8000"), env_file


def _key(client) -> str:
    return re.search(r'const UI_KEY = "([^"]+)"', client.get("/").text).group(1)


def test_page_is_off_unless_enabled(settings):
    app = create_app(settings, vision=None, search=None, fetcher=FakeFetcher())
    assert TestClient(app, base_url="http://localhost:8000").get("/").status_code == 404


def test_page_only_answers_localhost(settings, tmp_path):
    client, _ = _client(settings, tmp_path)
    assert client.get("/").status_code == 200
    rebinding, _ = _client(settings, tmp_path, host="attacker.example")  # DNS rebinding: foreign Host header
    assert rebinding.get("/").status_code == 403
    assert rebinding.get("/ui/state").status_code == 403


def test_state_shows_models_and_hides_keys(settings, tmp_path):
    os.environ["ANTHROPIC_API_KEY"] = "sk-secret-value"
    client, _ = _client(settings, tmp_path)
    state = client.get("/ui/state").json()
    assert state["ollama"] == {"running": True, "models": ["qwen3.5:2b", "llava:7b"]}
    assert state["secrets"]["ANTHROPIC_API_KEY"] is True
    assert "sk-secret-value" not in client.get("/ui/state").text
    assert state["active"]["vision"] == "fake-vision"


def test_saving_needs_the_page_key(settings, tmp_path):
    client, _ = _client(settings, tmp_path)
    body = {"changes": {"TASADOR_VISION_PROVIDER": "none"}}
    assert client.post("/ui/settings", json=body).status_code == 403
    assert client.post("/ui/settings", json=body, headers={"X-Tasador-UI": "guess"}).status_code == 403


def test_save_writes_env_and_switches_providers_live(settings, tmp_path):
    client, env_file = _client(settings, tmp_path)
    changes = {"TASADOR_VISION_PROVIDER": "ollama", "TASADOR_VISION_MODEL": "qwen3.5:2b",
               "TASADOR_SEARCH_PROVIDER": "searxng", "TASADOR_SEARXNG_URL": "http://localhost:8888",
               "TASADOR_VERIFY_PAGES": "4"}
    response = client.post("/ui/settings", json={"changes": changes}, headers={"X-Tasador-UI": _key(client)})
    assert response.status_code == 200, response.text
    assert response.json()["active"]["vision"] == "ollama:qwen3.5:2b"
    assert response.json()["active"]["search"] == "searxng"
    assert client.get("/v1/capabilities").json()["provider"]["vision"] == "ollama:qwen3.5:2b"  # live, no restart

    saved = env_file.read_text(encoding="utf-8")
    assert saved.startswith("# my notes\nTASADOR_VERIFY_PAGES=4\nOTHER=keep\n")  # edited in place, rest kept
    assert "TASADOR_VISION_MODEL=qwen3.5:2b" in saved


def test_invalid_settings_are_refused_and_nothing_changes(settings, tmp_path):
    client, env_file = _client(settings, tmp_path)
    before = env_file.read_text(encoding="utf-8")
    key = {"X-Tasador-UI": _key(client)}
    # a cloud preset without a model cannot work: refused before anything is written
    response = client.post("/ui/settings", json={"changes": {"TASADOR_VISION_PROVIDER": "gemini"}}, headers=key)
    assert response.status_code == 422 and "TASADOR_VISION_MODEL" in response.json()["detail"]
    assert env_file.read_text(encoding="utf-8") == before
    assert client.get("/v1/capabilities").json()["provider"]["vision"] == "fake-vision"


@pytest.mark.parametrize("changes", [
    {"TASADOR_API_TOKEN": "x"},                       # not editable from the page
    {"TASADOR_VISION_PROVIDER": "evil"},
    {"TASADOR_VISION_MODEL": "a\nTASADOR_API_TOKEN=x"},  # no line injection into .env
    {"TASADOR_VISION_BASE_URL": "file:///etc/passwd"},
    {"TASADOR_VERIFY_PAGES": "500"},
])
def test_validate_rejects(changes):
    with pytest.raises(ValueError):
        validate(changes)


def test_write_env_file_creates_and_updates(tmp_path):
    path = tmp_path / ".env"
    write_env_file(path, {"A": "1"})
    write_env_file(path, {"A": "", "B": "2"})
    assert path.read_text(encoding="utf-8") == "A=\n\nB=2\n"
