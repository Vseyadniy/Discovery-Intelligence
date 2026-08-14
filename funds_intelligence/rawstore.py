"""Raw paid-response persistence — write BEFORE you parse.

The first live landscape pass spent 115k tokens and left nothing on disk: the
controller went straight from `res.text` into `json.loads`, so the exception
took the only copy of the response with it.

Invariant established here: **every paid `ResearchPass` response is written to
the run folder before any extraction is attempted.** Extraction, validation and
artifact persistence all happen afterwards, so a malformed response is always
diagnosable and the paid work is never lost.

Layout (inside the run folder, alongside `run.json`):

    raw/001-landscape.txt              the exact response text, verbatim
    raw/001-landscape.json             {stage, target, engine, tokens, ts, chars}
    raw/002-research-alpha-capital.txt
    …

The sequence number is monotonic per run and never reused, so a retry of a
failed stage preserves the earlier attempt rather than overwriting it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s), flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)


def next_seq(h) -> int:
    """Next unused sequence number — derived from disk, so it survives a
    restart and never collides with a previous attempt."""
    d = h.path("raw")
    if not d.exists():
        return 1
    used = []
    for p in d.glob("*.txt"):
        m = re.match(r"(\d+)-", p.name)
        if m:
            used.append(int(m.group(1)))
    return (max(used) + 1) if used else 1


def save_raw(h, *, stage: str, text: str, target: str = "",
             engine: str = "", tokens: int = 0):
    """Persist one paid response verbatim. Returns the .txt Path.

    Called before extraction — this must not raise on odd content, so the text
    is written as-is with surrogates escaped rather than validated.
    """
    h.subdir("raw")
    seq = next_seq(h)
    name = f"{seq:03d}-{_slug(stage)}"
    if target:
        name += f"-{_slug(target)}"
    txt = h.path("raw", f"{name}.txt")
    txt.write_text(text or "", encoding="utf-8", errors="surrogatepass")
    h.path("raw", f"{name}.json").write_text(json.dumps({
        "seq": seq,
        "stage": stage,
        "target": target,
        "engine": engine,
        "tokens": tokens,
        "chars": len(text or ""),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return txt


def list_raw(h) -> list:
    """Every persisted raw response for this run, oldest first."""
    d = h.path("raw")
    return sorted(d.glob("*.txt")) if d.exists() else []
