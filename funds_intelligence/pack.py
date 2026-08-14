"""FundsPack — the ResearchPack implementation + the mandate spec.

Funds-specific vocabulary lives here; the Research Core stays domain-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from research_core import OutputSpec, ResearchSpec, RuleRegistry

from . import rules

PACK_ID = "funds"
# Persisted on every run this pack creates. 0.2.0 = the first version with a
# real production contract: stage prompts that declare the output schema, and
# persist-then-extract-then-validate at the paid-output boundary. A 0.1.0 run is
# NOT comparable — its passes were sent a bare label as their system prompt.
PACK_VERSION = "0.2.0"


@dataclass
class FundsMandate:
    """A Funds research mandate: what the analyst is actually asking for.
    Serialised into `ResearchSpec.params` so it lives in run.json."""
    geography: list[str] = field(default_factory=list)     # ["RU", "Baltics"]
    window_from: int | None = None                          # vintage window start
    window_to: int | None = None
    filters: dict = field(default_factory=dict)             # {"stage": "series_a", ...}
    depth: str = "landscape"                                # landscape | standard | deep
    target_count: int = 5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FundsMandate":
        d = d or {}
        return cls(geography=list(d.get("geography") or []),
                   window_from=d.get("window_from"), window_to=d.get("window_to"),
                   filters=dict(d.get("filters") or {}),
                   depth=d.get("depth", "landscape"),
                   target_count=int(d.get("target_count", 5)))

    def in_window(self, vintage) -> bool:
        try:
            v = int(vintage)
        except (TypeError, ValueError):
            return True                     # unknown vintage never filters out
        if self.window_from is not None and v < self.window_from:
            return False
        if self.window_to is not None and v > self.window_to:
            return False
        return True


class FundsPack:
    """The first ResearchPack. Contributes deterministic rules, outputs and a
    default spec; owns no core machinery."""

    id = PACK_ID
    version = PACK_VERSION

    def register_rules(self, registry: RuleRegistry) -> None:
        rules.register(registry)

    def outputs(self) -> list[OutputSpec]:
        return [
            OutputSpec("fund_landscape", "json",
                       "Landscape of candidate managers/vehicles with provenance."),
            OutputSpec("fund_graph", "json",
                       "Per-target typed graph (manco/vehicle/person/portfolio/deal/CV)."),
            OutputSpec("deep_dive", "json",
                       "Child-run expansion: deals, portfolio companies, people."),
        ]

    def default_spec(self, mandate: FundsMandate | None = None) -> ResearchSpec:
        m = mandate or FundsMandate()
        return ResearchSpec(
            pack_id=self.id,
            geo=",".join(m.geography) or None,
            params={"mandate": m.to_dict()},
            rule_ids=list(rules.ALL_RULE_IDS),
            outputs=[o.name for o in self.outputs()],
        )

    def registry(self) -> RuleRegistry:
        """A fresh RuleRegistry with this pack's rules registered."""
        reg = RuleRegistry()
        self.register_rules(reg)
        return reg
