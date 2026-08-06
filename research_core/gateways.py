"""Retrieval and model gateways — thin contracts the core depends on instead of
concrete providers, plus deterministic offline adapters for tests.

The PRODUCTION adapters already exist in the company code and are NOT imported
here (that would pull provider SDKs, `.env` loading, and paid-request surface
into the core and break offline isolation):

    ModelGateway      → wrap src.model_router  (collect / verify)
    RetrievalGateway  → wrap src.web_tools     (web_search / fetch_url)
    Grounding         → wrap src.web_tools.SourceLog

A future pack constructs the production adapter and passes it in; the core and
its tests use the Null/InMemory adapters below and make no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ── value objects ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    text: str
    ok: bool = True


@dataclass(frozen=True)
class ModelResult:
    text: str
    tokens: int = 0
    raw: object = None


# ── contracts ─────────────────────────────────────────────────────────────────
@runtime_checkable
class ModelGateway(Protocol):
    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> ModelResult: ...


@runtime_checkable
class RetrievalGateway(Protocol):
    def search(self, query: str, n: int = 5) -> list[SearchResult]: ...
    def fetch(self, url: str) -> FetchedPage: ...


@runtime_checkable
class Grounding(Protocol):
    """A per-attempt log of what a research pass actually saw/fetched, so a
    claimed source or value can be verified against real retrieval (the company
    SourceLog contract, abstracted)."""
    def seen(self, url: str) -> None: ...
    def fetched(self, url: str, text: str) -> None: ...
    def has_source(self, url: str) -> bool: ...
    def supports_value(self, value: str) -> bool: ...


# ── deterministic offline adapters (for tests / stubs) ────────────────────────
class NullModelGateway:
    """Never calls a model. Returns a fixed, deterministic string so a pipeline
    can be exercised offline without any provider."""
    def __init__(self, reply: str = "{}"):
        self.reply = reply
        self.calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> ModelResult:
        self.calls += 1
        return ModelResult(text=self.reply, tokens=0)


class NullRetrievalGateway:
    """No network. Search returns nothing; fetch returns an empty, not-ok page."""
    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        return []

    def fetch(self, url: str) -> FetchedPage:
        return FetchedPage(url=url, text="", ok=False)


@dataclass
class InMemorySourceLog:
    """A minimal, deterministic Grounding for tests: records URLs and page text
    seen this attempt, and checks source/value containment against them."""
    _seen: set = field(default_factory=set)
    _pages: dict = field(default_factory=dict)

    def seen(self, url: str) -> None:
        self._seen.add(url)

    def fetched(self, url: str, text: str) -> None:
        self._seen.add(url)
        self._pages[url] = text or ""

    def has_source(self, url: str) -> bool:
        return url in self._seen

    def supports_value(self, value: str) -> bool:
        v = (value or "").strip()
        return bool(v) and any(v in text for text in self._pages.values())
