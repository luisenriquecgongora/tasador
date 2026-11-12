"""Providers against mocked HTTP: the real SDK / client parsing runs, nothing leaves the machine."""

from __future__ import annotations

import json
from dataclasses import replace

import anthropic
import httpx
import httpx2
import pytest

from tasador_engine.product_key import GoalQuery
from tasador_engine.providers.common import ProviderError, extract_json_object
from tasador_engine.providers.search import AnthropicWebSearch, SearxngSearch, build_search
from tasador_engine.providers.vision import AnthropicVision, OpenAICompatibleVision, build_vision, output_schema
from tasador_engine.schemas import Barcode, IdentifyContext
from conftest import jpeg_bytes

VISION_JSON = {
    "name": "Pioneer DJ DJM-900NXS2", "brand": "Pioneer DJ", "model": "DJM-900NXS2", "type": "mixer",
    "serial_number": None, "description": "A 4-channel DJ mixer.", "confidence": 0.9,
    "alternatives": [], "search_queries": ["Pioneer DJM-900NXS2"],
}


def _message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _anthropic(handler) -> anthropic.AsyncAnthropic:
    # The Anthropic SDK 2.x runs on httpx2, so its mock transport comes from there.
    return anthropic.AsyncAnthropic(
        api_key="test", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )


async def test_anthropic_vision_sends_images_schema_and_evidence(settings):
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=_message([{"type": "text", "text": json.dumps(VISION_JSON)}]))

    vision = AnthropicVision(settings, client=_anthropic(handler))
    context = IdentifyContext(locale="pl", allowed_types=["mixer"])
    result = await vision.identify([jpeg_bytes()], context, ["DJM-900NXS2"], [Barcode(format="EAN13", value="123", image_index=0)])

    assert result.model == "DJM-900NXS2"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["schema"]["properties"]["type"]["anyOf"][0]["enum"] == ["mixer"]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert "DJM-900NXS2" in content[-1]["text"] and "EAN13 123" in content[-1]["text"]


@pytest.mark.parametrize(
    "response, message",
    [(httpx2.Response(200, json=_message([], "refusal")), "declined"),
     (httpx2.Response(200, json=_message([{"type": "text", "text": "not json"}])), "unusable"),
     (httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}), "HTTP 529")],
)
async def test_anthropic_vision_errors(settings, response, message):
    vision = AnthropicVision(settings, client=_anthropic(lambda request: response))
    with pytest.raises(ProviderError, match=message):
        await vision.identify([jpeg_bytes()], IdentifyContext(), [], [])


async def test_anthropic_search_keeps_only_urls_it_saw(settings):
    listings = {"results": [
        {"title": "Shop A", "url": "https://shop-a.pl/djm", "goal": "new", "price": 10499, "currency": "pln"},
        {"title": "Invented", "url": "https://made-up.example/x", "goal": "new", "price": 1, "currency": "PLN"},
    ]}
    calls: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:  # first turn pauses mid-search and must be resumed
            return httpx2.Response(200, json=_message([
                {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "djm"}},
                {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                    {"type": "web_search_result", "url": "https://shop-a.pl/djm", "title": "Shop A",
                     "encrypted_content": "e", "page_age": None}]},
            ], "pause_turn"))
        return httpx2.Response(200, json=_message([{"type": "text", "text": "Here: " + json.dumps(listings)}]))

    search = AnthropicWebSearch(settings, client=_anthropic(handler))
    queries = [GoalQuery("new", "Pioneer DJM-900NXS2 cena"), GoalQuery("manual", "Pioneer DJM-900NXS2 instrukcja")]
    hits = await search.search(queries, IdentifyContext(country="pl", currency="PLN"))

    assert [(h.url, h.goal, h.price, h.currency) for h in hits] == [("https://shop-a.pl/djm", "new", 10499, "PLN")]
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["tools"][0]["user_location"] == {"type": "approximate", "country": "PL"}
    assert "manual: Pioneer DJM-900NXS2 instrukcja" in calls[0]["messages"][0]["content"]
    assert calls[1]["messages"][1]["role"] == "assistant"  # paused turn sent back


async def test_openai_compatible_vision(settings):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        reply = "```json\n" + json.dumps(VISION_JSON) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    local = replace(settings, openai_base_url="http://localhost:11434/v1")
    vision = OpenAICompatibleVision(local, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await vision.identify([jpeg_bytes()], IdentifyContext(), [], [])

    assert result.brand == "Pioneer DJ"
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["body"]["model"] == "qwen2.5vl:7b"
    assert seen["body"]["reasoning_effort"] == "none"  # thinking off by default
    assert seen["body"]["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_openai_compatible_vision_unreachable(settings):
    def handler(request):
        raise httpx.ConnectError("refused")

    local = replace(settings, openai_base_url="http://localhost:11434/v1")
    vision = OpenAICompatibleVision(local, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="cannot reach"):
        await vision.identify([jpeg_bytes()], IdentifyContext(), [], [])


async def test_searxng_runs_one_query_per_goal(settings):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        query = request.url.params["q"]
        seen.append(query)
        if query.endswith("cena sklep"):
            return httpx.Response(503)  # one failing query does not fail the search
        return httpx.Response(200, json={"results": [
            {"url": "https://allegro.pl/1", "title": "Pioneer DJM-900NXS2", "content": "6 200,00 zł"},
            {"url": "ftp://odd.example/x", "title": "skipped", "content": ""},
        ]})

    search = SearxngSearch(replace(settings, searxng_url="http://searxng:8080"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    queries = [GoalQuery("official", "Pioneer DJ DJM-900NXS2"), GoalQuery("new", "Pioneer DJ DJM-900NXS2 cena sklep"),
               GoalQuery("used", "Pioneer DJ DJM-900NXS2 używany")]
    hits = await search.search(queries, IdentifyContext(locale="pl"))

    assert sorted(seen) == sorted(q.text for q in queries)
    assert [(h.url, h.goal) for h in hits] == [("https://allegro.pl/1", "official")]  # deduplicated, ftp dropped


async def test_searxng_down_is_an_error(settings):
    search = SearxngSearch(replace(settings, searxng_url="http://searxng:8080"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(ProviderError, match="did not answer"):
        await search.search([GoalQuery("new", "x")], IdentifyContext())


def test_auto_provider_resolution(settings):
    auto = replace(settings, vision_provider="auto", search_provider="auto")
    assert build_vision(auto) is None and build_search(auto) is None
    with_key = replace(auto, anthropic_api_key="k")
    assert build_vision(with_key).name.startswith("anthropic:")
    assert build_search(with_key).name.startswith("anthropic-web-search")
    local = replace(auto, openai_base_url="http://localhost:11434/v1", searxng_url="http://s:8080")
    assert build_vision(local).name.startswith("openai-compatible")
    assert build_search(local).name == "searxng"
    with pytest.raises(ValueError):
        build_vision(replace(settings, vision_provider="openai-compatible"))


def test_schema_and_json_helpers():
    assert output_schema([])["properties"]["type"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert extract_json_object('noise {"a": 1} trailing') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("[1, 2]")
