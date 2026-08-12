"""Append-only event ledger.

The single source of truth for a run's history, mirroring the company
`events.jsonl` byte format exactly: one JSON object per line,
`{"ts": <iso>, "event": <name>, **fields}`, appended under a lock so
concurrent workers are safe. Reusable verbatim by any research pack; the
company code (`src.runs._event`) writes the same shape, so tooling can read
both.

Concurrency note: the append lock is keyed by the ledger's *resolved path* and
shared process-wide, so two `EventLedger` instances pointing at the same file —
e.g. a controller and a UI each holding their own `RunHandle` to one run — still
serialise their writes. (A per-instance lock would not: two handles = two locks
= no mutual exclusion. The company `runs._event` avoids this with a single
module-level lock; keying by path is the generalisation that also isolates
unrelated runs.)
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator

from ._util import now_iso

# Process-wide registry of per-file append locks, so any two EventLedger
# instances for the same file coordinate. `_REGISTRY_LOCK` guards the dict.
_REGISTRY_LOCK = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _REGISTRY_LOCK:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = _FILE_LOCKS[key] = threading.Lock()
        return lock


class EventLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = _lock_for(self.path)   # shared per-file, not per-instance

    def append(self, event: str, **fields) -> dict:
        """Append one event. Returns the written record (with its ts)."""
        rec = {"ts": now_iso(), "event": str(event), **fields}
        line = json.dumps(rec, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return rec

    def read(self) -> list[dict]:
        return list(self.iter())

    def iter(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # never let a corrupt line break a reader

    def count(self, event: str | None = None) -> int:
        return sum(1 for r in self.iter() if event is None or r.get("event") == event)

    def last(self, event: str | None = None) -> dict | None:
        found = None
        for r in self.iter():
            if event is None or r.get("event") == event:
                found = r
        return found
