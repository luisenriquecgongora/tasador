from __future__ import annotations

import os

import pytest

from tasador_engine import autodetect
from tasador_engine.autodetect import autodetect_local, load_env_file

KEYS = ("TASADOR_VISION_PROVIDER", "TASADOR_VISION_MODEL", "TASADOR_VISION_BASE_URL", "ANTHROPIC_API_KEY",
        "TASADOR_SEARCH_PROVIDER", "TASADOR_SEARXNG_URL", "TASADOR_SEARCH_API_KEY", "TASADOR_TEST_A", "TASADOR_TEST_B")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    for key in KEYS:
        os.environ.pop(key, None)


def test_load_env_file_fills_gaps_only(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# comment\nTASADOR_TEST_A=from-file\nTASADOR_TEST_B=\nTASADOR_VISION_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("TASADOR_VISION_MODEL", "process-model")
    assert load_env_file(str(path)) == 1
    assert os.environ["TASADOR_TEST_A"] == "from-file"
    assert "TASADOR_TEST_B" not in os.environ  # empty values are skipped
    assert os.environ["TASADOR_VISION_MODEL"] == "process-model"  # the process wins
    assert load_env_file(str(tmp_path / "missing.env")) == 0


def test_autodetect_uses_local_services_when_nothing_is_set(monkeypatch):
    monkeypatch.setattr(autodetect, "vision_models", lambda: ["qwen3.5:2b"])
    monkeypatch.setattr(autodetect, "searxng_reachable", lambda url: True)
    assert autodetect_local() == ["vision: ollama qwen3.5:2b", "search: searxng http://localhost:8888"]
    assert os.environ["TASADOR_VISION_PROVIDER"] == "ollama"
    assert os.environ["TASADOR_SEARXNG_URL"] == "http://localhost:8888"


def test_autodetect_leaves_explicit_choices_alone(monkeypatch):
    monkeypatch.setattr(autodetect, "vision_models", lambda: ["qwen3.5:2b"])
    monkeypatch.setattr(autodetect, "searxng_reachable", lambda url: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert autodetect_local() == []
    assert "TASADOR_VISION_PROVIDER" not in os.environ


def test_autodetect_without_local_services(monkeypatch):
    def no_ollama():
        raise OSError("connection refused")

    monkeypatch.setattr(autodetect, "vision_models", no_ollama)
    monkeypatch.setattr(autodetect, "searxng_reachable", lambda url: False)
    assert autodetect_local() == []
