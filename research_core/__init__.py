"""Research Core — a small, deterministic, provider-agnostic foundation shared
by future specialised intelligence products (Funds, Assets, Pricing, Telegram…).

Design rules (see RESEARCH_CORE_HANDOFF.md):
  * pure stdlib; imports NOTHING from `src/` (the Company Intelligence app), so
    it cannot affect the company workflow or its tests;
  * mechanisms only — persistence, ledger, validation framework, scoped repair,
    controller vocabulary, gateway contracts, pack/spec seam;
  * reliability-critical logic is CODE (rules are registered callables); an LLM
    may select capabilities but never author executable validation;
  * production model/retrieval adapters live in the company code and are wrapped
    behind the gateway contracts, not imported here.
"""
from __future__ import annotations

from ._util import now_iso, slugify, is_empty
from .ledger import EventLedger
from .runstore import RunStore, RunHandle
from .validation import Issue, ValidationResult, RuleRegistry, REJECT, WARN
from .repair import RepairPatch, apply_patch
from .control import (
    Limits, ControllerSnapshot, RunControl,
    TERMINAL_STATES, SUCCESS_STATES, is_terminal, classify,
    COMPLETE, COMPLETE_WITH_GAPS, NEEDS_REVIEW, STOPPED_QUOTA, STOPPED_PROVIDER,
    STOPPED_BUDGET, STOPPED_NO_PROGRESS, STOPPED_USER, INTERRUPTED, BLOCKED_INPUT,
)
from .gateways import (
    SearchResult, FetchedPage, ModelResult, ResearchPassResult,
    ResearchPass, ModelGateway, RetrievalGateway, Grounding,
    NullModelGateway, NullRetrievalGateway, InMemorySourceLog, ScriptedResearchPass,
)
from .spec import OutputSpec, ResearchSpec, ResearchPack, PackRegistry, REGISTRY

__all__ = [
    "now_iso", "slugify", "is_empty",
    "EventLedger", "RunStore", "RunHandle",
    "Issue", "ValidationResult", "RuleRegistry", "REJECT", "WARN",
    "RepairPatch", "apply_patch",
    "Limits", "ControllerSnapshot", "RunControl",
    "TERMINAL_STATES", "SUCCESS_STATES", "is_terminal", "classify",
    "COMPLETE", "COMPLETE_WITH_GAPS", "NEEDS_REVIEW", "STOPPED_QUOTA",
    "STOPPED_PROVIDER", "STOPPED_BUDGET", "STOPPED_NO_PROGRESS", "STOPPED_USER",
    "INTERRUPTED", "BLOCKED_INPUT",
    "SearchResult", "FetchedPage", "ModelResult", "ResearchPassResult",
    "ResearchPass", "ModelGateway", "RetrievalGateway", "Grounding",
    "NullModelGateway", "NullRetrievalGateway", "InMemorySourceLog",
    "ScriptedResearchPass",
    "OutputSpec", "ResearchSpec", "ResearchPack", "PackRegistry", "REGISTRY",
]
