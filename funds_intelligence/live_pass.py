"""Production `ResearchPass` adapter — the ONLY place Funds Intelligence touches
the live provider stack.

It translates the existing, proven production pass into the core `ResearchPass`
contract. It deliberately owns **no** browsing logic: search, page fetching, the
function-calling loop, per-stage tool budgets, novelty/early-stop and quota
handling all stay inside `src.model_router` / `src.web_tools`. This module only
maps types and harvests telemetry.

    research_core.ResearchPass          ← contract
        ↑ implements
    funds_intelligence.LiveResearchPass ← THIS module (translation only)
        │ calls
        ▼
    src.model_router.collect()          ← provider + tool-loop orchestration
        └─ src.web_tools (web_search / fetch_url / SourceLog / quota)

Nothing here is imported by `research_core` (the core stays provider-free and
offline); the Funds controller receives a pass object and never knows which
implementation it got.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from research_core import ResearchPassResult

# Production stack — imported here and nowhere else in Funds Intelligence.
from src import model_router as mr
from src import web_tools as wt
from src.api_runner import _error_category          # reuse the proven taxonomy

# The first live milestone is pinned to this pair; no provider fallback.
LIVE_MODEL_MODE = "deepseek"
LIVE_SEARCH_PROVIDER = "tavily"


class ProviderMismatch(RuntimeError):
    """Raised instead of silently running on an unintended provider."""


# ── grounding adapter ────────────────────────────────────────────────────────
class SourceLogGrounding:
    """Maps a production `web_tools.SourceLog` onto the core `Grounding`
    protocol.

    A wrapper is required, not a subclass: `SourceLog.seen` / `.fetched` are
    dicts, while `Grounding.seen()` / `.fetched()` are methods — the names
    collide. URL comparison reuses `web_tools._norm` so grounding matches the
    production semantics exactly (domain+path, no scheme/query/www)."""

    def __init__(self, log=None):
        self.log = log

    # -- write-through (protocol completeness; the tool loop normally fills these)
    def seen(self, url: str) -> None:
        if self.log is not None and url:
            self.log.seen[wt._norm(url)] = url

    def fetched(self, url: str, text: str) -> None:
        if self.log is not None and url:
            n = wt._norm(url)
            self.log.seen[n] = url
            self.log.fetched[n] = text or ""

    # -- queries used by Funds validation
    def has_source(self, url: str) -> bool:
        if self.log is None or not url:
            return False
        return wt._norm(url) in self.log.seen

    def supports_value(self, value: str) -> bool:
        """True when the value text actually appears in a page this pass fetched
        — the check that stops a fabricated AUM/fund size from being recorded as
        confirmed."""
        v = (value or "").strip()
        if self.log is None or not v:
            return False
        return any(v in (text or "") for text in self.log.fetched.values())

    # -- telemetry passthrough
    @property
    def stats(self) -> dict:
        return dict(getattr(self.log, "stats", {}) or {})

    @property
    def tool_calls(self) -> int:
        return int(getattr(self.log, "tool_calls", 0) or 0)

    def urls_seen(self) -> int:
        return len(getattr(self.log, "seen", {}) or {})

    def pages_fetched(self) -> int:
        return len(getattr(self.log, "fetched", {}) or {})


# ── usage / failure records (persisted into events.jsonl by the controller) ──
@dataclass
class PassUsage:
    """What one pass actually consumed. Values come from the production
    SourceLog counters — never estimated here."""
    engine: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    searches: int = 0
    fetches: int = 0
    search_denied: int = 0
    search_rate_limited: int = 0
    search_errors: int = 0
    urls_seen: int = 0
    pages_fetched: int = 0
    seconds: float = 0.0

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def as_event(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v}
        d["tokens"] = self.tokens
        return d


@dataclass
class PassFailure:
    category: str = ""          # timeout | stream | quota | budget | parse | provider | other
    message: str = ""           # URL-masked
    spend: dict = field(default_factory=dict)   # tokens/tool calls burned for nothing


def _mask(ex: Exception) -> str:
    import re
    return re.sub(r"https?://\S+", "‹url›", f"{type(ex).__name__}: {ex}")[:300]


# ── the adapter ──────────────────────────────────────────────────────────────
class LiveResearchPass:
    """`ResearchPass` backed by `model_router.collect()`.

    One instance = one Funds run's research passes (it is stateless apart from
    accumulated telemetry). Every call:
      1. resets the thread-local SourceLog (a failure must never inherit the
         previous pass's grounding);
      2. delegates the ENTIRE browse/extract loop to `collect()`;
      3. harvests the SourceLog into grounding + usage;
      4. classifies any exception with the production taxonomy.

    `on_event(action, detail)` is passed straight through to `collect()` so live
    tool activity ("searching"/"reading"/"writing") is observable.
    """

    #: the controller's paid-work guard duck-types on this flag
    costs_money = True

    def __init__(self, *, stage: str = "funds_research", budget: int | None = None,
                 allow_extend: bool = False, max_tokens: int = 16000,
                 on_event=None, enforce_providers: bool = True):
        self.stage = stage
        self.budget = budget
        self.allow_extend = allow_extend      # first live run: fixed exposure
        self.max_tokens = max_tokens
        self.on_event = on_event
        self.enforce_providers = enforce_providers
        self.calls = 0
        self.usages: list[PassUsage] = []
        self.failures: list[PassFailure] = []
        self.last_failure: PassFailure | None = None

    # -- safety: never run on an unintended provider -----------------------
    def check_providers(self) -> dict:
        """Verify the configured provider pair WITHOUT making a call."""
        import os
        mode = mr.MODE
        provider = (os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
        info = {"model_mode": mode, "model": mr.DEEPSEEK_MODEL,
                "search_provider": provider}
        if self.enforce_providers:
            if mode != LIVE_MODEL_MODE:
                raise ProviderMismatch(
                    f"AGENT_MODE is «{mode}» — the first live Funds run is pinned to "
                    f"«{LIVE_MODEL_MODE}» (no provider fallback).")
            if provider != LIVE_SEARCH_PROVIDER:
                raise ProviderMismatch(
                    f"SEARCH_PROVIDER is «{provider}» — the first live Funds run is "
                    f"pinned to «{LIVE_SEARCH_PROVIDER}» (no provider fallback).")
        return info

    # -- the contract ------------------------------------------------------
    def run_pass(self, system: str, user: str, *, budget: int | None = None) -> ResearchPassResult:
        import time
        self.check_providers()
        self.calls += 1
        t0 = time.time()

        mr.reset_source_log()      # never inherit a previous pass's grounding
        try:
            text, engine = mr.collect(
                system, user,
                max_tokens=self.max_tokens,
                on_event=self.on_event,
                budget=budget if budget is not None else self.budget,
                allow_extend=self.allow_extend)
        except Exception as ex:                       # noqa: BLE001 — classify, don't swallow
            log = mr.get_source_log()
            fail = PassFailure(category=_error_category(ex), message=_mask(ex),
                               spend=_spend_of(log))
            self.failures.append(fail)
            self.last_failure = fail
            raise

        log = mr.get_source_log()
        usage = _usage_of(log, engine=engine, seconds=time.time() - t0)
        self.usages.append(usage)
        return ResearchPassResult(text=text, grounding=SourceLogGrounding(log),
                                  engine=engine, tokens=usage.tokens)

    # -- aggregate telemetry ----------------------------------------------
    def total_usage(self) -> PassUsage:
        tot = PassUsage(engine=self.usages[-1].engine if self.usages else "")
        for u in self.usages:
            for k in ("tokens_in", "tokens_out", "tool_calls", "searches", "fetches",
                      "search_denied", "search_rate_limited", "search_errors",
                      "urls_seen", "pages_fetched"):
                setattr(tot, k, getattr(tot, k) + getattr(u, k))
            tot.seconds += u.seconds
        return tot


def _usage_of(log, engine: str, seconds: float) -> PassUsage:
    stats = dict(getattr(log, "stats", {}) or {})
    g = SourceLogGrounding(log)
    return PassUsage(
        engine=engine,
        tokens_in=stats.get("tokens_in", 0), tokens_out=stats.get("tokens_out", 0),
        tool_calls=int(getattr(log, "tool_calls", 0) or 0),
        searches=stats.get("searches", 0), fetches=stats.get("fetches", 0),
        search_denied=stats.get("search_denied", 0),
        search_rate_limited=stats.get("search_rate_limited", 0),
        search_errors=stats.get("search_errors", 0),
        urls_seen=g.urls_seen(), pages_fetched=g.pages_fetched(),
        seconds=round(seconds, 2))


def _spend_of(log) -> dict:
    """Tokens/tool calls burned by a FAILED pass (mirrors api_runner._fail_stats)."""
    if log is None:
        return {}
    stats = {k: v for k, v in (getattr(log, "stats", {}) or {}).items() if v}
    tc = int(getattr(log, "tool_calls", 0) or 0)
    return {"tool_calls": tc, **stats} if (tc or stats) else {}


# ── zero-call configuration report (used by Preview) ─────────────────────────
def preflight() -> dict:
    """Report the CONFIGURED live setup without making any request. Key presence
    is reported as a boolean only — never a value."""
    import os
    provider = (os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
    return {
        "model_mode": mr.MODE,
        "model": mr.DEEPSEEK_MODEL,
        "model_key_present": bool(mr.DEEPSEEK_API_KEY),
        "search_provider": provider,
        "search_key_present": bool(os.environ.get("TAVILY_API_KEY")
                                   or os.environ.get("SEARCH_API_KEY")),
        "provider_fallback": False,
        "concurrency": mr.company_concurrency(),
        "budget_extend_default": mr._budget_extend(),
        "pricing_configured": bool(os.environ.get("TOKEN_PRICE_IN")
                                   and os.environ.get("TOKEN_PRICE_OUT")),
        "quota_flag": bool(getattr(wt, "QUOTA_EXHAUSTED", False)),
    }
