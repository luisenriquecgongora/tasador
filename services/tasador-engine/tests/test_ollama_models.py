from __future__ import annotations

from tasador_engine.ollama_models import vision_models


def test_picks_installed_vision_models_preferred_first():
    tags = {"models": [{"name": "llama3:8b"}, {"name": "llava:7b"}, {"name": "qwen3.5:2b"}, {"name": "nomic-embed-text"}]}
    capabilities = {
        "llama3:8b": ["completion"],
        "llava:7b": ["completion", "vision"],
        "qwen3.5:2b": ["completion", "vision", "tools", "thinking"],
        "nomic-embed-text": ["embedding"],
    }
    models = vision_models(get=lambda path: tags, post=lambda path, body: {"capabilities": capabilities[body["model"]]})
    assert models == ["qwen3.5:2b", "llava:7b"]


def test_no_vision_model():
    assert vision_models(get=lambda path: {"models": [{"name": "llama3:8b"}]},
                         post=lambda path, body: {"capabilities": ["completion"]}) == []
