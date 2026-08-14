"""Funds Intelligence — the first ResearchPack built on `research_core`.

An OFFLINE vertical slice: it pressure-tests the core against a genuinely
different research domain (funds are a graph of distinct entity kinds, not one
flat record per company). No live model/search adapters, no UI, no planner.

Layout:
  model.py       typed graph: management company / fund vehicle / person /
                 portfolio company / deal / continuation vehicle / evidence,
                 with four-valued claim + relationship states
  rules.py       deterministic Funds gate rules (registered callables)
  pack.py        FundsPack (ResearchPack) + FundsMandate
  controller.py  landscape → scope approval → one fund per safe step → gate →
                 scoped repair → linked deep-dive child run
  fixtures/      a few deliberately tricky synthetic mandates (scripted passes)
"""
from __future__ import annotations

from .pack import FundsPack, FundsMandate, PACK_ID, PACK_VERSION
from .controller import FundsController, AWAITING_SCOPE, build_graph, expand_graph
from .model import (
    FundGraph, Node, Edge, Claim, Evidence,
    MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, PORTFOLIO_COMPANY, DEAL,
    CONTINUATION_VEHICLE, EVIDENCE, NODE_KINDS,
    CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED, CLAIM_STATES,
    MANAGES, EMPLOYS, INVESTED_IN, DEAL_OF, DEAL_TARGET, CONTINUES,
)
from .extraction import (
    MalformedPassOutput, extract_json,
    validate_landscape, validate_research, validate_deep_dive,
)
from .rawstore import save_raw, list_raw
from . import rules
from . import prompts

__all__ = [
    "FundsPack", "FundsMandate", "PACK_ID", "PACK_VERSION",
    "MalformedPassOutput", "extract_json",
    "validate_landscape", "validate_research", "validate_deep_dive",
    "save_raw", "list_raw", "prompts",
    "FundsController", "AWAITING_SCOPE", "build_graph", "expand_graph",
    "FundGraph", "Node", "Edge", "Claim", "Evidence",
    "MANAGEMENT_COMPANY", "FUND_VEHICLE", "PERSON", "PORTFOLIO_COMPANY", "DEAL",
    "CONTINUATION_VEHICLE", "EVIDENCE", "NODE_KINDS",
    "CONFIRMED", "PARTIALLY_CONFIRMED", "INFERRED", "UNRESOLVED", "CLAIM_STATES",
    "MANAGES", "EMPLOYS", "INVESTED_IN", "DEAL_OF", "DEAL_TARGET", "CONTINUES",
    "rules",
]
