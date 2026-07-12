"""
Two agent modes — a fixed division of labour: GPT-5.5 researches, Claude administers.

  MODE = "gpt"     → the collectors (research) run on GPT-5.5 (OpenAI Responses API
                     + web_search), the project's designated researcher. The
                     verifier still escalates to Claude on a flag (ambiguity /
                     conflict / strategy), so Claude stays the adjudicator.
  MODE = "claude"  → collectors + verification run fully on Claude (Opus by
                     default; Sonnet/Haiku selectable). Runs here with no extra
                     setup. Claude collectors use the server-side web_search tool
                     so they research instead of answering from memory. Use this as
                     the in-house quality baseline for the gold set.

`gpt` mode points at OpenAI by default but accepts any OpenAI-compatible endpoint
via CHEAP_BASE_URL/CHEAP_MODEL. Everything is env-configurable (see .env.example).
Set the mode with `--mode {gpt,claude}` on the orchestrator, or AGENT_MODE in .env.
`cheap` is accepted as a back-compat alias for `gpt`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

# ── mode ──────────────────────────────────────────────────────────────────────
def _canon_mode(m: str) -> str:
    m = (m or "").lower()
    if m in ("gpt", "cheap", "openai"):
        return "gpt"
    if m in ("grok", "xai", "x.ai"):
        return "grok"
    if m in ("deepseek", "ds"):
        return "deepseek"
    return m

MODE = _canon_mode(os.environ.get("AGENT_MODE", "gpt"))  # "gpt" | "claude"


def set_mode(m: str) -> None:
    global MODE
    MODE = _canon_mode(m) or MODE


# ── research engine: GPT-5.5 (OpenAI Responses API + web_search) ──────────────
# The designated researcher. Web-capable via the Responses API web_search tool, so
# collectors research live sources instead of answering from memory. Defaults to
# OpenAI; point CHEAP_BASE_URL/CHEAP_MODEL at any OpenAI-compatible endpoint to
# swap it. Confirm the exact model string ("gpt-5.5") from the OpenAI dashboard.
CHEAP_BASE_URL = os.environ.get("CHEAP_BASE_URL") or "https://api.openai.com/v1"
CHEAP_MODEL    = os.environ.get("CHEAP_MODEL") or "gpt-5.5"
CHEAP_API_KEY  = os.environ.get("CHEAP_API_KEY", "")
# The research engine browses (Responses API web_search); can be disabled per run.
CHEAP_WEB_CAPABLE = True

# ── Grok engine (xAI — OpenAI-compatible Chat Completions + Live Search) ─────
GROK_BASE_URL = os.environ.get("GROK_BASE_URL") or "https://api.x.ai/v1"
GROK_MODEL    = os.environ.get("GROK_MODEL") or "grok-4.20-0309-reasoning"
GROK_API_KEY  = os.environ.get("GROK_API_KEY", "")

# ── DeepSeek engine (OpenAI-compatible; NO server-side web search — research
#     collectors run on the app's own web tools, see _run_deepseek_tools) ──────
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")

# ── app-side web tools (DeepSeek quantitative research) ──────────────────────
# DeepSeek research collectors browse through the app's own web_search +
# fetch_url tools (src/web_tools.py, client-side function calling) — a search
# API key is required. web_tools reads these from the environment.
SEARCH_API_KEY  = os.environ.get("SEARCH_API_KEY", "")
SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER") or "brave"

# Source log of the most recent DeepSeek tools run, read by api_runner for the
# grounding check. NOTE — v1 safety measure: this is shared module-global state,
# so api_runner runs DeepSeek collectors A/B SEQUENTIALLY (see run_next_step).
# TODO: when parallelizing DeepSeek collectors, return/pass the SourceLog
# explicitly per collector instead of through this global.
LAST_SOURCE_LOG = None

# ── Claude engine ─────────────────────────────────────────────────────────────
CLAUDE_MODEL     = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")       # claude mode: collectors + verify
ESCALATION_MODEL = os.environ.get("ESCALATION_MODEL", "claude-opus-4-8")   # gpt mode: verifier escalation target
USE_WEB_SEARCH   = os.environ.get("USE_WEB_SEARCH", "1").lower() not in ("0", "false", "no")

_cheap_client = None
_claude_client = None
_grok_client = None
_deepseek_client = None


def _cheap():
    global _cheap_client
    if _cheap_client is None:
        from openai import OpenAI  # lazy: claude mode needs neither the package nor a key
        # research passes run for many minutes — generous timeout, single retry
        # (the default 600s timeout + 2 silent retries is what made the app look
        # frozen: a >10-min research pass timed out and restarted from scratch)
        _cheap_client = OpenAI(base_url=CHEAP_BASE_URL, api_key=CHEAP_API_KEY,
                               timeout=1800.0, max_retries=1)
    return _cheap_client


def _claude():
    global _claude_client
    if _claude_client is None:
        import anthropic  # reads ANTHROPIC_API_KEY / `ant auth login` profile
        _claude_client = anthropic.Anthropic()
    return _claude_client


def _grok():
    global _grok_client
    if _grok_client is None:
        from openai import OpenAI   # xAI speaks the OpenAI wire protocol
        _grok_client = OpenAI(base_url=GROK_BASE_URL, api_key=GROK_API_KEY,
                              timeout=1800.0, max_retries=1)
    return _grok_client


def _deepseek():
    global _deepseek_client
    if _deepseek_client is None:
        from openai import OpenAI
        _deepseek_client = OpenAI(base_url=DEEPSEEK_BASE_URL, api_key=DEEPSEEK_API_KEY,
                                  timeout=1800.0, max_retries=1)
    return _deepseek_client


def _thinks(model: str) -> bool:
    """Adaptive thinking + effort are supported on Opus 4.6+ and Sonnet 5/4.6, not Haiku."""
    return model.startswith("claude-opus-4-") or model in ("claude-sonnet-5", "claude-sonnet-4-6")


def _run_responses(client, model: str, system: str, user: str, web: bool = False,
                   max_tokens: int = 16000, on_event=None) -> str:
    """Shared Responses-API runner (OpenAI GPT-5.5 and xAI Grok speak the same
    protocol, web research included via the server-side web_search tool).

    STREAMED for two reasons: a research pass generates for many minutes (a
    non-streaming call dies on idle HTTP timeouts), and the stream carries live
    tool activity — `on_event(action, detail)` receives ("searching", query),
    ("reading", url) and ("writing", "") for the app's status line."""
    kwargs = dict(model=model, instructions=system, input=user, max_output_tokens=max_tokens)
    if web and USE_WEB_SEARCH:
        kwargs["tools"] = [{"type": "web_search"}]
    ev = on_event or (lambda a, d: None)
    wrote = False
    with client.responses.stream(**kwargs) as s:
        for event in s:
            et = getattr(event, "type", "")
            # .added fires when a tool call starts; .done carries the filled-in
            # action (query/url) — xAI populates it only at .done
            if et in ("response.output_item.added", "response.output_item.done"):
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "web_search_call":
                    action = getattr(item, "action", None)
                    url = getattr(action, "url", None) or ""
                    query = getattr(action, "query", None) or ""
                    if url:
                        ev("reading", url)
                    elif query or et == "response.output_item.added":
                        ev("searching", query)
            elif et == "response.output_text.delta" and not wrote:
                wrote = True
                ev("writing", "")
        resp = s.get_final_response()
    if getattr(resp, "status", None) == "incomplete":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", "unknown")
        raise RuntimeError(f"{model} response incomplete ({reason}) — "
                           f"raise max_output_tokens or retry")
    text = resp.output_text or ""
    if not text.strip():
        raise RuntimeError(f"{model} returned no text (status="
                           f"{getattr(resp, 'status', '?')}) — retry the step")
    return text


