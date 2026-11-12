"""Web search providers: goal queries in, raw search hits out.

Providers only search. Sorting hits into official / reference / purchase links
(links.py) and checking pages (pages.py) happen afterwards, the same for every provider.

- SearxngSearch: a self-hosted SearXNG meta-search instance. No API key, no model.
- BraveSearch: the Brave Search API (its own web index). Needs an API key, no model.
- AnthropicWebSearch: Claude with the server-side web search tool. Hits whose URL did not
  appear in the actual search results are dropped, so every hit is a page that exists.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import SEARCH_PROVIDERS, Settings
from ..net import anthropic_client, tls_context
from ..product_key import Goal, GoalQuery
from ..schemas import IdentifyContext
from .common import ProviderError, extract_json_object

RESULTS_PER_QUERY = 8
MAX_PAUSE_RESUMES = 3


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    goal: Goal
    # Filled by providers that already read a price (Claude); SearXNG leaves them empty.
    price: float | None = None
    currency: str | None = None


class SearchProvider(Protocol):
    name: str

    async def search(self, queries: list[GoalQuery], context: IdentifyContext) -> list[SearchHit]: ...


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    unique = []
    for hit in hits:
        if hit.url not in seen:
            seen.add(hit.url)
            unique.append(hit)
    return unique


class SearxngSearch:
    """Needs `search.formats: [html, json]` in the SearXNG settings.yml."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.base_url = settings.searxng_url
        self.name = "searxng"
        self._client = client or httpx.AsyncClient(timeout=30, verify=tls_context())

    async def _one(self, query: GoalQuery, context: IdentifyContext) -> list[SearchHit] | None:
        params = {"q": query.text, "format": "json", "language": context.locale}
        try:
            response = await self._client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            results = response.json().get("results", [])
        except (httpx.HTTPError, ValueError):
            return None
        hits = []
        for result in results[:RESULTS_PER_QUERY]:
            url, title = result.get("url"), (result.get("title") or "").strip()
            if url and title and url.startswith(("http://", "https://")):
                hits.append(SearchHit(url=url, title=title, snippet=(result.get("content") or "").strip(), goal=query.goal))
        return hits

    async def search(self, queries, context) -> list[SearchHit]:
        answers = await asyncio.gather(*(self._one(q, context) for q in queries))
        if queries and all(answer is None for answer in answers):
            raise ProviderError(f"web search failed: SearXNG at {self.base_url} did not answer")
        return _dedupe([hit for answer in answers if answer for hit in answer])


class BraveSearch:
    """https://api.search.brave.com - key in TASADOR_SEARCH_API_KEY."""

    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.name = "brave"
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.search_api_key}
        self._client = client or httpx.AsyncClient(timeout=20, headers=headers, verify=tls_context())
        if client is not None:
            self._client.headers.update(headers)

    async def _one(self, query: GoalQuery, context: IdentifyContext) -> list[SearchHit] | None:
        params: dict = {"q": query.text, "count": RESULTS_PER_QUERY, "search_lang": context.locale.split("-")[0]}
        if context.country:
            params["country"] = context.country.upper()
        try:
            response = await self._client.get(self.URL, params=params)
            response.raise_for_status()
            results = (response.json().get("web") or {}).get("results", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return [
            SearchHit(url=r["url"], title=r.get("title", "").strip(), snippet=(r.get("description") or "").strip(), goal=query.goal)
            for r in results[:RESULTS_PER_QUERY]
            if r.get("url", "").startswith(("http://", "https://")) and r.get("title")
        ]

    async def search(self, queries, context) -> list[SearchHit]:
        answers = await asyncio.gather(*(self._one(q, context) for q in queries))
        if queries and all(answer is None for answer in answers):
            raise ProviderError("web search failed: Brave Search did not answer (check TASADOR_SEARCH_API_KEY)")
        return _dedupe([hit for answer in answers if answer for hit in answer])


class _Result(BaseModel):
    title: str
    url: str
    goal: Literal["official", "manual", "new", "used"] = "new"
    price: float | None = None
    currency: str | None = None


class _Results(BaseModel):
    results: list[_Result] = Field(default_factory=list)


def _claude_prompt(queries: list[GoalQuery], context: IdentifyContext) -> str:
    where = f" Prefer shops and pages from country {context.country}." if context.country else ""
    money = f" Prefer prices in {context.currency}." if context.currency else ""
    lines = "\n".join(f"- {q.goal}: {q.text}" for q in queries)
    return (
        "Search the web for one product. Goals and suggested queries:\n"
        f"{lines}\n"
        "official = the manufacturer's own product page; manual = manuals, specifications and reviews; "
        "new = shops selling it new; used = marketplaces selling it used. "
        f"Only pages about this exact model, not accessories or spare parts.{where}{money}\n"
        "When you are done, reply with only a JSON object of this shape and nothing else:\n"
        '{"results": [{"title": "...", "url": "...", "goal": "official" | "manual" | "new" | "used", '
        '"price": 123.45 or null, "currency": "ISO 4217 code" or null}]}\n'
        "At most 20 results, each a page you saw in the search results, with its exact URL."
    )


class AnthropicWebSearch:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.model = settings.search_model
        self.effort = "medium"
        self.max_uses = settings.search_max_uses
        self.name = f"anthropic-web-search:{self.model}"
        self._client = client or anthropic_client(settings.anthropic_api_key or None, settings.request_timeout_seconds)

    async def search(self, queries, context) -> list[SearchHit]:
        tool: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_uses}
        if context.country:
            tool["user_location"] = {"type": "approximate", "country": context.country.upper()}
        messages: list[dict] = [{"role": "user", "content": _claude_prompt(queries, context)}]
        seen_urls: set[str] = set()

        for _ in range(MAX_PAUSE_RESUMES + 1):
            try:
                response = await self._client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    messages=messages,
                    tools=[tool],
                    output_config={"effort": self.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"web search failed: HTTP {exc.status_code}") from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError("web search failed: cannot reach the Anthropic API") from exc

            for block in response.content:
                # A failed search returns an error object instead of a list of results.
                if block.type == "web_search_tool_result" and isinstance(block.content, list):
                    seen_urls.update(result.url for result in block.content if getattr(result, "url", None))
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            raise ProviderError("web search was declined")
        if response.stop_reason == "pause_turn":
            raise ProviderError("web search did not finish")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            parsed = _Results.model_validate(extract_json_object(text))
        except (ValueError, ValidationError) as exc:
            raise ProviderError("web search returned unusable output") from exc

        return _dedupe([
            SearchHit(url=r.url, title=r.title, snippet="", goal=r.goal, price=r.price,
                      currency=r.currency.upper() if r.currency else None)
            for r in parsed.results
            if r.url in seen_urls
        ])


def build_search(settings: Settings) -> SearchProvider | None:
    provider = settings.resolved_search_provider()
    if provider == "anthropic":
        return AnthropicWebSearch(settings)
    if provider == "searxng":
        if not settings.searxng_url:
            raise ValueError("TASADOR_SEARCH_PROVIDER=searxng needs TASADOR_SEARXNG_URL")
        return SearxngSearch(settings)
    if provider == "brave":
        if not settings.search_api_key:
            raise ValueError("TASADOR_SEARCH_PROVIDER=brave needs TASADOR_SEARCH_API_KEY")
        return BraveSearch(settings)
    if provider == "none":
        return None
    raise ValueError(f"unknown TASADOR_SEARCH_PROVIDER: {provider} (choose one of: {', '.join(SEARCH_PROVIDERS)}, auto)")
