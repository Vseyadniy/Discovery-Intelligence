"""Zero-call Preview — the read-only exposure report shown BEFORE any paid work.

Makes no model call, no search call, and no network request of any kind: it
reads the *configured* budgets/limits from `src.model_router` + the environment
and multiplies them by the plan the controller would execute. Every number is
derived from configuration, never estimated.

Cost is reported only when `TOKEN_PRICE_IN`/`TOKEN_PRICE_OUT` are configured;
otherwise the report says so explicitly rather than guessing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from research_core import Limits

from .live_pass import preflight, LIVE_MODEL_MODE, LIVE_SEARCH_PROVIDER


@dataclass
class LivePlan:
    """The exact shape of the intended run — one entry per PAID pass."""
    label: str
    mandate_text: str = ""
    landscape_targets: int = 3        # candidates the landscape should return
    research_targets: int = 3         # approved managers actually researched
    deep_dives: int = 1               # deep dives after the landscape
    landscape_budget: int = 12        # tool calls allowed to the landscape pass
    research_budget: int = 18         # tool calls per manager research pass
    deep_dive_budget: int = 12        # tool calls for the deep-dive pass
    max_tokens_per_pass: int = 16000  # model_router.collect max_tokens
    allow_extend: bool = False        # no earned budget extension on run #1
    limits: Limits = field(default_factory=lambda: Limits(max_steps=4))

    @property
    def paid_passes(self) -> int:
        """Landscape + one pass per researched manager + deep dives.
        Repair passes are NOT included: repair is analyst-driven in this slice
        and would be an explicit extra approval."""
        return 1 + self.research_targets + self.deep_dives

    @property
    def tool_call_ceiling(self) -> int:
        return (self.landscape_budget
                + self.research_budget * self.research_targets
                + self.deep_dive_budget * self.deep_dives)

    @property
    def token_ceiling(self) -> int:
        """Model OUTPUT ceiling the controller can authorise (max_tokens is a
        per-response cap; input tokens are not bounded by configuration)."""
        return self.max_tokens_per_pass * self.paid_passes


def build_preview(plan: LivePlan) -> dict:
    """Zero-call. Returns the structured exposure report."""
    cfg = preflight()
    ready = (cfg["model_mode"] == LIVE_MODEL_MODE
             and cfg["search_provider"] == LIVE_SEARCH_PROVIDER
             and cfg["model_key_present"] and cfg["search_key_present"]
             and not cfg["quota_flag"])
    blockers = []
    if cfg["model_mode"] != LIVE_MODEL_MODE:
        blockers.append(f"AGENT_MODE={cfg['model_mode']} (need {LIVE_MODEL_MODE})")
    if cfg["search_provider"] != LIVE_SEARCH_PROVIDER:
        blockers.append(f"SEARCH_PROVIDER={cfg['search_provider']} (need {LIVE_SEARCH_PROVIDER})")
    if not cfg["model_key_present"]:
        blockers.append("DEEPSEEK_API_KEY missing")
    if not cfg["search_key_present"]:
        blockers.append("TAVILY_API_KEY/SEARCH_API_KEY missing")
    if cfg["quota_flag"]:
        blockers.append("search quota flag is set (sticky) — reset before running")
    if cfg["concurrency"] != 1:
        blockers.append(f"DS_COMPANY_CONCURRENCY={cfg['concurrency']} (run #1 is sequential)")

    return {
        "plan": {
            "label": plan.label,
            "mandate": plan.mandate_text,
            "landscape_targets": plan.landscape_targets,
            "research_targets": plan.research_targets,
            "deep_dives": plan.deep_dives,
            "one_target_per_step": True,
            "allow_budget_extension": plan.allow_extend,
        },
        "providers": {
            "model": f"{cfg['model_mode']}:{cfg['model']}",
            "search": cfg["search_provider"],
            "fallback": cfg["provider_fallback"],
            "concurrency": cfg["concurrency"],
        },
        "exposure": {
            "paid_passes": plan.paid_passes,
            "tool_call_ceiling": plan.tool_call_ceiling,
            "tool_calls_per_pass": {
                "landscape": plan.landscape_budget,
                "research_each": plan.research_budget,
                "deep_dive": plan.deep_dive_budget,
            },
            "max_output_tokens_per_pass": plan.max_tokens_per_pass,
            "max_output_tokens_total": plan.token_ceiling,
            "budget_extension_per_pass": (cfg["budget_extend_default"]
                                          if plan.allow_extend else 0),
            "searches_upper_bound": plan.tool_call_ceiling,   # every tool call could be a search
        },
        "stop_conditions": {
            "controller_max_steps": plan.limits.max_steps,
            "scope_approval_required_before_research": True,
            "paid_work_approval_required": True,
            "pause_stop_resume": "file-derived (control.json in the run folder)",
            "sticky_search_quota": "HTTP 402 sets QUOTA_EXHAUSTED → no further searches",
            "provider_failures": "classified (timeout/stream/quota/budget/parse/provider)",
            "no_silent_provider_switch": True,
        },
        "artifacts": [
            "logs/<run>/run.json — mandate, spec, pack_version",
            "logs/<run>/events.jsonl — every decision + per-pass usage",
            "logs/<run>/landscape.json — candidate managers",
            "logs/<run>/scope.json — the approved subset (human gate)",
            "logs/<run>/targets/<t>.json — accepted graph + verdict + issues",
            "logs/<run>/rejected/<t>.json — rejected graph + issues (repair queue)",
            "logs/<child>/… — deep-dive child run (parent_run_id, parent_target)",
        ],
        "evidence_to_inspect_after": [
            "events.jsonl: pass_usage (tokens_in/out, tool_calls, searches, fetches)",
            "events.jsonl: target_researched verdict + codes per manager",
            "whether rules fired on REAL sources, and any false positives",
            "grounding: did an ungrounded AUM/fund-size get downgraded?",
            "manager-vs-vehicle typing in landscape.json (the core domain risk)",
            "search_errors / search_rate_limited / search_denied counters",
            "wall-clock seconds per pass vs the streaming timeouts",
        ],
        "cost": ("not reported — TOKEN_PRICE_IN/TOKEN_PRICE_OUT are not configured"
                 if not cfg["pricing_configured"] else "pricing configured"),
        "ready_to_run": ready,
        "blockers": blockers,
    }


def render_preview(report: dict) -> str:
    """Human-readable Preview (what the operator approves)."""
    p, pr, ex, st = (report["plan"], report["providers"],
                     report["exposure"], report["stop_conditions"])
    lines = [
        f"PREVIEW (read-only, zero calls) — {p['label']}",
        f"  mandate: {p['mandate']}",
        "",
        f"  providers      : model {pr['model']} · search {pr['search']} · "
        f"fallback={pr['fallback']} · concurrency={pr['concurrency']}",
        "",
        "  MAXIMUM LIVE EXPOSURE",
        f"    paid passes                : {ex['paid_passes']} "
        f"(1 landscape + {p['research_targets']} research + {p['deep_dives']} deep dive)",
        f"    tool-call ceiling          : {ex['tool_call_ceiling']} "
        f"(landscape {ex['tool_calls_per_pass']['landscape']} + "
        f"{p['research_targets']}×{ex['tool_calls_per_pass']['research_each']} + "
        f"{p['deep_dives']}×{ex['tool_calls_per_pass']['deep_dive']})",
        f"    searches (upper bound)     : ≤ {ex['searches_upper_bound']}",
        f"    max output tokens / pass   : {ex['max_output_tokens_per_pass']:,}",
        f"    max output tokens / run    : {ex['max_output_tokens_total']:,}",
        f"    earned budget extension    : {ex['budget_extension_per_pass']} "
        f"(extension {'ENABLED' if p['allow_budget_extension'] else 'DISABLED'})",
        f"    cost                       : {report['cost']}",
        "",
        "  STOP CONDITIONS",
        f"    controller max_steps       : {st['controller_max_steps']}",
        f"    scope approval before research : {st['scope_approval_required_before_research']}",
        f"    paid-work approval         : {st['paid_work_approval_required']}",
        f"    pause/stop/resume          : {st['pause_stop_resume']}",
        f"    search quota               : {st['sticky_search_quota']}",
        f"    provider failures          : {st['provider_failures']}",
        "",
        "  ARTIFACTS",
    ]
    lines += [f"    - {a}" for a in report["artifacts"]]
    lines += ["", "  EVIDENCE TO INSPECT AFTER THE RUN"]
    lines += [f"    - {e}" for e in report["evidence_to_inspect_after"]]
    lines += ["", f"  ready_to_run: {report['ready_to_run']}"]
    if report["blockers"]:
        lines += ["  BLOCKERS:"] + [f"    ! {b}" for b in report["blockers"]]
    return "\n".join(lines)


# ── the proposed first live run ──────────────────────────────────────────────
EUROPEAN_PE_MANDATE = (
    "Find European private equity managers investing in B2B software and "
    "tech-enabled services, with evidence of active buy-and-build activity "
    "during 2022–2026.")


def european_pe_plan() -> LivePlan:
    """The exact plan proposed for the first controlled live Funds run."""
    return LivePlan(
        label="European PE — B2B software buy-and-build (2022–2026)",
        mandate_text=EUROPEAN_PE_MANDATE,
        landscape_targets=3, research_targets=3, deep_dives=1,
        landscape_budget=12, research_budget=18, deep_dive_budget=12,
        max_tokens_per_pass=16000, allow_extend=False,
        limits=Limits(max_steps=4, max_tool_calls=78, max_tokens=64000),
    )