def _run_cheap(system: str, user: str, web: bool = False, max_tokens: int = 16000,
               on_event=None) -> str:
    return _run_responses(_cheap(), CHEAP_MODEL, system, user, web, max_tokens, on_event)


def _run_grok(system: str, user: str, web: bool = False, max_tokens: int = 16000,
              on_event=None) -> str:
    # xAI's Agent Tools API (the old Live Search `search_parameters` was
    # deprecated with HTTP 410) — same Responses protocol as OpenAI.
    return _run_responses(_grok(), GROK_MODEL, system, user, web, max_tokens, on_event)


def _run_deepseek(system: str, user: str, max_tokens: int = 16000, on_event=None) -> str:
    # DeepSeek via Chat Completions (streamed). No web tool exists on this API —
    # callers must only use it for non-browsing work (verify / qual design).
    ev = on_event or (lambda a, d: None)
    kwargs = dict(
        model=DEEPSEEK_MODEL, max_tokens=max_tokens, stream=True,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    parts = []
    for chunk in _deepseek().chat.completions.create(**kwargs):
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            if not parts:
                ev("writing", "")
            parts.append(chunk.choices[0].delta.content)
    text = "".join(parts)
    if not text.strip():
        raise RuntimeError(f"{DEEPSEEK_MODEL} returned no text — retry the step")
    return text


# OpenAI function schemas for the app-side tools (DeepSeek research only)
_DS_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Returns a JSON list of "
                       "{title, url, snippet} results.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "the search query"},
            "count": {"type": "integer",
                      "description": "max results to return (default 8)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Open a web page and return its visible text (truncated "
                       "to ~10k chars). On failure returns {url, error} — try "
                       "an alternative source instead of giving up.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the http(s) URL to open"}},
            "required": ["url"]}}},
]

