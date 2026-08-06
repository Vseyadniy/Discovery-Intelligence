"""Small shared helpers (stdlib only). Kept byte-compatible with the company
run store so a Research Core run folder is indistinguishable on disk from a
Company Intelligence one (same `run.json` / `events.jsonl` shapes)."""
from __future__ import annotations

import re
from datetime import datetime, timezone


def now_iso() -> str:
    """UTC ISO-8601, seconds precision — identical to src.runs._now()."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(s: str) -> str:
    """Filesystem-safe slug — identical rules to src.runs._slug()."""
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "run"


def is_empty(value) -> bool:
    """Default emptiness test for a field value. Handles a bare scalar and the
    company field shape `{"value": ..., ...}` (mirrors gate.value_of() ∈ (None, ""))."""
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    return value is None or (isinstance(value, str) and value.strip() == "")
