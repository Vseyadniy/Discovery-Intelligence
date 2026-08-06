"""Controller vocabulary: terminal states, run-level limits, progress snapshots,
and cooperative pause/stop.

This is the reusable skeleton behind the company Auto controller (`src.auto`):
the terminal-state set, run-level budgets, snapshot-diff no-progress detection,
and file-backed pause/stop so a run resumes across process restarts. It drives
no company logic — a pack supplies the per-step work and its own progress
fingerprint.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

# ── terminal states (superset of the company Auto states) ─────────────────────
COMPLETE = "complete"
COMPLETE_WITH_GAPS = "complete-with-gaps"
NEEDS_REVIEW = "needs-review"
STOPPED_QUOTA = "stopped-quota"
STOPPED_PROVIDER = "stopped-provider"
STOPPED_BUDGET = "stopped-budget"
STOPPED_NO_PROGRESS = "stopped-no-progress"
STOPPED_USER = "stopped-user"
INTERRUPTED = "interrupted"
BLOCKED_INPUT = "blocked-input"

TERMINAL_STATES = frozenset({
    COMPLETE, COMPLETE_WITH_GAPS, NEEDS_REVIEW, STOPPED_QUOTA, STOPPED_PROVIDER,
    STOPPED_BUDGET, STOPPED_NO_PROGRESS, STOPPED_USER, INTERRUPTED, BLOCKED_INPUT,
})
SUCCESS_STATES = frozenset({COMPLETE, COMPLETE_WITH_GAPS})


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def classify(state: str) -> str:
    """Coarse category for reporting: success | review | stopped | error | running."""
    if state in SUCCESS_STATES:
        return "success"
    if state == NEEDS_REVIEW:
        return "review"
    if state in (INTERRUPTED, BLOCKED_INPUT):
        return "error"
    if state in TERMINAL_STATES:
        return "stopped"
    return "running"


# ── run-level limits (a hit → STOPPED_BUDGET / STOPPED_PROVIDER) ──────────────
@dataclass
class Limits:
    max_steps: int | None = None
    max_wall_seconds: float | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    provider_fail_steps: int = 2   # consecutive all-failed steps → stopped-provider

    def exceeded(self, *, steps=0, wall_seconds=0.0, tool_calls=0, tokens=0) -> str | None:
        """Return the name of the first limit hit, else None."""
        if self.max_steps is not None and steps >= self.max_steps:
            return "max_steps"
        if self.max_wall_seconds is not None and wall_seconds >= self.max_wall_seconds:
            return "max_wall_seconds"
        if self.max_tool_calls is not None and tool_calls >= self.max_tool_calls:
            return "max_tool_calls"
        if self.max_tokens is not None and tokens >= self.max_tokens:
            return "max_tokens"
        return None


# ── progress snapshot (snapshot-diff no-progress detection) ───────────────────
@dataclass(frozen=True)
class ControllerSnapshot:
    """An opaque, comparable fingerprint of observable progress. A pack builds it
    from whatever it counts (records accepted, fields resolved, …). Equality =
    'nothing observable changed', which the controller uses to detect a stall."""
    fingerprint: tuple = ()

    @classmethod
    def of(cls, mapping: dict) -> "ControllerSnapshot":
        return cls(tuple(sorted((str(k), _hashable(v)) for k, v in (mapping or {}).items())))

    def changed_from(self, other: "ControllerSnapshot | None") -> bool:
        return other is None or self.fingerprint != other.fingerprint


def _hashable(v):
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((str(k), _hashable(x)) for k, x in v.items()))
    return v


# ── cooperative pause / stop, file-backed so it survives a restart ────────────
@dataclass
class RunControl:
    """Cooperative control checked between safe steps. In-memory flags plus an
    optional `control.json` in the run dir, so a Pause/Stop set by a UI (or a
    prior session) is honoured after resume. Never interrupts a step mid-flight."""
    run_dir: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def _file(self) -> Path | None:
        return (Path(self.run_dir) / "control.json") if self.run_dir else None

    def _read(self) -> dict:
        f = self._file
        if f and f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _write(self, state: dict) -> None:
        f = self._file
        if f:
            f.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def _set(self, key: str, value: bool) -> None:
        with self._lock:
            state = self._read()
            state[key] = value
            self._mem = getattr(self, "_mem", {})
            self._mem[key] = value
            self._write(state)

    def _get(self, key: str) -> bool:
        with self._lock:
            if getattr(self, "_mem", {}).get(key):
                return True
            return bool(self._read().get(key))

    def request_pause(self) -> None: self._set("pause", True)
    def request_stop(self) -> None: self._set("stop", True)
    def clear(self) -> None:
        with self._lock:
            self._mem = {}
            self._write({})

    def should_pause(self) -> bool: return self._get("pause")
    def should_stop(self) -> bool: return self._get("stop")