# Appended to the system prompt for DeepSeek research runs ONLY — the shared
# prompts/*.md files are never modified.
_DS_TOOLS_ADDENDUM = (
    "\n\nYou have no built-in browsing. Use the web_search and fetch_url tools "
    "for every fact. Cite as `source` only URLs you actually fetched or saw in "
    "search results this session. If a page fails to fetch, try an alternative "
    "source rather than answering from memory.")

_DS_MAX_TOOL_CALLS = 25   # fallback cap when a stage has no configured budget
_DS_KEEP_FETCHES = 8      # newest fetched pages kept verbatim in the ~64k context

# Per-stage tool budgets (env-configurable) + field-aware stopping. The base
# budget can EXTEND (by DS_BUDGET_EXTEND calls) only while the caller says
# required fields are still unresolved AND recent calls keep producing new
# evidence; conversely, a pass that stops finding anything new is finished
# EARLY — agents are never pushed to exhaust a budget for its own sake.
_STAGE_BUDGET_DEFAULTS = {"discovery": 20, "collector_a": 25,
                          "collector_b": 25, "repair": 12}
_DS_NOVELTY_WINDOW = 6    # executed calls with zero new URLs → evidence dried up


def stage_budget(stage: str) -> int:
    """Tool budget for a research stage: DS_BUDGET_<STAGE> env, else default."""
    raw = os.environ.get(f"DS_BUDGET_{stage.upper()}", "")
    try:
        n = int(raw)
        if n > 0:
            return n
    except ValueError:
        pass
    return _STAGE_BUDGET_DEFAULTS.get(stage, _DS_MAX_TOOL_CALLS)


def _budget_extend() -> int:
    try:
        return max(0, int(os.environ.get("DS_BUDGET_EXTEND", "") or 8))
    except ValueError:
        return 8
_DS_IDLE_TIMEOUT = 180.0  # max silence between stream chunks before we give up
_DS_PASS_DEADLINE = 1500  # max seconds for one whole tools pass (25 min)

