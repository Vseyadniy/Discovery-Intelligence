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

# ── DeepSeek engine (OpenAI-compatible; NO server-side web search — suitable
#     for the verifier and the qualitative track, not for browsing collectors) ─
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")

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
def collect(system: str, user: str, max_tokens: int = 16000, on_event=None):
    """Run one collector (web research on). Returns (raw_text, engine_label)."""
    if MODE == "claude":
        return _run_claude(CLAUDE_MODEL, system, user, max_tokens,
                           web=USE_WEB_SEARCH, on_event=on_event), CLAUDE_MODEL
    if MODE == "grok":
        return _run_grok(system, user, web=True, max_tokens=max_tokens,
                         on_event=on_event), GROK_MODEL
    if MODE == "deepseek":
        raise RuntimeError(
            "DeepSeek has no server-side web search, and research collectors must "
            "browse live sources. Pick ChatGPT / Claude / Grok for quantitative "
            "research steps — DeepSeek works for the qualitative track (tab 2), "
            "where no browsing is needed.")
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
        return (f"mode=deepseek  verify/qual={DEEPSEEK_MODEL} (DeepSeek, NO web — "
                f"not for browsing collectors)")
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
