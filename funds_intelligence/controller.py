"""Funds controller — the offline vertical slice workflow.

State machine (all state derived from files in the run folder, never memory):

  created → landscape → awaiting-scope-approval → researching (one fund per
  safe step) → repair* → complete / complete-with-gaps / needs-review

Safety mirrors the company Auto model: the landscape STOPS for explicit scope
approval before any per-target work; every step is interruptible via the core
`RunControl` (pause/stop honoured between steps, survives a restart); progress
is snapshot-diffed so a stalled run terminates instead of looping.

A deep dive is a *child run*: its own run folder, linked to the parent by
`parent_run_id` + `parent_target`, reusing the parent's accepted evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from research_core import (
    RunStore, RunControl, ControllerSnapshot, Limits, RepairPatch, apply_patch,
    COMPLETE, COMPLETE_WITH_GAPS, NEEDS_REVIEW, STOPPED_USER, STOPPED_BUDGET,
    STOPPED_NO_PROGRESS, BLOCKED_INPUT,
)

from .model import (
    FundGraph, Node, Edge, Evidence, Claim,
    MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, PORTFOLIO_COMPANY, DEAL,
    CONTINUATION_VEHICLE, MANAGES, EMPLOYS, INVESTED_IN, DEAL_OF, DEAL_TARGET,
    CONTINUES, CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED,
    TIER_PRIMARY, TIER_REPUTABLE, TIER_WEAK,
)
from .pack import FundsPack, FundsMandate

AWAITING_SCOPE = "awaiting-scope-approval"

# Source-tier heuristic for fixture/real URLs (deterministic, code-owned).
_TIER_BY_HOST = {"reg.example": TIER_PRIMARY, "press.example": TIER_REPUTABLE,
                 "gp.example": TIER_WEAK}


def _tier_for(url: str) -> int:
    for host, tier in _TIER_BY_HOST.items():
        if host in (url or ""):
            return tier
    return TIER_WEAK


def _state_of(d, default=UNRESOLVED) -> str:
    if isinstance(d, dict):
        return d.get("state", default)
    return default


def _val(d):
    return d.get("value") if isinstance(d, dict) else d


class FundsController:
    """Drives one Funds run offline against a scripted ResearchPass."""

    def __init__(self, store: RunStore, pack: FundsPack | None = None,
                 limits: Limits | None = None):
        self.store = store
        self.pack = pack or FundsPack()
        self.registry = self.pack.registry()
        self.limits = limits or Limits(max_steps=50)

    # ── run creation ──────────────────────────────────────────────────────
    def create_run(self, label: str, mandate: FundsMandate):
        spec = self.pack.default_spec(mandate)
        h = self.store.create(pack_id=self.pack.id, label=label,
                              pack_version=self.pack.version,
                              meta={"spec": spec.to_meta(), "status": "created",
                                    "kind": "landscape"})
        h.event("mandate_defined", geography=mandate.geography,
                window=[mandate.window_from, mandate.window_to],
                depth=mandate.depth, target_count=mandate.target_count,
                filters=mandate.filters)
        return h

    def create_deep_dive(self, parent, target_name: str):
        """A linked CHILD run for one accepted parent target."""
        gpath = parent.path("targets", f"{_slug(target_name)}.json")
        if not gpath.exists():
            raise FileNotFoundError(f"no accepted target graph for {target_name!r}")
        pmeta = parent.load_meta()
        spec = dict(pmeta.get("spec") or {})
        h = self.store.create(pack_id=self.pack.id,
                              label=f"{target_name} deep dive",
                              pack_version=self.pack.version,
                              meta={"spec": spec, "status": "created",
                                    "kind": "deep_dive",
                                    "parent_run_id": parent.run_id,
                                    "parent_target": target_name})
        # reuse the parent's accepted graph as the child's starting evidence base
        h.subdir("targets")
        h.path("targets", f"{_slug(target_name)}.json").write_text(
            gpath.read_text(encoding="utf-8"), encoding="utf-8")
        inherited = FundGraph.from_dict(json.loads(gpath.read_text(encoding="utf-8")))
        h.event("deep_dive_created", parent_run_id=parent.run_id,
                parent_target=target_name,
                inherited_nodes=len(inherited.nodes),
                inherited_evidence=len(inherited.evidence))
        return h

    # ── paid-work approval (must precede ANY paid pass) ───────────────────
    def approve_paid_work(self, h, *, approved_by: str = "operator",
                          note: str = "") -> dict:
        """Record explicit approval to spend money on this run. Persisted in
        run.json + events.jsonl, so a resumed session cannot silently re-acquire
        it. Mirrors the company Auto safety model."""
        meta = h.update_meta(paid_work_approved=True,
                             paid_work_approved_by=approved_by)
        h.event("paid_work_approved", approved_by=approved_by, note=note)
        return meta

    def paid_work_approved(self, h) -> bool:
        return bool(h.load_meta().get("paid_work_approved"))

    def require_paid_approval(self, h, action: str) -> None:
        """Raise BEFORE a paid pass unless approval is on file. Offline passes
        (ScriptedResearchPass) are unaffected — the guard is only applied by
        callers that run a live adapter."""
        if not self.paid_work_approved(h):
            h.event("paid_work_blocked", action=action)
            raise PermissionError(
                f"{action} would spend money but this run has no paid-work "
                f"approval — call approve_paid_work() first (see Preview).")

    # ── phase 1: landscape ────────────────────────────────────────────────
    def run_landscape(self, h, research_pass) -> list[dict]:
        mandate = FundsMandate.from_dict(
            (h.load_meta().get("spec") or {}).get("params", {}).get("mandate"))
        _guard_paid(self, h, research_pass, "landscape")
        res = research_pass.run_pass("funds landscape", f"mandate: {mandate.to_dict()}")
        _log_usage(h, research_pass, stage="landscape")
        data = json.loads(res.text or "{}")
        candidates = []
        for c in (data.get("candidates") or [])[: max(1, mandate.target_count)]:
            if not mandate.in_window(c.get("vintage")):
                continue
            candidates.append(c)
        h.path("landscape.json").write_text(
            json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        h.update_meta(status=AWAITING_SCOPE, landscape_count=len(candidates))
        h.event("landscape_built", count=len(candidates),
                names=[c.get("name") for c in candidates], engine=res.engine)
        h.event("awaiting_scope_approval", count=len(candidates))
        return candidates

    def approve_scope(self, h, approved_names: list[str]) -> list[str]:
        """Explicit human gate — nothing per-target runs before this."""
        cands = json.loads(h.path("landscape.json").read_text(encoding="utf-8"))["candidates"]
        known = {c["name"] for c in cands}
        approved = [n for n in approved_names if n in known]
        h.path("scope.json").write_text(
            json.dumps({"approved": approved}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        h.update_meta(status="researching", approved_count=len(approved))
        h.event("scope_approved", approved=approved,
                rejected=sorted(known - set(approved)))
        return approved

    # ── phase 2: one target per safe step ─────────────────────────────────
    def _stems(self, h, folder: str) -> set[str]:
        d = h.path(folder)
        return {p.stem for p in d.glob("*.json")} if d.exists() else set()

    def approved_targets(self, h) -> list[str]:
        sp = h.path("scope.json")
        if not sp.exists():
            return []
        return list(json.loads(sp.read_text(encoding="utf-8"))["approved"])

    def pending_targets(self, h) -> list[str]:
        """File-derived research queue: approved targets not yet ATTEMPTED.
        A target is attempted once it has either an accepted graph (`targets/`)
        or a rejected record (`rejected/`) — a rejected target belongs to the
        repair queue, not the research queue. (Counting only accepted graphs
        made rejected targets immortal and produced a false no-progress stop.)"""
        attempted = self._stems(h, "targets") | self._stems(h, "rejected")
        return [n for n in self.approved_targets(h) if _slug(n) not in attempted]

    def rejected_targets(self, h) -> list[str]:
        """File-derived repair queue."""
        rej = self._stems(h, "rejected")
        return [n for n in self.approved_targets(h) if _slug(n) in rej]

    def research_one(self, h, research_pass, target: str) -> dict:
        """One safe step = exactly one fund target researched, gated, saved."""
        _guard_paid(self, h, research_pass, f"research «{target}»")
        res = research_pass.run_pass(f"funds research: {target}", target)
        _log_usage(h, research_pass, stage="research", target=target)
        graph = build_graph(json.loads(res.text or "{}"), res.grounding)
        result = self.registry.run(graph.to_dict(), self.rule_ids(h))
        h.subdir("targets")
        payload = {"target": target, "graph": graph.to_dict(),
                   "verdict": result.verdict, "issues": result.as_dicts()}
        (h.path("targets", f"{_slug(target)}.json") if result.accepted
         else h.path("rejected", f"{_slug(target)}.json"))
        if result.accepted:
            h.path("targets", f"{_slug(target)}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            h.subdir("rejected")
            h.path("rejected", f"{_slug(target)}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        h.event("target_researched", target=target, verdict=result.verdict,
                nodes=len(graph.nodes), edges=len(graph.edges),
                evidence=len(graph.evidence),
                codes=sorted(result.codes()), engine=res.engine)
        return payload

    def rule_ids(self, h) -> list[str]:
        return list((h.load_meta().get("spec") or {}).get("rule_ids") or [])

    # ── phase 3: scoped repair ────────────────────────────────────────────
    def repair_target(self, h, target: str, patch: RepairPatch) -> dict:
        """Apply a SCOPED patch to a rejected target's graph, re-gate, and
        promote it if it now passes. Unrelated accepted data is preserved by
        the core `apply_patch` policy."""
        rp = h.path("rejected", f"{_slug(target)}.json")
        payload = json.loads(rp.read_text(encoding="utf-8"))
        graph = payload["graph"]
        merged = apply_patch(graph, patch, container="nodes",
                             flags_key="review_flags", is_empty=_node_is_empty)
        result = self.registry.run(merged, self.rule_ids(h))
        payload = {"target": target, "graph": merged, "verdict": result.verdict,
                   "issues": result.as_dicts()}
        h.event("target_repaired", target=target, verdict=result.verdict,
                scope=sorted(patch.scope), codes=sorted(result.codes()))
        if result.accepted:
            h.subdir("targets")
            h.path("targets", f"{_slug(target)}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            rp.unlink()
        else:
            rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return payload

    # ── phase 4: deep dive expansion ──────────────────────────────────────
    def expand_deep_dive(self, h, research_pass) -> dict:
        """Expand deals / portfolio companies / people onto the INHERITED parent
        graph, preserving the parent's accepted nodes and evidence."""
        meta = h.load_meta()
        target = meta["parent_target"]
        gpath = h.path("targets", f"{_slug(target)}.json")
        payload = json.loads(gpath.read_text(encoding="utf-8"))
        graph = FundGraph.from_dict(payload["graph"])
        before_nodes, before_ev = len(graph.nodes), len(graph.evidence)

        _guard_paid(self, h, research_pass, f"deep dive «{target}»")
        res = research_pass.run_pass(f"deep dive: {target}", target)
        _log_usage(h, research_pass, stage="deep_dive", target=target)
        expand_graph(graph, json.loads(res.text or "{}"), res.grounding)

        result = self.registry.run(graph.to_dict(), self.rule_ids(h))
        out = {"target": target, "graph": graph.to_dict(),
               "verdict": result.verdict, "issues": result.as_dicts()}
        gpath.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        h.update_meta(status=COMPLETE if result.accepted else NEEDS_REVIEW)
        h.event("deep_dive_expanded", target=target, verdict=result.verdict,
                nodes_before=before_nodes, nodes_after=len(graph.nodes),
                evidence_before=before_ev, evidence_after=len(graph.evidence),
                preserved=before_nodes <= len(graph.nodes),
                codes=sorted(result.codes()))
        return out

    # ── controller loop (pause/stop/limits/no-progress) ───────────────────
    def snapshot(self, h) -> ControllerSnapshot:
        """Fingerprint of MEANINGFUL persisted state — not just the pending
        count. Includes, per attempted target, its verdict plus node/edge/
        evidence totals, so a step that repairs or enriches a graph in place
        registers as progress even when no file appears or disappears.
        Deterministic and entirely file-derived (survives a restart)."""
        state = {}
        for folder in ("targets", "rejected"):
            d = h.path(folder)
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    state[f"{folder}/{p.stem}"] = "unreadable"
                    continue
                g = payload.get("graph") or {}
                state[f"{folder}/{p.stem}"] = [
                    payload.get("verdict", ""),
                    len(g.get("nodes") or {}),
                    len(g.get("edges") or []),
                    len(g.get("evidence") or {}),
                ]
        return ControllerSnapshot.of(state)

    def run_until_terminal(self, h, pass_for, control: RunControl | None = None) -> str:
        """Drive research one target per step until a terminal state.
        `pass_for(target)` supplies that target's scripted ResearchPass."""
        control = control or RunControl(run_dir=h.dir)
        steps = 0
        # Baseline BEFORE the first step, so a successful first step is compared
        # against real prior state (comparing against None made every first step
        # "progress" and hid genuine early stalls).
        last = self.snapshot(h)
        while True:
            if control.should_stop():
                h.update_meta(status=STOPPED_USER)
                h.event("auto_terminal", state=STOPPED_USER, reason="user stop")
                return STOPPED_USER
            if control.should_pause():
                h.update_meta(status="paused")
                h.event("auto_paused", step=steps)
                return "paused"
            hit = self.limits.exceeded(steps=steps)
            if hit:
                h.update_meta(status=STOPPED_BUDGET)
                h.event("auto_terminal", state=STOPPED_BUDGET, reason=hit)
                return STOPPED_BUDGET

            pending = self.pending_targets(h)
            if not pending:
                break
            target = pending[0]                      # ONE target per safe step
            self.research_one(h, pass_for(target), target)
            steps += 1

            snap = self.snapshot(h)
            if not snap.changed_from(last):
                h.update_meta(status=STOPPED_NO_PROGRESS)
                h.event("auto_terminal", state=STOPPED_NO_PROGRESS,
                        reason="snapshot unchanged after a step")
                return STOPPED_NO_PROGRESS
            last = snap

        rejected = list(h.path("rejected").glob("*.json")) if h.path("rejected").exists() else []
        state = NEEDS_REVIEW if rejected else COMPLETE
        h.update_meta(status=state)
        h.event("auto_terminal", state=state,
                accepted=len(list(h.path("targets").glob("*.json"))) if h.path("targets").exists() else 0,
                rejected=len(rejected))
        return state


