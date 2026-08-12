"""Funds data model — the domain vocabulary the Research Core deliberately
does not know about.

The central modelling decision (and the thing that makes Funds genuinely
different from Company Intelligence) is that a "fund" is NOT one entity. Six
distinct node kinds must never be collapsed, plus evidence:

  MANAGEMENT_COMPANY   the GP / manager firm ("Alpha Capital Partners").
                       Has AUM (across all vehicles), a legal entity, people.
  FUND_VEHICLE         one fund with a vintage and a *fund size*
                       ("Alpha Capital Fund III, 2021, $250m").
                       AUM ≠ fund size — conflating them is the #1 domain error.
  PERSON               a human, whose role is TIME-BOUND (a partner who left in
                       2019 is not "current leadership").
  PORTFOLIO_COMPANY    an investee.
  DEAL                 an investment event linking a fund vehicle to a portfolio
                       company (round, date, amount). Relationships need their
                       own evidence — "X is in the portfolio" is a claim.
  CONTINUATION_VEHICLE a GP-led secondary that carries assets out of an older
                       fund. Its own vehicle, linked to the predecessor.
  EVIDENCE             a source-level record (url, tier, excerpt, retrieved_at)
                       that claims and relationships point at.

Every value is a `Claim`: value + state + evidence ids. Every relationship is an
`Edge` with its own state + evidence. State is explicit and four-valued so that
"we could not find it" is never silently rendered as "it is not so".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict

# ── node kinds ────────────────────────────────────────────────────────────────
MANAGEMENT_COMPANY = "management_company"
FUND_VEHICLE = "fund_vehicle"
PERSON = "person"
PORTFOLIO_COMPANY = "portfolio_company"
DEAL = "deal"
CONTINUATION_VEHICLE = "continuation_vehicle"
EVIDENCE = "evidence"

NODE_KINDS = (MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, PORTFOLIO_COMPANY,
              DEAL, CONTINUATION_VEHICLE, EVIDENCE)

# ── claim / relationship states ───────────────────────────────────────────────
CONFIRMED = "confirmed"                    # ≥1 tier-1/2 source states it directly
PARTIALLY_CONFIRMED = "partially_confirmed"  # supported but incomplete/indirect
INFERRED = "inferred"                      # derived by reasoning, not stated
UNRESOLVED = "unresolved"                  # looked for, not established
CLAIM_STATES = (CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED)

# States that may NOT be presented as established fact in a deliverable.
NON_FACTUAL_STATES = (INFERRED, UNRESOLVED)

# ── edge kinds (relationships carry their own provenance) ─────────────────────
MANAGES = "manages"                  # management_company -> fund_vehicle
EMPLOYS = "employs"                  # management_company -> person (time-bound)
INVESTED_IN = "invested_in"          # fund_vehicle -> portfolio_company (via deal)
DEAL_OF = "deal_of"                  # deal -> fund_vehicle
DEAL_TARGET = "deal_target"          # deal -> portfolio_company
CONTINUES = "continues"              # continuation_vehicle -> predecessor fund
EDGE_KINDS = (MANAGES, EMPLOYS, INVESTED_IN, DEAL_OF, DEAL_TARGET, CONTINUES)

# Source tiers (same spirit as the company registry tiers)
TIER_PRIMARY = 1      # regulator filing, official register, the GP's own disclosure
TIER_REPUTABLE = 2    # established press / recognised database
TIER_WEAK = 3         # blog, marketing page, self-reported profile


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class Evidence:
    """A source-level record. `excerpt` is the text actually seen — grounding
    checks compare claim values against it."""
    url: str
    tier: int = TIER_WEAK
    excerpt: str = ""
    retrieved_at: str = ""
    is_marketing: bool = False     # GP's own promotional copy → never proof of activity
    id: str = field(default_factory=lambda: _nid("ev"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__ or k == "id"})


@dataclass
class Claim:
    """One attribute value with its state and the evidence supporting it."""
    value: object = None
    state: str = UNRESOLVED
    evidence_ids: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"value": self.value, "state": self.state,
                "evidence_ids": list(self.evidence_ids), "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        if not isinstance(d, dict):
            return cls(value=d, state=UNRESOLVED)
        return cls(value=d.get("value"), state=d.get("state", UNRESOLVED),
                   evidence_ids=list(d.get("evidence_ids") or []), note=d.get("note", ""))

    @property
    def is_factual(self) -> bool:
        return self.state in (CONFIRMED, PARTIALLY_CONFIRMED)


@dataclass
class Edge:
    """A relationship with its own state + evidence (never assumed from context)."""
    kind: str
    src: str
    dst: str
    state: str = UNRESOLVED
    evidence_ids: list[str] = field(default_factory=list)
    props: dict = field(default_factory=dict)     # e.g. {"from": "2016", "to": "2019"}
    id: str = field(default_factory=lambda: _nid("edge"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        e = cls(kind=d["kind"], src=d["src"], dst=d["dst"],
                state=d.get("state", UNRESOLVED),
                evidence_ids=list(d.get("evidence_ids") or []),
                props=dict(d.get("props") or {}))
        if d.get("id"):
            e.id = d["id"]
        return e

    @property
    def is_current(self) -> bool:
        """A time-bound edge is current only if it has no end date."""
        return not str(self.props.get("to") or "").strip()


@dataclass
class Node:
    """A typed entity holding Claims. `kind` must be one of NODE_KINDS."""
    kind: str
    name: str
    claims: dict = field(default_factory=dict)     # {field: Claim}
    id: str = ""

    def __post_init__(self):
        if self.kind not in NODE_KINDS:
            raise ValueError(f"unknown node kind {self.kind!r}; allowed {NODE_KINDS}")
        if not self.id:
            self.id = _nid(self.kind[:4])

    def claim(self, name: str) -> Claim:
        return self.claims.get(name) or Claim()

    def set(self, name: str, claim: Claim) -> None:
        self.claims[name] = claim

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "claims": {k: v.to_dict() for k, v in self.claims.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        n = cls(kind=d["kind"], name=d.get("name", ""), id=d.get("id", ""))
        n.claims = {k: Claim.from_dict(v) for k, v in (d.get("claims") or {}).items()}
        return n


@dataclass
class FundGraph:
    """The unit a Funds run persists per target: typed nodes + edges + evidence.
    Serialises to plain JSON so it lives in the run folder like any artifact."""
    nodes: dict = field(default_factory=dict)       # {id: Node}
    edges: list = field(default_factory=list)       # [Edge]
    evidence: dict = field(default_factory=dict)    # {id: Evidence}
    review_flags: list = field(default_factory=list)

    # ── building ──────────────────────────────────────────────────────────
    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        self.edges.append(edge)
        return edge

    def add_evidence(self, ev: Evidence) -> Evidence:
        self.evidence[ev.id] = ev
        return ev

    # ── querying ──────────────────────────────────────────────────────────
    def by_kind(self, kind: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def edges_of(self, kind: str | None = None, src: str | None = None,
                 dst: str | None = None) -> list[Edge]:
        out = self.edges
        if kind is not None:
            out = [e for e in out if e.kind == kind]
        if src is not None:
            out = [e for e in out if e.src == src]
        if dst is not None:
            out = [e for e in out if e.dst == dst]
        return list(out)

    def evidence_for(self, holder) -> list[Evidence]:
        ids = holder.evidence_ids if hasattr(holder, "evidence_ids") else []
        return [self.evidence[i] for i in ids if i in self.evidence]

    def best_tier(self, holder) -> int | None:
        tiers = [e.tier for e in self.evidence_for(holder)]
        return min(tiers) if tiers else None

    # ── serialisation ─────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"nodes": {i: n.to_dict() for i, n in self.nodes.items()},
                "edges": [e.to_dict() for e in self.edges],
                "evidence": {i: e.to_dict() for i, e in self.evidence.items()},
                "review_flags": list(self.review_flags)}

    @classmethod
    def from_dict(cls, d: dict) -> "FundGraph":
        g = cls()
        for i, nd in (d.get("nodes") or {}).items():
            g.nodes[i] = Node.from_dict(nd)
        g.edges = [Edge.from_dict(e) for e in (d.get("edges") or [])]
        for i, ev in (d.get("evidence") or {}).items():
            g.evidence[i] = Evidence.from_dict(ev)
        g.review_flags = list(d.get("review_flags") or [])
        return g
