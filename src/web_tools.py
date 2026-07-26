"""
App-provided web tools for engines with NO server-side browsing (DeepSeek).

The gpt/claude/grok engines browse through their providers' own web_search
tools; DeepSeek's API has none, so the app supplies the research capability
itself via client-side function calling:

  web_search(query)  — a search API (Brave by default; SEARCH_PROVIDER selects
                       the backend, SEARCH_API_KEY authenticates).
  fetch_url(url)     — GET + visible-text extraction, truncated to fit the
                       model's context. Failures return an error object instead
                       of raising, so the model can try an alternative source
                       (the prompts teach the rusprofile fallback when
                       bo.nalog.ru fails).

SourceLog records every URL the tools actually saw, and check_grounding()
enforces the same anti-fabrication contract the other engines get from
server-side search: a `source` the model never encountered this session is
stripped, which makes the EXISTING ingest gate reject the field as
'unsourced' and routes it into the EXISTING repair loop — no new gate codes.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_PAGE_CHARS = 10_000
DEFAULT_COUNT = 8
MAX_COUNT = 20

# Provider selection is OPERATOR-controlled and never automatic: the active
# provider is `SEARCH_PROVIDER` (brave | tavily), defaulting to brave. There is
# no fallback and no key-based switching — a run stays on the chosen provider.
KNOWN_PROVIDERS = ("brave", "tavily")


# ── search error taxonomy ─────────────────────────────────────────────────────
class SearchError(RuntimeError):
    """Base for search failures. `category` is the compact telemetry label;
    `http_status`/`retry_after` are filled where the provider gave them."""

    category = "error"

    def __init__(self, message: str = "", *, http_status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


class SearchQuotaExhausted(SearchError):
    """Explicit quota / credit exhaustion (Brave → HTTP 402). STICKY: sets the
    process-wide flag so the run degrades instead of burning model tokens."""
    category = "quota"


class SearchRateLimited(SearchError):
    """Transient rate limit (HTTP 429). NOT sticky — bounded retry/backoff
    applies, and a persistent 429 is reported as a rate limit, never as
    permanent quota exhaustion."""
    category = "rate_limit"


class SearchAuthError(SearchError):
    """Authentication failure (HTTP 401/403) — a bad or unauthorized key."""
    category = "auth"


class SearchConfigError(SearchError):
    """Configuration failure — missing key or unknown provider (no HTTP made)."""
    category = "config"


class SearchTimeout(SearchError):
    """Timeout or network failure reaching the provider."""
    category = "timeout"


class SearchMalformed(SearchError):
    """The provider replied but the body was not the expected JSON shape."""
    category = "malformed"


# Sticky per-process flag: once the quota/credit is gone every further
# web_search call fails instantly (no HTTP, no retries) — the run degrades
# explicitly instead of burning model tokens on doomed searches. Only true
# quota exhaustion (402) sets it; transient 429s do NOT. apply_env() /
# reset_quota_flag() clears it (new or upgraded key).
QUOTA_EXHAUSTED = False

# Bounded retry for transient rate limits (429), reused by every backend. Kept
# small; honours Retry-After when present, capped. Overridable for tests.
SEARCH_RETRY_MAX = 2
RETRY_BASE_SECONDS = 1.0
RETRY_CAP_SECONDS = 8.0

# Optional compact-telemetry sink: callable(SearchTelemetry) -> None. When set,
# web_search reports one SearchTelemetry per call. Left None in production (so
# events.jsonl is untouched); the harness/diagnostics set it to collect. A
# sink must never raise into the search path.
SEARCH_TELEMETRY_SINK = None


def reset_quota_flag() -> None:
    global QUOTA_EXHAUSTED
    QUOTA_EXHAUSTED = False


@dataclass
class SearchTelemetry:
    """One search call, summarised for diagnostics/comparison — counts and
    categories only, NEVER the query text, result bodies, keys, or headers."""
    provider: str
    outcome: str                     # ok | empty | error
    latency_ms: int
    results: int = 0
    error_category: str = ""         # "" for ok/empty; else rate_limit/quota/…
    http_status: int | None = None
    retry_after_s: float | None = None
    retries: int = 0
    retry_delay_s: float = 0.0
    quota_state: bool = False        # QUOTA_EXHAUSTED after this call
    # not serialised — lets web_search re-raise the exact production exception
    error_exc: BaseException | None = field(default=None, repr=False)

    def public_dict(self) -> dict:
        """JSON-safe view (drops the exception handle); safe to persist."""
        return {"provider": self.provider, "outcome": self.outcome,
                "latency_ms": self.latency_ms, "results": self.results,
                "error_category": self.error_category,
                "http_status": self.http_status,
                "retry_after_s": self.retry_after_s, "retries": self.retries,
                "retry_delay_s": round(self.retry_delay_s, 3),
                "quota_state": self.quota_state}


def _secret_values() -> list[str]:
    return [v for v in (os.environ.get("SEARCH_API_KEY", ""),
                        os.environ.get("BRAVE_API_KEY", ""),
                        os.environ.get("TAVILY_API_KEY", "")) if v]


def redact(text: str) -> str:
    """Mask any configured key value or key-shaped token so it can never reach
    a log, telemetry field, or error string."""
    s = str(text)
    for secret in _secret_values():
        if secret:
            s = s.replace(secret, "‹redacted-key›")
    s = re.sub(r"tvly-[A-Za-z0-9]+", "‹redacted-key›", s)
    s = re.sub(r"(?i)(x-subscription-token|authorization|api[_-]?key)"
               r"\s*[:=]\s*\S+", r"\1: ‹redacted›", s)
    return s


def _provider_key(provider: str) -> str:
    """Key for a provider: its specific var (BRAVE_API_KEY / TAVILY_API_KEY) if
    set, else the shared SEARCH_API_KEY. The per-provider vars let the harness
    exercise both providers at once; production needs only SEARCH_API_KEY."""
    specific = os.environ.get(f"{provider.upper()}_API_KEY", "")
    return specific or os.environ.get("SEARCH_API_KEY", "")


def require_search_key(provider: str | None = None) -> str:
    """The active provider's key, or a clear error. Called upfront by the
    DeepSeek tools loop (fail fast, before any model tokens are spent)."""
    prov = (provider or os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
    key = _provider_key(prov)
    if not key:
        raise SearchConfigError(
            f"No search key for provider «{prov}» — DeepSeek quantitative "
            f"research runs on the app's own web_search tool, which needs a "
            f"search API key. Set it in Settings → «Search API key» "
            f"({prov.upper()}_API_KEY or SEARCH_API_KEY), or pick ChatGPT / "
            f"Claude / Grok for this step.")
    return key


def _clamp_count(count) -> int:
    try:
        return max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_COUNT


def _retry_after_seconds(resp) -> float | None:
    raw = (resp.headers or {}).get("Retry-After") if resp is not None else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))          # delta-seconds form
    except (TypeError, ValueError):
        return None                          # HTTP-date form: ignore, use backoff


def _raise_for_status(resp, provider: str) -> None:
    """Map a non-2xx search response onto the taxonomy. 402 → sticky quota;
    429 → transient rate limit (never sticky); 401/403 → auth; other → error."""
    code = resp.status_code
    if code == 402:
        global QUOTA_EXHAUSTED
        QUOTA_EXHAUSTED = True
        raise SearchQuotaExhausted(
            f"{provider} search quota/credits exhausted (HTTP 402)",
            http_status=402)
    if code == 429:
        raise SearchRateLimited(f"{provider} rate limited (HTTP 429)",
                                http_status=429,
                                retry_after=_retry_after_seconds(resp))
    if code in (401, 403):
        raise SearchAuthError(f"{provider} authentication failed (HTTP {code}) "
                              f"— check the API key", http_status=code)
    if code >= 400:
        raise SearchError(f"{provider} HTTP {code}", http_status=code)


def _search_brave(query: str, count: int) -> list[dict]:
    key = require_search_key("brave")
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search",
                         params={"q": query, "count": count},
                         headers={"X-Subscription-Token": key,
                                  "Accept": "application/json", "User-Agent": _UA},
                         timeout=20)
    except requests.Timeout as ex:
        raise SearchTimeout(f"brave request timed out: {type(ex).__name__}")
    except requests.RequestException as ex:
        t = SearchTimeout(f"brave network error: {type(ex).__name__}")
        t.category = "network"
        raise t
    _raise_for_status(r, "brave")
    try:
        items = ((r.json().get("web") or {}).get("results") or [])[:count]
    except (ValueError, AttributeError, TypeError) as ex:
        raise SearchMalformed(f"brave response not valid JSON: {type(ex).__name__}")
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("description", "")} for i in items]


def _search_tavily(query: str, count: int) -> list[dict]:
    """Tavily Search — controlled/reproducible: basic depth, no auto params, no
    generated answer, no raw page content, the existing result limit. The key
    travels in the Authorization header (never the body), so no secret is ever
    part of a payload we might log."""
    key = require_search_key("tavily")
    body = {"query": query, "search_depth": "basic", "auto_parameters": False,
            "include_answer": False, "include_raw_content": False,
            "max_results": count}
    try:
        r = requests.post("https://api.tavily.com/search", json=body,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json",
                                   "User-Agent": _UA},
                          timeout=20)
    except requests.Timeout as ex:
        raise SearchTimeout(f"tavily request timed out: {type(ex).__name__}")
    except requests.RequestException as ex:
        t = SearchTimeout(f"tavily network error: {type(ex).__name__}")
        t.category = "network"
        raise t
    _raise_for_status(r, "tavily")
    try:
        items = (r.json().get("results") or [])[:count]
    except (ValueError, AttributeError, TypeError) as ex:
        raise SearchMalformed(f"tavily response not valid JSON: {type(ex).__name__}")
    # normalise to the SAME internal contract Brave uses: {title, url, snippet}
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("content", "")} for i in items
            if isinstance(i, dict)]


_SEARCH_PROVIDERS = {"brave": _search_brave, "tavily": _search_tavily}


def resolve_provider(provider: str | None = None) -> str:
    prov = (provider or os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
    if prov not in _SEARCH_PROVIDERS:
        raise SearchConfigError(
            f"unknown SEARCH_PROVIDER «{prov}» — known: "
            f"{', '.join(sorted(_SEARCH_PROVIDERS))}")
    return prov


def search_with_telemetry(query: str, count: int = DEFAULT_COUNT,
                          provider: str | None = None
                          ) -> tuple[list[dict], SearchTelemetry]:
    """Run one search and return (results, telemetry). NEVER raises for a
    search-level failure — the outcome is captured in the telemetry (with the
    original exception attached as `error_exc` for callers that must re-raise).
    Bounded retry/backoff is applied to transient rate limits only. Does NOT
    consult the sticky quota flag (that gate lives in web_search); the harness
    uses this directly so a prior run's sticky state can't skew a comparison."""
    n = _clamp_count(count)
    t0 = time.time()
    try:
        prov = resolve_provider(provider)
    except SearchConfigError as ex:
        return [], SearchTelemetry(str(provider or ""), "error",
                                   int((time.time() - t0) * 1000),
                                   error_category="config", error_exc=ex)
    fn = _SEARCH_PROVIDERS[prov]
    retries = 0
    retry_delay = 0.0
    last_retry_after: float | None = None
    while True:
        try:
            results = fn(query, n)
            latency = int((time.time() - t0) * 1000)
            return results, SearchTelemetry(
                prov, "ok" if results else "empty", latency,
                results=len(results), http_status=200, retries=retries,
                retry_delay_s=retry_delay, quota_state=QUOTA_EXHAUSTED)
        except SearchRateLimited as ex:
            last_retry_after = ex.retry_after
            if retries >= SEARCH_RETRY_MAX:
                latency = int((time.time() - t0) * 1000)
                return [], SearchTelemetry(
                    prov, "error", latency, error_category="rate_limit",
                    http_status=429, retry_after_s=last_retry_after,
                    retries=retries, retry_delay_s=retry_delay,
                    quota_state=QUOTA_EXHAUSTED, error_exc=ex)
            delay = min(ex.retry_after or RETRY_BASE_SECONDS * (2 ** retries),
                        RETRY_CAP_SECONDS)
            time.sleep(delay)
            retry_delay += delay
            retries += 1
        except SearchError as ex:
            latency = int((time.time() - t0) * 1000)
            return [], SearchTelemetry(
                prov, "error", latency, error_category=ex.category,
                http_status=ex.http_status, retry_after_s=ex.retry_after,
                retries=retries, retry_delay_s=retry_delay,
                quota_state=QUOTA_EXHAUSTED, error_exc=ex)


def _emit_telemetry(tel: SearchTelemetry) -> None:
    sink = SEARCH_TELEMETRY_SINK
    if sink is None:
        return
    try:
        sink(tel)
    except Exception:
        pass                    # telemetry must never break the search path


def web_search(query: str, count: int = DEFAULT_COUNT) -> list[dict]:
    """Search the web → [{title, url, snippet}]. Backend per SEARCH_PROVIDER
    (operator-selected; no fallback). Preserves the production contract: the
    sticky-quota short-circuit and the typed exceptions the DeepSeek tools loop
    dispatches on (SearchQuotaExhausted vs everything else)."""
    if QUOTA_EXHAUSTED:
        raise SearchQuotaExhausted(
            "search quota exhausted — no new searches until the plan is "
            "upgraded or the quota resets")
    results, tel = search_with_telemetry(query, count)
    _emit_telemetry(tel)
    if tel.error_exc is not None:
        raise tel.error_exc
    return results


# ── page fetch ────────────────────────────────────────────────────────────────
def fetch_url(url: str) -> dict:
    """GET a page → {url, final_url, title, text, fetched_at}; visible text only,
    truncated to MAX_PAGE_CHARS. Never raises: failures return {url, error} so
    the model can move on to an alternative source."""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=20,
                         allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        if len(text) > MAX_PAGE_CHARS:
            text = text[:MAX_PAGE_CHARS] + " …[truncated]"
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        return {"url": url, "final_url": str(r.url), "title": title, "text": text,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    except Exception as ex:
        return {"url": url, "error": f"{type(ex).__name__}: {ex}"}


# ── grounding ─────────────────────────────────────────────────────────────────
def _norm(url: str) -> str:
    """Comparable form: domain+path, no scheme/query/fragment/trailing slash/www."""
    try:
        p = urlparse(str(url).strip())
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return (host + (p.path or "").rstrip("/")) if host else ""
    except Exception:
        return ""


class SourceLog:
    """Everything the web tools saw in one collector pass: search-result URLs,
    fetched URLs (incl. redirect targets) and page texts. check_grounding()
    audits a record's cited sources against this log."""

    def __init__(self):
        self._lock = threading.Lock()
        self.seen: dict[str, str] = {}      # normalized → original URL
        self.fetched: dict[str, str] = {}   # normalized → page text
        self.queries: set[str] = set()      # normalized queries already run
        self.tool_calls = 0
        # per-pass telemetry counters, filled by the tools loop and flushed
        # into the run's events.jsonl (counts only — no queries/URLs/keys)
        self.stats = {"searches": 0, "fetches": 0, "search_denied": 0,
                      "search_rate_limited": 0, "search_errors": 0,
                      "budget_rounds": 0, "requests": 0,
                      "tokens_in": 0, "tokens_out": 0,
                      "early_stop": 0, "extended": 0,
                      "dup_queries": 0, "cache_hits": 0}

    def log_query(self, q: str) -> bool:
        """Register a search query; False when the same query already ran this
        session (duplicate — not worth an HTTP call or quota)."""
        qn = " ".join(str(q).lower().split())
        with self._lock:
            if qn in self.queries:
                return False
            self.queries.add(qn)
            return True

    def cached_text(self, url: str) -> str | None:
        """Page text if this URL (or its redirect target) was already fetched
        this session — a re-read costs nothing and hits no server."""
        with self._lock:
            return self.fetched.get(_norm(url))

    def log_search(self, results: list[dict]) -> None:
        with self._lock:
            self.stats["searches"] += 1
            for r in results or []:
                n = _norm(r.get("url", ""))
                if n:
                    self.seen[n] = r.get("url", "")

    def log_fetch(self, url: str, result: dict) -> None:
        with self._lock:
            self.stats["fetches"] += 1
            for u in (url, (result or {}).get("final_url", "")):
                n = _norm(u)
                if n:
                    self.seen[n] = u
            if isinstance(result, dict) and result.get("text"):
                n = _norm(result.get("final_url") or url)
                if n:
                    self.fetched[n] = result["text"]

    def check_grounding(self, record: dict, only_fields=None) -> list[str]:
        """Audit record['fields'][*]['source'] against the URLs actually seen:
          * exact match (domain+path)      → grounded, untouched
          * domain-only match              → source kept + review_flags note
          * no match                       → source stripped to "" (the existing
            gate then rejects the field as 'unsourced' → existing repair loop)
        A source may hold several URLs (comma/space-joined by the verifier
        merge) — each part is checked; one genuinely visited part grounds it.
        `only_fields` restricts the audit — REQUIRED for repair passes, whose
        instruction is "keep every other field exactly as-is": untouched fields
        keep sources grounded in their ORIGINAL pass, which this pass's log
        never saw, and stripping those would loop clean fields through repair
        forever.
        review_flags notes carry the DOMAIN only — full URLs can contain year
        strings ("…/2024/…") that would trip the gate's history-missing
        suppression keywords. Full URLs go into the returned detail strings,
        which belong in event/debug logs only.
        Returns one detail string per affected field; [] when nothing cited
        or the object has no fields dict (e.g. discovery output)."""
        fields = record.get("fields") if isinstance(record, dict) else None
        if not isinstance(fields, dict):
            return []
        def flags() -> list:
            # lazy: a fully grounded record stays byte-identical
            if not isinstance(record.get("review_flags"), list):
                record["review_flags"] = []
            return record["review_flags"]
        with self._lock:
            seen = set(self.seen)
        domains = {n.split("/", 1)[0] for n in seen}
        details: list[str] = []
        for name, f in fields.items():
            if only_fields is not None and name not in only_fields:
                continue
            if not isinstance(f, dict):
                continue
            src = f.get("source")
            if not src or not str(src).startswith(("http://", "https://")):
                continue
            parts = [p for p in re.split(r"[,;\s]+", str(src))
                     if p.startswith(("http://", "https://"))]
            norms = [n for n in (_norm(p) for p in parts) if n]
            if any(n in seen for n in norms):
                continue
            dom_hits = sorted({n.split("/", 1)[0] for n in norms} & domains)
            if dom_hits:
                flags().append(f"{name}: source URL not opened this session "
                               f"(domain seen: {dom_hits[0]})")
                details.append(f"{name}: flagged, page not opened ({src})")
            else:
                doms = sorted({n.split("/", 1)[0] for n in norms})
                f["source"] = ""
                flags().append(f"{name}: ungrounded source removed"
                               + (f" ({', '.join(doms)})" if doms else ""))
                details.append(f"{name}: ungrounded source removed ({src})")
        return details