# ── graph building (deterministic, from a pass reply + its grounding) ────────
def _evidence_from(graph: FundGraph, url: str, grounding, marketing=False) -> str | None:
    if not url:
        return None
    excerpt = ""
    if grounding is not None and getattr(grounding, "_pages", None):
        excerpt = grounding._pages.get(url, "")
    ev = graph.add_evidence(Evidence(url=url, tier=_tier_for(url), excerpt=excerpt,
                                     is_marketing=marketing))
    return ev.id


def _claim(graph: FundGraph, spec, grounding) -> Claim:
    """Build a Claim, DOWNGRADING to unresolved when grounding does not support
    the value (a fabricated number next to a real URL never stays factual)."""
    if not isinstance(spec, dict):
        return Claim(value=spec, state=UNRESOLVED)
    url = spec.get("source", "")
    eid = _evidence_from(graph, url, grounding, marketing=bool(spec.get("marketing")))
    state = spec.get("state", UNRESOLVED)
    value = spec.get("value")
    if eid and grounding is not None and value not in (None, ""):
        if state in (CONFIRMED, PARTIALLY_CONFIRMED) and not grounding.supports_value(str(value)):
            state = INFERRED          # claimed as fact but not present in the page text
    return Claim(value=value, state=state,
                 evidence_ids=[eid] if eid else [], note=spec.get("note", ""))


