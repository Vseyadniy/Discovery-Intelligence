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
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_PAGE_CHARS = 10_000


# ── search (provider-pluggable) ───────────────────────────────────────────────
def require_search_key() -> str:
    """The search key, or a clear error. Called upfront by the DeepSeek tools
    loop (fail fast, before any model tokens are spent) and by the backends."""
    key = os.environ.get("SEARCH_API_KEY", "")
    if not key:
        raise RuntimeError(
            "SEARCH_API_KEY is missing — DeepSeek quantitative research runs on "
            "the app's own web_search tool, which needs a search API key. Enter "
            "one in Settings → «Search API key» (free tier: "
            "https://api-dashboard.search.brave.com), or pick ChatGPT / Claude / "
            "Grok for this step.")
    return key


def _search_brave(query: str, count: int) -> list[dict]:
    key = require_search_key()
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": count},
                     headers={"X-Subscription-Token": key,
                              "Accept": "application/json", "User-Agent": _UA},
                     timeout=20)
    r.raise_for_status()
    items = ((r.json().get("web") or {}).get("results") or [])[:count]
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("description", "")} for i in items]


# extend here (e.g. "tavily": _search_tavily) — callers stay unchanged
_SEARCH_PROVIDERS = {"brave": _search_brave}


def web_search(query: str, count: int = 8) -> list[dict]:
    """Search the web → [{title, url, snippet}]. Backend per SEARCH_PROVIDER."""
    provider = (os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
    fn = _SEARCH_PROVIDERS.get(provider)
    if fn is None:
        raise RuntimeError(f"unknown SEARCH_PROVIDER «{provider}» — "
                           f"known: {', '.join(sorted(_SEARCH_PROVIDERS))}")
    return fn(query, max(1, min(int(count or 8), 20)))


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
        self.tool_calls = 0

    def log_search(self, results: list[dict]) -> None:
        with self._lock:
            for r in results or []:
                n = _norm(r.get("url", ""))
                if n:
                    self.seen[n] = r.get("url", "")

    def log_fetch(self, url: str, result: dict) -> None:
        with self._lock:
            for u in (url, (result or {}).get("final_url", "")):
                n = _norm(u)
                if n:
                    self.seen[n] = u
            if isinstance(result, dict) and result.get("text"):
                n = _norm(result.get("final_url") or url)
                if n:
                    self.fetched[n] = result["text"]

    def check_grounding(self, record: dict) -> list[str]:
        """Audit record['fields'][*]['source'] against the URLs actually seen:
          * exact match (domain+path)      → grounded, untouched
          * domain-only match              → source kept + review_flags note
          * no match                       → source stripped to "" (the existing
            gate then rejects the field as 'unsourced' → existing repair loop)
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
            if not isinstance(f, dict):
                continue
            src = f.get("source")
            if not src or not str(src).startswith(("http://", "https://")):
                continue
            n = _norm(src)
            if n and n in seen:
                continue
            dom = n.split("/", 1)[0] if n else ""
            if dom and dom in domains:
                flags().append(f"{name}: source URL not opened this session "
                               f"(domain seen: {dom})")
                details.append(f"{name}: flagged, page not opened ({src})")
            else:
                f["source"] = ""
                flags().append(f"{name}: ungrounded source removed"
                               + (f" ({dom})" if dom else ""))
                details.append(f"{name}: ungrounded source removed ({src})")
        return details
