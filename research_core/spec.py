"""Research Spec / Pack / Registry — the extension seam.

A **ResearchPack** is a specialised intelligence product (Funds, Assets,
Pricing, Telegram, …). It declares:
  * an id,
  * the deterministic validation rules it contributes (registered callables),
  * the outputs (deliverables) it produces,
  * a default ResearchSpec.

A **ResearchSpec** is the per-run configuration: which pack, geography,
free-form params, the selected validation rule ids, and the requested outputs.
It is data — safe to persist in run.json and to build (in future) from a plan.

The registry lets a controller resolve a pack by id. Company Intelligence is
deliberately NOT registered here yet: this task creates the seam, not a
migration. Funds Intelligence will be the first registered pack.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .validation import RuleRegistry


@dataclass(frozen=True)
class OutputSpec:
    """A declarative deliverable descriptor. `builder` is an id resolved by the
    pack (not executable data), keeping outputs declarative and code-controlled."""
    name: str
    kind: str                 # e.g. "xlsx" | "docx" | "md" | "json"
    description: str = ""
    builder: str = ""         # id of a pack-provided builder; "" = pack default


@dataclass
class ResearchSpec:
    pack_id: str
    geo: str | None = None
    output_language: str = "English"
    params: dict = field(default_factory=dict)
    rule_ids: list[str] = field(default_factory=list)   # selected deterministic rules
    outputs: list[str] = field(default_factory=list)    # OutputSpec names to build

    def to_meta(self) -> dict:
        """Serialisable form for run.json."""
        return {"pack_id": self.pack_id, "geo": self.geo,
                "output_language": self.output_language, "params": self.params,
                "rule_ids": list(self.rule_ids), "outputs": list(self.outputs)}

    @classmethod
    def from_meta(cls, d: dict) -> "ResearchSpec":
        return cls(pack_id=d["pack_id"], geo=d.get("geo"),
                   output_language=d.get("output_language", "English"),
                   params=d.get("params") or {}, rule_ids=list(d.get("rule_ids") or []),
                   outputs=list(d.get("outputs") or []))


@runtime_checkable
class ResearchPack(Protocol):
    id: str
    version: str        # exact pack version; persisted on every run it owns
    def register_rules(self, registry: RuleRegistry) -> None: ...
    def outputs(self) -> list[OutputSpec]: ...
    def default_spec(self) -> ResearchSpec: ...


class PackRegistry:
    def __init__(self):
        self._packs: dict[str, ResearchPack] = {}

    def register(self, pack: ResearchPack) -> None:
        pid = getattr(pack, "id", None)
        if not pid:
            raise ValueError("pack must have a non-empty `id`")
        if pid in self._packs:
            raise ValueError(f"pack id already registered: {pid!r}")
        self._packs[pid] = pack

    def get(self, pack_id: str) -> ResearchPack:
        if pack_id not in self._packs:
            raise KeyError(f"no registered pack: {pack_id!r}")
        return self._packs[pack_id]

    def ids(self) -> list[str]:
        return sorted(self._packs)


# process-wide default registry a controller can import
REGISTRY = PackRegistry()
