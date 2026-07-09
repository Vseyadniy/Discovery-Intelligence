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
GROK_MODEL    = os.environ.get("GROK_MODEL") or "grok-4"
GROK_API_KEY  = os.environ.get("GROK_API_KEY", "")

# ── Claude engine ─────────────────────────────────────────────────────────────
CLAUDE_MODEL     = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")       # claude mode: collectors + verify
ESCALATION_MODEL = os.environ.get("ESCALATION_MODEL", "claude-opus-4-8")   # gpt mode: verifier escalation target
USE_WEB_SEARCH   = os.environ.get("USE_WEB_SEARCH", "1").lower() not in ("0", "false", "no")

_cheap_client = None
_claude_client = None
_grok_client = None


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


def _thinks(model: str) -> bool:
    """Adaptive thinking + effort are supported on Opus 4.6+ and Sonnet 5/4.6, not Haiku."""
    return model.startswith("claude-opus-4-") or model in ("claude-sonnet-5", "claude-sonnet-4-6")


def _run_cheap(system: str, user: str, web: bool = False, max_tokens: int = 16000) -> str:
    # GPT-5.5 via the Responses API — the browsing path (web_search tool).
    # STREAMED: a research pass generates for many minutes; a non-streaming call
    # dies on idle HTTP timeouts. Reasoning also consumes output tokens, so the
    # ceiling is generous — 4000 used to truncate real collector passes.
    client = _cheap()
    kwargs = dict(model=CHEAP_MODEL, instructions=system, input=user, max_output_tokens=max_tokens)
    if web and USE_WEB_SEARCH:
        kwargs["tools"] = [{"type": "web_search"}]
    with client.responses.stream(**kwargs) as s:
        resp = s.get_final_response()
    if getattr(resp, "status", None) == "incomplete":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", "unknown")
        raise RuntimeError(f"{CHEAP_MODEL} response incomplete ({reason}) — "
                           f"raise max_output_tokens or retry")
    text = resp.output_text or ""
    if not text.strip():
        raise RuntimeError(f"{CHEAP_MODEL} returned no text (status="
                           f"{getattr(resp, 'status', '?')}) — retry the step")
    return text


def _run_grok(system: str, user: str, web: bool = False, max_tokens: int = 16000) -> str:
    # Grok via Chat Completions (streamed). Web research uses xAI's server-side
    # Live Search — enabled through the vendor extension `search_parameters`.
    client = _grok()
    kwargs = dict(
        model=GROK_MODEL, max_tokens=max_tokens, stream=True,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    if web and USE_WEB_SEARCH:
        kwargs["extra_body"] = {"search_parameters": {"mode": "auto"}}
    parts = []
    for chunk in client.chat.completions.create(**kwargs):
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            parts.append(chunk.choices[0].delta.content)
    text = "".join(parts)
    if not text.strip():
        raise RuntimeError(f"{GROK_MODEL} returned no text — retry the step")
    return text


def _run_claude(model: str, system: str, user: str, max_tokens: int = 16000, web: bool = False) -> str:
    client = _claude()
    kwargs = dict(model=model, max_tokens=max_tokens, system=system)
    if _thinks(model):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
    if web:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": user}]
    resp = None
    for _ in range(6):  # server-side web search can pause_turn; resume until done
        with client.messages.stream(messages=messages, **kwargs) as s:
            resp = s.get_final_message()   # streamed: no HTTP timeout on long runs
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# ── public interface: the orchestrator calls these ───────────────────────────
def collect(system: str, user: str, max_tokens: int = 16000):
    """Run one collector (web research on). Returns (raw_text, engine_label)."""
    if MODE == "claude":
        return _run_claude(CLAUDE_MODEL, system, user, max_tokens, web=USE_WEB_SEARCH), CLAUDE_MODEL
    if MODE == "grok":
        return _run_grok(system, user, web=True, max_tokens=max_tokens), GROK_MODEL
    return _run_cheap(system, user, web=True, max_tokens=max_tokens), CHEAP_MODEL


def verify(system: str, user: str, escalate: bool, max_tokens: int = 12000):
    """Run the verifier (no browsing). `escalate` flags ambiguity/conflict/strategy. Returns (raw_text, engine_label)."""
    if MODE == "claude":
        model = ESCALATION_MODEL if (escalate and CLAUDE_MODEL != ESCALATION_MODEL) else CLAUDE_MODEL
        return _run_claude(model, system, user, max_tokens, web=False), model
    if escalate:
        return _run_claude(ESCALATION_MODEL, system, user, max_tokens, web=False), ESCALATION_MODEL
    if MODE == "grok":
        return _run_grok(system, user, web=False, max_tokens=max_tokens), GROK_MODEL
    return _run_cheap(system, user, web=False, max_tokens=max_tokens), CHEAP_MODEL


def banner() -> str:
    if MODE == "claude":
        web = "web_search on" if USE_WEB_SEARCH else "no web"
        return f"mode=claude  collectors/verify={CLAUDE_MODEL} ({web})  escalate={ESCALATION_MODEL}"
    if MODE == "grok":
        web = "live_search on" if USE_WEB_SEARCH else "no web"
        return f"mode=grok  research/verify={GROK_MODEL} (xAI, {web})  escalate={ESCALATION_MODEL}"
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