def build_graph(reply: dict, grounding) -> FundGraph:
    """Turn one research reply into a typed FundGraph. Pure + deterministic:
    the LLM proposes values, this code decides types, states and provenance."""
    g = FundGraph()
    mc_spec = reply.get("management_company") or {}
    mc = None
    if mc_spec:
        mc = g.add_node(Node(MANAGEMENT_COMPANY, mc_spec.get("name", "")))
        for fname, fspec in mc_spec.items():
            if fname == "name":
                continue
            mc.set(fname, _claim(g, fspec, grounding))

    for v in reply.get("vehicles") or []:
        node = g.add_node(Node(FUND_VEHICLE, v.get("name", "")))
        for fname in ("fund_size", "aum", "strategy", "active_strategy"):
            if fname in v:
                node.set(fname, _claim(g, v[fname], grounding))
        if v.get("vintage") is not None:
            # a vintage inherits the vehicle's own evidence — asserting it as
            # fact with no source would (correctly) trip `unsupported-claim`
            ev = [i for c in node.claims.values() for i in c.evidence_ids]
            node.set("vintage", Claim(value=v["vintage"],
                                      state=CONFIRMED if ev else UNRESOLVED,
                                      evidence_ids=ev[:1]))
        if mc is not None:
            ev = [i for c in node.claims.values() for i in c.evidence_ids]
            g.add_edge(Edge(MANAGES, mc.id, node.id,
                            state=CONFIRMED if ev else UNRESOLVED,
                            evidence_ids=ev[:1]))

    for cv in reply.get("continuation_vehicles") or []:
        node = g.add_node(Node(CONTINUATION_VEHICLE, cv.get("name", "")))
        if "fund_size" in cv:
            node.set("fund_size", _claim(g, cv["fund_size"], grounding))
        if cv.get("vintage") is not None:
            ev = [i for c in node.claims.values() for i in c.evidence_ids]
            node.set("vintage", Claim(value=cv["vintage"],
                                      state=CONFIRMED if ev else UNRESOLVED,
                                      evidence_ids=ev[:1]))
        pred = next((n for n in g.by_kind(FUND_VEHICLE)
                     if n.name == cv.get("continues")), None)
        if pred is not None:
            ev = [i for c in node.claims.values() for i in c.evidence_ids]
            g.add_edge(Edge(CONTINUES, node.id, pred.id,
                            state=CONFIRMED if ev else UNRESOLVED,
                            evidence_ids=ev[:1]))

    for p in reply.get("people") or []:
        node = g.add_node(Node(PERSON, p.get("name", "")))
        eid = _evidence_from(g, p.get("source", ""), grounding)
        state = p.get("state", CONFIRMED) if eid else UNRESOLVED
        node.set("role", Claim(value=p.get("role"), state=state,
                               evidence_ids=[eid] if eid else []))
        if p.get("role_status"):
            node.set("role_status", Claim(value=p["role_status"], state=state,
                                          evidence_ids=[eid] if eid else []))
        if mc is not None:
            props = {k: p[k] for k in ("from", "to") if p.get(k)}
            if p.get("role_status") == "current":
                props["current"] = True
            g.add_edge(Edge(EMPLOYS, mc.id, node.id,
                            state=CONFIRMED if eid else UNRESOLVED,
                            evidence_ids=[eid] if eid else [], props=props))

    for pc in reply.get("portfolio") or []:
        node = g.add_node(Node(PORTFOLIO_COMPANY, pc.get("company", "")))
        evs = [i for i in (pc.get("evidence") or []) if i]
        vehicle = next(iter(g.by_kind(FUND_VEHICLE)), None)
        if vehicle is not None:
            g.add_edge(Edge(INVESTED_IN, vehicle.id, node.id,
                            state=pc.get("state", UNRESOLVED), evidence_ids=evs))
    return g