_QUOTA_NOTE = ("search quota exhausted — no new searches available. Work with "
               "the pages already fetched and URLs you already know (fetch_url "
               "still works). Leave fields you cannot confirm blank and add a "
               "review_flags note instead of guessing.")


def _run_deepseek_tools(system: str, user: str, max_tokens: int = 16000,
                        on_event=None, budget: int | None = None,
                        allow_extend: bool = True):
    """DeepSeek research pass: Chat Completions function-calling loop over the
    app's web_search/fetch_url tools. Returns (text, SourceLog) — the log holds
    every URL the model saw, for the grounding check in api_runner.
    Each iteration is one short non-streaming call (the long-run streaming
    concern doesn't apply: browsing happens between calls, in the app)."""
    import time as _time

    import httpx

    from . import web_tools
    from .web_tools import (SearchQuotaExhausted, SourceLog, fetch_url,
                            require_search_key, web_search)
    require_search_key()   # fail fast — before any model tokens are spent
    ev = on_event or (lambda a, d: None)
    log = SourceLog()
    t_start = _time.time()
    quota_announced = False
    # idle timeout catches a stream that stops sending chunks (the «frozen at
    # analyzing & writing» failure); the pass deadline bounds the whole loop
    req_timeout = httpx.Timeout(connect=30.0, read=_DS_IDLE_TIMEOUT,
                                write=60.0, pool=30.0)
    messages = [{"role": "system", "content": system + _DS_TOOLS_ADDENDUM},
                {"role": "user", "content": user}]
    fetch_idxs: list[int] = []   # indices of fetch_url tool results, for trimming
    calls = 0
    nudged = False
    exhausted_rounds = 0
    base = int(budget or _DS_MAX_TOOL_CALLS)
    hard = base + (_budget_extend() if allow_extend else 0)
    novelty: list[bool] = []     # per executed call: did it surface any NEW URL?

    def _cap() -> int:
        # the extension is earned, not granted: only while recent calls keep
        # producing evidence the model hasn't seen yet
        if allow_extend and novelty and any(novelty[-_DS_NOVELTY_WINDOW:]):
            return hard
        return base

    def _dried_up() -> bool:
        # field-aware early stop: enough spend AND a full window of calls that
        # produced nothing new — finishing beats burning the rest of the budget
        return (len(novelty) >= max(_DS_NOVELTY_WINDOW, base // 2)
                and not any(novelty[-_DS_NOVELTY_WINDOW:]))

    while True:
        capped = calls >= _cap() or _dried_up()
        # Tools stay in the request even past the call budget: REMOVING them
        # mid-conversation made DeepSeek leak its internal tool markup
        # ('<｜DSML｜…') as plain text instead of answering (seen live on
        # BPMSoft/Digital Design). Past the budget, tool calls are simply not
        # executed — each gets a budget-exhausted error payload, which keeps
        # the model in valid function-calling mode and steers it to finish.
        # STREAMED like every other engine here: a non-streaming call sits
        # silent for the whole generation (the final JSON can take minutes),
        # freezing the app's status line and risking idle-connection stalls.
        if _time.time() - t_start > _DS_PASS_DEADLINE:
            raise RuntimeError(
                f"{DEEPSEEK_MODEL} pass exceeded {_DS_PASS_DEADLINE // 60} min "
                f"after {calls} tool call(s) — retry (it resumes from saved "
                f"files), or pick another provider for this step")
        kwargs = dict(model=DEEPSEEK_MODEL, max_tokens=max_tokens,
                      messages=messages, tools=_DS_TOOLS, tool_choice="auto",
                      stream=True, timeout=req_timeout,
                      stream_options={"include_usage": True})
        ev("thinking", "")   # liveness between tool batches: the status line
        content_parts: list[str] = []   # must move even while the model decides
        acc: dict[int, dict] = {}          # tool-call deltas keyed by index
        finish = None
        log.stats["requests"] += 1
        for chunk in _deepseek().chat.completions.create(**kwargs):
            u = getattr(chunk, "usage", None)   # final chunk: usage, no choices
            if u is not None:
                log.stats["tokens_in"] += getattr(u, "prompt_tokens", 0) or 0
                log.stats["tokens_out"] += getattr(u, "completion_tokens", 0) or 0
            if not chunk.choices:
                continue
            ch = chunk.choices[0]
            finish = ch.finish_reason or finish
            d = ch.delta
            if d is None:
                continue
            if d.content:
                if not content_parts:
                    ev("writing", "")
                content_parts.append(d.content)
            for tc in (d.tool_calls or []):
                slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function is not None:
                    if tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        content = "".join(content_parts)
        tool_calls = [acc[i] for i in sorted(acc)]
        if tool_calls:
            messages.append({"role": "assistant", "content": content,
                             "tool_calls": [
                                 {"id": t["id"], "type": "function",
                                  "function": {"name": t["name"],
                                               "arguments": t["arguments"]}}
                                 for t in tool_calls]})
            batch_exhausted = False
            for t in tool_calls:
                if calls >= _cap() or _dried_up():
                    batch_exhausted = True
                    if _dried_up() and calls < hard:
                        log.stats["early_stop"] = 1
                        reason = (f"the last {_DS_NOVELTY_WINDOW} calls produced "
                                  "no new evidence — stop researching")
                    else:
                        reason = f"tool budget exhausted ({calls} calls)"
                    messages.append({"role": "tool", "tool_call_id": t["id"],
                                     "content": json.dumps({"error": (
                                         f"{reason} — return the strict JSON "
                                         "answer now, using only sources already "
                                         "consulted")}, ensure_ascii=False)})
                    continue
                calls += 1
                log.tool_calls = calls
                new_before = len(log.seen)
                try:
                    args = json.loads(t["arguments"] or "{}")
                except Exception:
                    args = {}
                name = t["name"]
                if name == "web_search":
                    q = str(args.get("query", ""))
                    if not log.log_query(q):
                        # identical query already ran — deny without HTTP/quota;
                        # non-novel, so repeats push the pass toward early stop
                        log.stats["dup_queries"] += 1
                        novelty.append(False)
                        messages.append({"role": "tool", "tool_call_id": t["id"],
                                         "content": json.dumps({"error": (
                                             "this exact query already ran this "
                                             "session — refine it, or open pages "
                                             "already found")}, ensure_ascii=False)})
                        continue
                    if web_tools.QUOTA_EXHAUSTED:
                        # deny instantly — no HTTP, no status churn, and tell
                        # the model once how to proceed without search. Denials
                        # still consume the budget so the loop stays bounded.
                        calls += 1
                        novelty.append(False)   # a denial yields no evidence
                        log.stats["search_denied"] += 1
                        if not quota_announced:
                            quota_announced = True
                            ev("quota", "")
                        payload = json.dumps({"error": _QUOTA_NOTE},
                                             ensure_ascii=False)
                        messages.append({"role": "tool", "tool_call_id": t["id"],
                                         "content": payload})
                        continue
                    ev("searching", q)
                    try:
                        results = web_search(q, int(args.get("count") or 8))
                        log.log_search(results)
                        payload = json.dumps(results, ensure_ascii=False)
                    except SearchQuotaExhausted:
                        log.stats["search_denied"] += 1
                        if not quota_announced:
                            quota_announced = True
                            ev("quota", "")
                        payload = json.dumps({"error": _QUOTA_NOTE},
                                             ensure_ascii=False)
                    except Exception as ex:
                        payload = json.dumps(
                            {"error": f"{type(ex).__name__}: {ex}"},
                            ensure_ascii=False)
                elif name == "fetch_url":
                    u = str(args.get("url", ""))
                    cached = log.cached_text(u)
                    if cached is not None:
                        # re-read served from the session cache: no HTTP, and
                        # non-novel — repeats wind the pass down, not up
                        log.stats["cache_hits"] += 1
                        payload = json.dumps(
                            {"url": u, "note": "served from session cache — "
                             "you already fetched this page", "text": cached},
                            ensure_ascii=False)
                        fetch_idxs.append(len(messages))   # still trimmable
                    else:
                        ev("reading", u)
                        result = fetch_url(u)      # never raises: {url, error}
                        log.log_fetch(u, result)
                        fetch_idxs.append(len(messages))
                        payload = json.dumps(result, ensure_ascii=False)
                else:
                    payload = json.dumps({"error": f"unknown tool «{name}»"})
                novelty.append(len(log.seen) > new_before)
                messages.append({"role": "tool", "tool_call_id": t["id"],
                                 "content": payload})
            # keep only the newest pages verbatim in the model's context —
            # the SourceLog keeps everything for the grounding check regardless
            while len(fetch_idxs) > _DS_KEEP_FETCHES:
                i = fetch_idxs.pop(0)
                try:
                    dropped = json.loads(messages[i]["content"]).get("url", "")
                except Exception:
                    dropped = ""
                messages[i]["content"] = f"[dropped from context: {dropped}]"
            if batch_exhausted:
                exhausted_rounds += 1
                log.stats["budget_rounds"] = exhausted_rounds
                if exhausted_rounds == 1:
                    messages.append({"role": "user", "content":
                        "Finish now: return the strict JSON using only sources "
                        "already consulted."})
                elif exhausted_rounds > 3:
                    raise RuntimeError(
                        f"{DEEPSEEK_MODEL} kept requesting tools after the "
                        f"{_DS_MAX_TOOL_CALLS}-call budget ran out — retry, or "
                        f"pick ChatGPT / Claude / Grok for this step")
            continue
        text = content.strip()
        if text and "{" in text:
            log.stats["extended"] = max(0, calls - base)   # calls earned past base
            return text, log
        if not nudged:                             # one retry, then give up
            nudged = True
            messages.append({"role": "assistant",
                             "content": content or "(no output)"})
            messages.append({"role": "user", "content":
                "Finish now: return the strict JSON using only sources already "
                "consulted." if capped else
                "You must research via the web_search and fetch_url tools, then "
                "return the strict JSON object. Start with web_search now."})
            continue
        raise RuntimeError(
            f"{DEEPSEEK_MODEL} produced neither tool calls nor JSON after "
            f"{calls} tool call(s) (finish_reason={finish}, content head: "
            f"{content[:160]!r}) — retry, or pick ChatGPT / Claude / Grok "
            f"for this step")


def _run_claude(model: str, system: str, user: str, max_tokens: int = 16000,
                web: bool = False, on_event=None) -> str:
    client = _claude()
    ev = on_event or (lambda a, d: None)
    kwargs = dict(model=model, max_tokens=max_tokens, system=system)
    if _thinks(model):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
    if web:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": user}]
    resp = None
    wrote = False
    for _ in range(6):  # server-side web search can pause_turn; resume until done
        with client.messages.stream(messages=messages, **kwargs) as s:
            for event in s:
                t = getattr(event, "type", "")
                if t == "content_block_start":
                    b = getattr(event, "content_block", None)
                    bt = getattr(b, "type", "")
                    if bt == "server_tool_use":
                        q = ""
                        inp = getattr(b, "input", None)
                        if isinstance(inp, dict):
                            q = inp.get("query") or ""
                        ev("searching", q)
                    elif bt == "web_search_tool_result":
                        c = getattr(b, "content", None)
                        if isinstance(c, list) and c:
                            ev("reading", getattr(c[0], "url", "") or "")
                elif (t == "content_block_delta" and not wrote
                      and getattr(getattr(event, "delta", None), "type", "") == "text_delta"):
                    wrote = True
                    ev("writing", "")
            resp = s.get_final_message()   # streamed: no HTTP timeout on long runs
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# ── public interface: the orchestrator calls these ───────────────────────────
def collect(system: str, user: str, max_tokens: int = 16000, on_event=None,
            budget: int | None = None, allow_extend: bool = True):
    """Run one collector (web research on). Returns (raw_text, engine_label).
    `budget`/`allow_extend` shape the app-side tool budget (DeepSeek only —
    server-side search providers ignore them)."""
    if MODE == "claude":
        return _run_claude(CLAUDE_MODEL, system, user, max_tokens,
                           web=USE_WEB_SEARCH, on_event=on_event), CLAUDE_MODEL
    if MODE == "grok":
        return _run_grok(system, user, web=True, max_tokens=max_tokens,
                         on_event=on_event), GROK_MODEL
    if MODE == "deepseek":
        # No server-side search on this API — research runs on the app's own
        # web tools. v1: the SourceLog travels through the module global (see
        # LAST_SOURCE_LOG note) — do not run two deepseek collects concurrently.
        global LAST_SOURCE_LOG
        text, log = _run_deepseek_tools(system, user, max_tokens, on_event,
                                        budget=budget, allow_extend=allow_extend)
        LAST_SOURCE_LOG = log
        return text, f"{DEEPSEEK_MODEL}+tools"
    return _run_cheap(system, user, web=True, max_tokens=max_tokens,
                      on_event=on_event), CHEAP_MODEL


def verify(system: str, user: str, escalate: bool, max_tokens: int = 12000, on_event=None):
    """Run the verifier (no browsing). `escalate` flags ambiguity/conflict/strategy. Returns (raw_text, engine_label)."""
    if MODE == "claude":
        model = ESCALATION_MODEL if (escalate and CLAUDE_MODEL != ESCALATION_MODEL) else CLAUDE_MODEL
        return _run_claude(model, system, user, max_tokens, web=False, on_event=on_event), model
    if escalate:
        return _run_claude(ESCALATION_MODEL, system, user, max_tokens,
                           web=False, on_event=on_event), ESCALATION_MODEL
    if MODE == "grok":
        return _run_grok(system, user, web=False, max_tokens=max_tokens,
                         on_event=on_event), GROK_MODEL
    if MODE == "deepseek":
        return _run_deepseek(system, user, max_tokens, on_event), DEEPSEEK_MODEL
    return _run_cheap(system, user, web=False, max_tokens=max_tokens,
                      on_event=on_event), CHEAP_MODEL


def banner() -> str:
    if MODE == "claude":
        web = "web_search on" if USE_WEB_SEARCH else "no web"
        return f"mode=claude  collectors/verify={CLAUDE_MODEL} ({web})  escalate={ESCALATION_MODEL}"
    if MODE == "grok":
        web = "web_search on" if USE_WEB_SEARCH else "no web"
        return f"mode=grok  research/verify={GROK_MODEL} (xAI, {web})  escalate={ESCALATION_MODEL}"
    if MODE == "deepseek":
        return (f"mode=deepseek  research={DEEPSEEK_MODEL} (app web tools: "
                f"search+fetch)  verify/qual={DEEPSEEK_MODEL}")
    gpt_web = "web_search on" if (CHEAP_WEB_CAPABLE and USE_WEB_SEARCH) else "no browsing"
    return (f"mode=gpt  research/verify={CHEAP_MODEL} (OpenAI, {gpt_web})  "
            f"escalate={ESCALATION_MODEL}")


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response (handles ```json fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else text
    start = blob.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in model output:\n{text[:500]}")
    depth = 0
    for i in range(start, len(blob)):
        if blob[i] == "{":
            depth += 1
        elif blob[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(blob[start : i + 1])
    raise ValueError("Unbalanced JSON in model output.")
