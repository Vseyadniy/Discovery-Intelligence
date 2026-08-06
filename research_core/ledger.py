"""Append-only event ledger.

The single source of truth for a run's history, mirroring the company
`events.jsonl` byte format exactly: one JSON object per line,
`{"ts": <iso>, "event": <name>, **fields}`, appended under a lock so
concurrent workers are safe. Reusable verbatim by any research pack; the
company code (`src.runs._event`) writes the same shape, so tooling can read
both.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator

from ._util import now_iso


class EventLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

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
