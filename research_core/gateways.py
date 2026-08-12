"""Retrieval and model gateways — thin contracts the core depends on instead of
concrete providers, plus deterministic offline adapters for tests.

The PRODUCTION adapters already exist in the company code and are NOT imported
here (that would pull provider SDKs, `.env` loading, and paid-request surface
into the core and break offline isolation):

    ResearchPass      → wrap src.model_router.collect  (browse+extract+ground pass)
    ModelGateway      → wrap src.model_router.verify   (no-browse model call)
    RetrievalGateway  → wrap src.web_tools             (pack-driven search/fetch)
    Grounding         → wrap src.web_tools.SourceLog

A future pack constructs the production adapter and passes it in; the core and
its tests use the Null/Scripted adapters below and make no network calls.

Why THREE model-facing contracts, not one:
  * `collect()` in the company code is a *single high-level research pass* — for
    server-side providers (gpt/claude/grok) the model browses itself; for
    DeepSeek the app runs a full search→fetch→budget→SourceLog tool loop. It
    returns model text AND the grounding log. That whole orchestration lives
    inside the provider, so the reusable seam is `ResearchPass.run_pass(...)`,
    NOT `ModelGateway.complete()` + `RetrievalGateway.search()` (which would make
    a pack re-own the tool loop — see RESEARCH_CORE_HANDOFF §gateway boundary).
  * `ModelGateway.complete()` remains the seam for a *no-browse* model call (the
    company `verify` path).
  * `RetrievalGateway` remains the seam for a pack that drives its OWN searches
    (e.g. building a landscape deterministically from a registry).
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


@dataclass(frozen=True)
class ResearchPassResult:
    """The output of one research pass: the model's raw text (to be JSON-parsed
    by the pack), the `Grounding` produced during the pass (so the pack can
    reject unsupported claims), the engine label, and token usage. Mirrors the
    company `collect()` return `(text, grounding_log, engine)`."""
    text: str
    grounding: "Grounding | None" = None
    engine: str = ""
    tokens: int = 0


# ── contracts ─────────────────────────────────────────────────────────────────
@runtime_checkable
class ResearchPass(Protocol):
    """A single provider-owned browse→extract→ground pass. The provider decides
    whether/how to browse; the pack supplies the task (system+user) and consumes
    text + grounding. `budget` shapes an app-side tool budget where applicable
    (DeepSeek); server-side providers ignore it."""
    def run_pass(self, system: str, user: str, *, budget: "int | None" = None) -> ResearchPassResult: ...


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


class ScriptedResearchPass:
    """Deterministic offline `ResearchPass`. Each call pops the next scripted
    step: a `(reply_text, pages)` pair, where `pages` is `{url: text}` the pass
    'fetched'. It returns the reply plus an `InMemorySourceLog` populated from
    those pages, so a pack's grounding checks run against real (fixture) source
    text with no network. Exhausting the script yields an empty, ungrounded
    pass (models don't run out of turns; tests shouldn't over-call silently)."""

    def __init__(self, steps: "list[tuple[str, dict]]", engine: str = "scripted"):
        self._steps = list(steps)
        self._i = 0
        self.engine = engine
        self.calls = 0

    def run_pass(self, system: str, user: str, *, budget=None) -> ResearchPassResult:
        self.calls += 1
        log = InMemorySourceLog()
        if self._i >= len(self._steps):
            return ResearchPassResult(text="{}", grounding=log, engine=self.engine)
        reply, pages = self._steps[self._i]
        self._i += 1
        for url, text in (pages or {}).items():
            log.fetched(url, text)
        return ResearchPassResult(text=reply, grounding=log, engine=self.engine)


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
