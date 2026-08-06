"""Research run persistence — a generic file-based run store.

A run is a directory `<root>/<run_id>/` holding:
  run.json      pretty-printed metadata dict (same format as company run.json)
  events.jsonl  the append-only ledger (see ledger.py)
  …             pack-specific artifacts the pack writes under the run dir

This is the company `logs/<run_id>/` model with the company-specific bits
(market/depth/discovery) removed: the store keeps generic run metadata and
hands ownership of everything else to the pack. It deliberately does NOT know
about companies, schemas, gates, or Excel.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ._util import now_iso, slugify
from .ledger import EventLedger


class RunHandle:
    """An open run: its directory, metadata, and ledger."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.ledger = EventLedger(self.dir / "events.jsonl")

    @property
    def run_id(self) -> str:
        return self.dir.name

    # ── metadata ──────────────────────────────────────────────────────────
    def load_meta(self) -> dict:
        return json.loads((self.dir / "run.json").read_text(encoding="utf-8"))

    def save_meta(self, meta: dict) -> None:
        (self.dir / "run.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_meta(self, **fields) -> dict:
        meta = self.load_meta()
        meta.update(fields)
        self.save_meta(meta)
        return meta

    # ── artifacts ─────────────────────────────────────────────────────────
    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def subdir(self, *parts: str) -> Path:
        d = self.path(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── convenience: log an event ─────────────────────────────────────────
    def event(self, name: str, **fields) -> dict:
        return self.ledger.append(name, **fields)


class RunStore:
    """Creates and opens runs under a root directory (default `logs/`)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def create(self, *, pack_id: str, label: str, meta: dict | None = None) -> RunHandle:
        """Create a run folder. `pack_id` records which research pack owns it;
        `label` is a human slug (e.g. a market or mandate name). Extra `meta`
        is merged in. Returns an open RunHandle with a `created` event logged."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        run_id = f"{stamp}_{slugify(label)}_{slugify(pack_id)}"
        run_dir = self.root / run_id
        if run_dir.exists():
            raise FileExistsError(f"run already exists: {run_dir}")
        run_dir.mkdir(parents=True)
        base = {
            "run_id": run_id,
            "pack_id": pack_id,
            "label": label,
            "status": "created",
            "created_at": now_iso(),
        }
        base.update(meta or {})
        h = RunHandle(run_dir)
        h.save_meta(base)
        h.event("created", pack_id=pack_id, label=label)
        return h

    def open(self, run_id: str) -> RunHandle:
        run_dir = self.root / run_id
        if not (run_dir / "run.json").exists():
            raise FileNotFoundError(f"no run.json in {run_dir}")
        return RunHandle(run_dir)

    def exists(self, run_id: str) -> bool:
        return (self.root / run_id / "run.json").exists()

    def list(self) -> list[dict]:
        """Newest-first metadata for every run under the root."""
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            meta_f = d / "run.json"
            if d.is_dir() and meta_f.exists():
                try:
                    out.append(json.loads(meta_f.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
        return out