def expand_graph(g: FundGraph, reply: dict, grounding) -> FundGraph:
    """Deep dive: ADD deals / portfolio companies / people onto an existing
    graph. Never removes or rewrites inherited nodes or evidence."""
    for d in reply.get("deals") or []:
        deal = g.add_node(Node(DEAL, d.get("name", "")))
        eid = _evidence_from(g, d.get("source", ""), grounding)
        for fname in ("amount", "year"):
            if d.get(fname) is not None:
                deal.set(fname, Claim(value=d[fname], state=d.get("state", CONFIRMED),
                                      evidence_ids=[eid] if eid else []))
        target = next((n for n in g.by_kind(PORTFOLIO_COMPANY)
                       if n.name == d.get("target")), None)
        if target is None:
            target = g.add_node(Node(PORTFOLIO_COMPANY, d.get("target", "")))
        vehicle = next((n for n in g.by_kind(FUND_VEHICLE)
                        if n.name == d.get("vehicle")), None)
        state = d.get("state", UNRESOLVED)
        if vehicle is not None:
            g.add_edge(Edge(DEAL_OF, deal.id, vehicle.id, state=state,
                            evidence_ids=[eid] if eid else []))
            g.add_edge(Edge(INVESTED_IN, vehicle.id, target.id, state=state,
                            evidence_ids=[eid] if eid else []))
        g.add_edge(Edge(DEAL_TARGET, deal.id, target.id, state=state,
                        evidence_ids=[eid] if eid else []))

    mc = next(iter(g.by_kind(MANAGEMENT_COMPANY)), None)
    for p in reply.get("people") or []:
        node = g.add_node(Node(PERSON, p.get("name", "")))
        eid = _evidence_from(g, p.get("source", ""), grounding)
        node.set("role", Claim(value=p.get("role"), state=p.get("state", CONFIRMED),
                               evidence_ids=[eid] if eid else []))
        if mc is not None:
            props = {k: p[k] for k in ("from", "to") if p.get(k)}
            g.add_edge(Edge(EMPLOYS, mc.id, node.id,
                            state=p.get("state", UNRESOLVED),
                            evidence_ids=[eid] if eid else [], props=props))
    return g


# ── paid-pass guard + usage telemetry ────────────────────────────────────────
def is_paid_pass(research_pass) -> bool:
    """True when the pass will spend money. Duck-typed on purpose: the offline
    ScriptedResearchPass has no `costs_money` attribute, the live adapter sets
    it True — so this module never imports the provider stack."""
    return bool(getattr(research_pass, "costs_money", False))


def _guard_paid(controller, h, research_pass, action: str) -> None:
    if is_paid_pass(research_pass):
        controller.require_paid_approval(h, action)


def _log_usage(h, research_pass, *, stage: str, target: str = "") -> None:
    """Persist what the LAST pass actually consumed. No-op for offline passes
    (nothing was spent); values come from the adapter's SourceLog harvest."""
    usages = getattr(research_pass, "usages", None)
    if not usages:
        return
    ev = usages[-1].as_event() if hasattr(usages[-1], "as_event") else dict(usages[-1])
    h.event("pass_usage", stage=stage, **({"target": target} if target else {}), **ev)


def _node_is_empty(v) -> bool:
    """Emptiness for the `nodes` container used by scoped repair."""
    return v is None or v == {} or v == ""


def _slug(s: str) -> str:
    import re
    s = re.sub(r"[^\w\s-]", "", str(s), flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "target"
