"""Goal-based Auto Research v1 — the QUANTITATIVE track only.

A thin controller loop over the EXISTING file-derived state machine:
`api_runner.run_next_step` stays the only executor (discovery → per-company
research → gate → bounded repair → blank/flag) and `runs.build_excel` the only
builder. The controller adds NO pipeline logic — it decides, before each step,
whether pressing ⚡ again is still worth it, and stops with a deterministic
terminal state when it is not. Every decision and the terminal reason go to
events.jsonl (`auto_start` / `auto_decision` / `auto_approval` /
`auto_interrupted` / `auto_terminal`).

Safety model (added after the first live run):
  * Auto executes AT MOST ONE company per controller decision — limits, quota,
    approvals, and snapshots are re-checked between every company.
  * Paid work (discovery / research / repair) starts only after explicit
    approval: `--yes`/`--unattended`, `--approve-scope`, or an interactive
    confirmation. No approval → `blocked-input`, zero spend.
  * After discovery the controller STOPS at `awaiting-scope-approval` and
    shows the cohort; research starts only once the scope is approved (the
    approval is persisted in run.json, so a re-run continues).
  * `plan()` (`--plan`) is a read-only preview: current state, next actions,
    affected companies, limits, and an upper-bound execution plan — it can
    never call a provider or the search API.
  * `--finalize-only` allows ONLY deterministic salvage/autofix/gate + Excel;
    it refuses (blocked-input) whenever discovery, research, or model repair
    would be needed — guaranteed zero LLM/search calls.
  * Ctrl-C is caught: `auto_interrupted` is logged with the current action,
    company, snapshot, and observable spend (in-flight pass usage is marked
    incomplete when it cannot be measured exactly); exit code 130; completed
    artifacts stay on disk.

Terminal states (AutoResult.state):
  complete            all records accepted, 0 unresolved fields, Excel built
  complete-with-gaps  all accepted, unresolved (blanked+flagged) fields
                      remain, Excel built
  needs-review        every remaining reject has exhausted the pipeline's own
                      repair / Collector-B caps — more API spend cannot help
  awaiting-scope-approval  discovery done; research paused until the cohort
                      is approved (--approve-scope / --yes / interactive)
  stopped-quota       Brave search quota exhausted; already-researched records
                      finished their repair/blank resolution path, but NO new
                      company research was started
  stopped-provider    consecutive steps produced nothing but transient or
                      provider failures (timeout/stream/provider/quota)
  stopped-budget      a run-level limit was hit (steps, wall time, tool
                      calls, tokens)
  stopped-no-progress consecutive steps changed nothing observable, past the
                      pipeline's own bounded-repair guards
  paused              the user asked to pause (AutoControl) — the session
                      ended cleanly after the current company; a new
                      auto_run on the same folder resumes exactly there
  stopped-user        the user asked to stop (AutoControl) — same clean
                      boundary as paused, different intent label
  interrupted         Ctrl-C — logged and exited cleanly (code 130)
  blocked-input       a setup problem or a missing approval the controller
                      cannot supply itself (keys, provider, confirmation,
                      SystemExit from the pipeline)

Pause/resume/stop need no persistent session: the state machine lives in
files, so «resume» is simply a new auto_run over the same run folder (the
scope approval is persisted in run.json). AutoControl only asks the loop to
end at the next between-companies checkpoint — an in-flight pass is never
killed, so no artifact is ever half-written.

v1 invariants: DeepSeek only, strictly sequential (company concurrency forced
to 1 AND one company per step), quantitative research + Excel only, no
Pause/Resume UI. Progress is detected by comparing state snapshots around each
step, never by parsing summary text.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import api_runner, gate, runs, web_tools
from . import model_router as mr

# events that prove a pass succeeded / failed inside one controller step
_SUCCESS_EVENTS = {"api_discovery", "api_company", "api_repair",
                   "api_collector_b_rerun"}
_FAILED_EVENTS = {"api_company_failed", "api_repair_failed"}
# failure categories (api_runner._error_category) that read as "the provider
# or the pipe is unwell" — a streak of ONLY these stops the run as
# stopped-provider instead of burning the no-progress allowance
_PROVIDER_CATS = {"timeout", "stream", "provider", "quota"}
# controller actions that spend provider/search money
_PAID_ACTIONS = {"discovery", "research", "repair", "repair-only"}


@dataclass
class AutoLimits:
    """Run-level ceilings for one auto session. Per-stage tool budgets stay in
    model_router — these bound the WHOLE session on top of them."""
    max_steps: int = 200         # controller iterations (one company each)
    max_seconds: int = 3 * 3600  # wall clock for the session
    max_tool_calls: int = 3000   # DeepSeek search+fetch calls, incl. failed-pass waste
    max_tokens: int = 0          # tokens_in+tokens_out; 0 = no token ceiling
    provider_fail_steps: int = 2  # consecutive all-failed steps → stopped-provider
    # must stay > the pipeline's own 2-identical-signatures repair guard, so
    # the bounded repair gets to blank/flag before the controller gives up
    no_progress_steps: int = 3


@dataclass
class AutoResult:
    state: str
    reason: str
    steps: int
    seconds: int
    xlsx: str = ""


class AutoControl:
    """Cooperative pause/stop for a running auto session (thread-safe).
    The controller checks it at every between-companies checkpoint — a
    request never kills an in-flight pass, it ends the session cleanly
    after the current company. Resume = a new auto_run on the same folder."""

    def __init__(self):
        self._lock = threading.Lock()
        self._request = ""            # "" | "pause" | "stop"

    def request_pause(self) -> None:
        with self._lock:
            if not self._request:     # stop wins over a later pause
                self._request = "pause"

    def request_stop(self) -> None:
        with self._lock:
            self._request = "stop"

    @property
    def requested(self) -> str:
        with self._lock:
            return self._request


def create_auto_run(market: str, depth: str) -> Path:
    """Create a run for Auto. The legacy `model` field keeps its Prompt-mode
    meaning (which web chat a pasted prompt targets) and stays at the default,
    so the run remains a normal run in every other workflow; the Auto
    execution provider is recorded separately as `auto_provider`."""
    run_dir = runs.create_run(market, depth, "chatgpt")
    meta = runs._load_meta(run_dir)
    meta["auto_provider"] = "deepseek"
    runs._save_meta(run_dir, meta)
    return run_dir


def exit_code_for(state: str) -> int:
    """CLI exit code: 0 deliverable built, 130 Ctrl-C, 1 everything else."""
    if state.startswith("complete"):
        return 0
    if state == "interrupted":
        return 130
    return 1


# ── observation ───────────────────────────────────────────────────────────────
def snapshot(run_dir: Path) -> dict:
    """Deterministic view of the run's gate state (file reads only, no model
    calls). Two equal snapshots around a step ⇒ the step changed nothing."""
    brands, _note, _segments = runs.manifest(run_dir)
    try:
        built = bool(runs._load_meta(run_dir).get("xlsx"))
    except Exception:
        built = False
    snap = {"brands": len(brands), "pending": 0, "pending_brands": [],
            "accepted": 0, "rejected": 0, "sigs": {}, "b_codes": {},
            "built": built}
    if brands:
        g = runs.run_gate(run_dir, write_report=False)
        snap["pending_brands"] = runs._pending_brands(run_dir, brands)
        snap["pending"] = len(snap["pending_brands"])
        snap["accepted"] = len(g["accepted"])
        snap["rejected"] = len(g["rejected"])
        for e in g["rejected"]:
            rej = [i for i in e["issues"] if i["severity"] == "reject"]
            snap["sigs"][e["entity"]] = ",".join(
                sorted(f"{i['field']}:{i['code']}" for i in rej))
            snap["b_codes"][e["entity"]] = sorted(
                {i["code"] for i in rej} & gate.B_CODES)
    return snap


def decide(snap: dict, quota_exhausted: bool) -> str:
    """Next controller action from a snapshot. Pure policy, no I/O.
    Returns: discovery | research | repair | repair-only | finalize | stop-quota.
    `repair-only` advances the gate/repair/blank path WITHOUT starting new
    company research — the quota-safe resolution branch."""
    if snap["brands"] == 0:
        return "stop-quota" if quota_exhausted else "discovery"
    if snap["pending"]:
        if quota_exhausted:
            return "repair-only" if snap["rejected"] else "stop-quota"
        return "research"
    if snap["rejected"]:
        return "repair"
    return "finalize"


def stuck_rejects(run_dir: Path, snap: dict) -> list[str]:
    """Rejected entities whose EXISTING pipeline caps are exhausted — another
    step can only re-log «manual review», never fix them. Reuses the
    pipeline's own counters (events.jsonl) and limits; adds no policy."""
    out = []
    for entity, sig in snap.get("sigs", {}).items():
        b_fail = set(snap.get("b_codes", {}).get(entity) or [])
        if b_fail:
            # B-codes route to Collector-B reruns, never to record repair
            if api_runner._b_reruns(run_dir, entity, b_fail) >= api_runner._B_RERUN_LIMIT:
                out.append(f"{entity} [B-rerun cap: {'/'.join(sorted(b_fail))}]")
            continue
        sigs = api_runner._repair_sigs(run_dir, entity)
        if len(sigs) >= api_runner._REPAIR_LIMIT or sigs[-2:] == [sig, sig]:
            out.append(f"{entity} [repair cap: {sig[:60]}]")
    return out


# ── events helpers ────────────────────────────────────────────────────────────
def _read_events(run_dir: Path) -> list[dict]:
    try:
        lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _usage(events: list[dict]) -> dict:
    """Measurable spend carried by a slice of events (successes AND the waste
    of failed passes — both count against the session ceilings)."""
    u = {"tool_calls": 0, "tokens": 0}
    for ev in events:
        u["tool_calls"] += ev.get("tool_calls") or 0
        u["tokens"] += (ev.get("tokens_in") or 0) + (ev.get("tokens_out") or 0)
    return u


def _step_failure(events: list[dict]) -> tuple[bool, set]:
    """(the step produced ONLY failures, categories seen). A step with zero
    passes and zero failures (e.g. pure gate work) is not a failure."""
    ok = any(ev.get("event") in _SUCCESS_EVENTS for ev in events)
    cats = {str(ev.get("category") or "other") for ev in events
            if ev.get("event") in _FAILED_EVENTS}
    return (bool(cats) and not ok), cats


def _last_unresolved(run_dir: Path) -> int:
    """Final quality state as the pipeline recorded it (run_complete event)."""
    for ev in reversed(_read_events(run_dir)):
        if ev.get("event") == "run_complete":
            return int(ev.get("unresolved_fields") or 0)
    return 0


# ── read-only plan (--plan): never touches a provider or the search API ──────
def plan(run_dir: Path, limits: AutoLimits | None = None) -> str:
    """What Auto WOULD do, from files alone: current state, next actions and
    the companies they touch, which phases are still required, configured
    limits, and an upper-bound execution plan. Zero model/search calls."""
    lim = limits or AutoLimits()
    if not (run_dir / "run.json").exists():
        return f"not a run folder: {run_dir}"
    meta = runs._load_meta(run_dir)
    brands, _note, segments = runs.manifest(run_dir)
    snap = snapshot(run_dir)
    quota = web_tools.QUOTA_EXHAUSTED
    action = decide(snap, quota)
    approved = bool(meta.get("scope_approved"))
    stuck = stuck_rejects(run_dir, snap) if snap["rejected"] else []

    out = [f"# Auto plan — {meta['run_id']}  (read-only; no API calls made)",
           f"market: {meta.get('market', '?')} · depth: {meta.get('depth', '?')}"
           f" · provider: deepseek (v1) · 1 company per step",
           "",
           f"state: {snap['brands']} companies discovered · "
           f"{snap['pending']} pending research · {snap['accepted']} accepted · "
           f"{snap['rejected']} rejected by the gate · "
           f"Excel built: {'yes' if snap['built'] else 'no'}"
           + (" · ⚠️ search quota exhausted" if quota else ""),
           f"scope approval: {'granted' if approved else 'NOT granted — Auto stops after discovery until approved'}"]

    # phases still required
    phases = []
    if snap["brands"] == 0:
        phases.append("discovery (paid)")
        phases.append("research (cohort size unknown until discovery; paid)")
    elif snap["pending"]:
        phases.append(f"research ({snap['pending']} companies; paid)")
    if snap["rejected"]:
        phases.append(f"model repair ({snap['rejected']} records; paid"
                      + (f"; {len(stuck)} already at their caps → needs-review" if stuck else "") + ")")
    elif snap["brands"] and not snap["pending"]:
        pass
    if not phases:
        phases.append("only finalization (deterministic gate + Excel — "
                      "zero-spend, use --finalize-only)")
    out.append("required: " + "; then ".join(phases))

    # next action + affected companies
    out.append("")
    if action == "discovery":
        out.append(f"next action: discovery — 1 paid pass "
                   f"(tool budget ≤ {mr.stage_budget('discovery')})")
    elif action == "research":
        queue = snap["pending_brands"]
        out.append(f"next action: research «{queue[0]}» (1 of {len(queue)}; "
                   f"queue after it: {', '.join(queue[1:6]) or '—'}"
                   + ("…" if len(queue) > 7 else "") + ")")
    elif action in ("repair", "repair-only"):
        out.append("next action: repair — " + "; ".join(
            f"{ent} [{sig[:60]}]" for ent, sig in list(snap["sigs"].items())[:6]))
    elif action == "finalize":
        out.append("next action: finalize — deterministic gate re-check + "
                   "Excel build (no model calls); safe as --finalize-only")
    elif action == "stop-quota":
        out.append("next action: none — would stop as stopped-quota "
                   "(no new company research under a dead search quota)")
    if brands:
        out.append(f"cohort: {', '.join(brands)}")
        if segments:
            out.append(f"segments: {', '.join(segments)}")

    # limits + upper bound
    ca, cb = mr.stage_budget("collector_a"), mr.stage_budget("collector_b")
    rp, dv = mr.stage_budget("repair"), mr.stage_budget("discovery")
    ext = mr._budget_extend()
    out += ["",
            f"limits: {lim.max_steps} steps · {lim.max_seconds}s wall · "
            f"{lim.max_tool_calls} tool calls · "
            f"{lim.max_tokens or '∞'} tokens · "
            f"stop after {lim.provider_fail_steps} provider-failure steps / "
            f"{lim.no_progress_steps} no-progress steps"]
    if snap["brands"]:
        p, r = snap["pending"], snap["rejected"]
        rep_worst = (p + r) * api_runner._REPAIR_LIMIT
        steps_u = p + rep_worst + 1
        calls_u = p * (ca + cb + 2 * ext) + rep_worst * (rp + ext)
        out.append(
            f"upper bound: ≤ {steps_u} steps ({p} research + ≤{rep_worst} "
            f"repair + 1 finalize) · ≤ {calls_u} tool calls — both further "
            f"capped by the limits above")
    else:
        out.append(
            f"upper bound: 1 discovery step (≤ {dv} tool calls) now; per "
            f"company after approval: ≤ {ca + cb + 2 * ext} research + "
            f"≤ {api_runner._REPAIR_LIMIT} repairs × {rp + ext} tool calls")
    return "\n".join(out)


def _notify(cb, **kw) -> None:
    """Optional status callback (UI) — never allowed to break the session."""
    if cb is None:
        return
    try:
        cb(kw)
    except Exception:
        pass


# ── the controller ────────────────────────────────────────────────────────────
def auto_run(run_dir: Path, provider: str = "deepseek",
             limits: AutoLimits | None = None, log=print,
             unattended: bool = False, approve_scope: bool = False,
             finalize_only: bool = False, confirm=None,
             control: AutoControl | None = None, on_status=None) -> AutoResult:
    """Drive the quantitative run to a terminal state, one company per
    decision. `confirm` is an optional callable(message)->bool used for
    interactive approvals; without it, paid work needs `unattended` (--yes)
    or `approve_scope`. `finalize_only` restricts the session to
    deterministic gate checks + Excel (guaranteed zero LLM/search calls).
    `control` (AutoControl) allows a cooperative pause/stop between
    companies; `on_status` (callable(dict)) receives progress + spend after
    every decision and step — both are optional and UI-oriented."""
    lim = limits or AutoLimits()
    t0 = time.time()
    steps = 0
    used = {"tool_calls": 0, "tokens": 0}

    def terminal(state: str, reason, xlsx: str = "") -> AutoResult:
        secs = int(time.time() - t0)
        try:
            runs._event(run_dir, "auto_terminal", state=state,
                        reason=str(reason)[:300], steps=steps, seconds=secs,
                        **{k: v for k, v in used.items() if v})
        except Exception:
            pass                       # a missing run folder must still report
        log(f"[auto] ■ {state} — {reason}")
        return AutoResult(state, str(reason), steps, secs, xlsx)

    if not (run_dir / "run.json").exists():
        return terminal("blocked-input", f"not a run folder: {run_dir}")

    # ── strict finalize-only: deterministic healing + gate + Excel, nothing
    # else. No provider mode is set, no keys are needed, and the executor is
    # entered ONLY when the gate is already clean — its all-accepted branch
    # makes no model calls (gate determinism guarantees the branch).
    if finalize_only:
        runs.salvage_records(run_dir)     # deterministic, zero-API healing
        runs.autofix_records(run_dir)
        snap = snapshot(run_dir)
        runs._event(run_dir, "auto_decision", step=1, action="finalize-only",
                    pending=snap["pending"], accepted=snap["accepted"],
                    rejected=snap["rejected"])
        if snap["brands"] == 0:
            return terminal("blocked-input",
                            "finalize-only: discovery has not run — nothing to build")
        if snap["pending"]:
            pb = snap.get("pending_brands") or []
            return terminal("blocked-input",
                            f"finalize-only: {snap['pending']} company(ies) still "
                            f"need paid research ({', '.join(pb[:5])}"
                            + ("…" if snap["pending"] > 5 else "") + ")")
        if snap["rejected"]:
            return terminal("blocked-input",
                            f"finalize-only: {snap['rejected']} record(s) need "
                            f"model-based repair "
                            f"({', '.join(list(snap['sigs'])[:5])})")
        steps = 1
        try:
            log(f"[auto] {api_runner.run_next_step(run_dir, 1, log=log)}")
            xlsx = runs.build_excel(run_dir)
        except SystemExit as e:
            return terminal("blocked-input", e)
        unresolved = _last_unresolved(run_dir)
        state = "complete" if unresolved == 0 else "complete-with-gaps"
        return terminal(state, f"{snap['accepted']} records accepted, "
                               f"{unresolved} unresolved field(s), Excel built",
                        xlsx=str(xlsx))

    # v1 pre-flight — clear blocked states instead of downstream crashes
    if (provider or "deepseek").strip().lower() != "deepseek":
        return terminal("blocked-input",
                        f"Auto v1 drives DeepSeek only (asked for «{provider}»)")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return terminal("blocked-input",
                        "DEEPSEEK_API_KEY is missing — set it in Settings")
    if not os.environ.get("SEARCH_API_KEY"):
        return terminal("blocked-input",
                        "SEARCH_API_KEY is missing — DeepSeek research runs on "
                        "the app-side web_search tool (Brave key in Settings)")

    # record the Auto execution provider on the run WITHOUT touching the
    # legacy `model` field (that one means «Prompt-mode paste target»)
    try:
        meta0 = runs._load_meta(run_dir)
        if meta0.get("auto_provider") != "deepseek":
            meta0["auto_provider"] = "deepseek"
            runs._save_meta(run_dir, meta0)
    except Exception:
        pass

    mr.set_mode("deepseek")
    prev_conc = os.environ.get("DS_COMPANY_CONCURRENCY")
    os.environ["DS_COMPANY_CONCURRENCY"] = "1"   # v1: strictly sequential
    runs._event(run_dir, "auto_start", provider="deepseek", batch=1,
                max_steps=lim.max_steps, max_seconds=lim.max_seconds,
                max_tool_calls=lim.max_tool_calls, max_tokens=lim.max_tokens,
                **({"unattended": 1} if unattended else {}))
    no_progress = 0
    provider_fails = 0
    paid_ok = bool(unattended or approve_scope)   # explicit non-interactive OK
    paid_mode = "unattended" if unattended else "flag" if approve_scope else ""
    paid_logged = False
    current = {"action": "", "company": "", "before": None, "in_step": False}
    try:
        while True:
            # user pause/stop — the between-companies checkpoint
            req = control.requested if control is not None else ""
            if req:
                return terminal(
                    "paused" if req == "pause" else "stopped-user",
                    f"{req} requested — session ended at a clean company "
                    f"boundary; start Auto again on this run to resume")

            # run-level ceilings, checked before spending anything more
            if steps >= lim.max_steps:
                return terminal("stopped-budget",
                                f"step limit reached ({steps}/{lim.max_steps})")
            if time.time() - t0 >= lim.max_seconds:
                return terminal("stopped-budget",
                                f"wall-time limit reached ({lim.max_seconds}s)")
            if lim.max_tool_calls and used["tool_calls"] >= lim.max_tool_calls:
                return terminal("stopped-budget", f"tool-call limit reached "
                                f"({used['tool_calls']}/{lim.max_tool_calls})")
            if lim.max_tokens and used["tokens"] >= lim.max_tokens:
                return terminal("stopped-budget", f"token limit reached "
                                f"({used['tokens']}/{lim.max_tokens})")

            before = snapshot(run_dir)
            action = decide(before, web_tools.QUOTA_EXHAUSTED)
            if action == "research":
                pb = before.get("pending_brands") or []
                company = pb[0] if pb else ""
            elif action in ("repair", "repair-only"):
                company = next(iter(before.get("sigs") or {}), "")
            else:
                company = ""
            current.update(action=action, company=company, before=before,
                           in_step=False)
            runs._event(run_dir, "auto_decision", step=steps + 1, action=action,
                        pending=before["pending"], accepted=before["accepted"],
                        rejected=before["rejected"],
                        **({"company": company} if company else {}),
                        **({"quota": 1} if web_tools.QUOTA_EXHAUSTED else {}))
            log(f"[auto] step {steps + 1}: {action}"
                + (f" · {company}" if company else "")
                + f" (pending {before['pending']}, accepted {before['accepted']}, "
                  f"rejected {before['rejected']})")
            _notify(on_status, phase="deciding", step=steps + 1, action=action,
                    company=company, pending=before["pending"],
                    accepted=before["accepted"], rejected=before["rejected"],
                    tool_calls=used["tool_calls"], tokens=used["tokens"],
                    max_steps=lim.max_steps, max_tool_calls=lim.max_tool_calls,
                    max_tokens=lim.max_tokens)

            if action == "stop-quota":
                if before["brands"] == 0:
                    reason = ("search quota exhausted before discovery — "
                              "no cohort to research")
                else:
                    reason = (f"search quota exhausted — {before['pending']} of "
                              f"{before['brands']} companies left unresearched; "
                              f"researched records settled ({before['accepted']} "
                              f"accepted); no new company research started")
                return terminal("stopped-quota", reason)

            # ── scope-approval boundary: research never starts on an
            # unapproved cohort (persisted in run.json, so re-runs continue)
            if action == "research":
                meta = runs._load_meta(run_dir)
                if not meta.get("scope_approved"):
                    brands, _n, segments = runs.manifest(run_dir)
                    log(f"[auto] discovered scope — {len(brands)} companies: "
                        f"{', '.join(brands)}")
                    if segments:
                        log(f"[auto] segments: {', '.join(segments)}")
                    mode = ("unattended" if unattended else
                            "flag" if approve_scope else "")
                    if not mode and confirm is not None:
                        # the review happens IN the question: cohort + segments
                        listing = "\n".join(f"  • {b}" for b in brands[:20])
                        if len(brands) > 20:
                            listing += f"\n  … and {len(brands) - 20} more"
                        if confirm(
                                f"Discovery found {len(brands)} companies:\n"
                                f"{listing}\n"
                                + (f"Segments: {', '.join(segments)}\n"
                                   if segments else "")
                                + "\nApprove this scope for paid per-company "
                                  "research?"):
                            mode = "interactive"
                    if not mode:
                        return terminal(
                            "awaiting-scope-approval",
                            f"{len(brands)} companies discovered — review the "
                            f"list above (companies.json), then re-run with "
                            f"--approve-scope, --yes, or confirm interactively")
                    meta["scope_approved"] = {"at": runs._now(), "mode": mode}
                    runs._save_meta(run_dir, meta)
                    runs._event(run_dir, "auto_approval", kind="scope",
                                mode=mode, companies=len(brands))

            # ── paid-work confirmation: nothing is spent without approval
            if action in _PAID_ACTIONS and not paid_ok:
                if confirm is not None and confirm(
                        f"Start paid DeepSeek work ({action}"
                        + (f" · {company}" if company else "") + ")?"):
                    paid_ok, paid_mode = True, "interactive"
                else:
                    return terminal(
                        "blocked-input",
                        f"paid API work ({action}) needs approval — re-run "
                        f"with --yes/--unattended or confirm interactively")
            if action in _PAID_ACTIONS and not paid_logged:
                runs._event(run_dir, "auto_approval", kind="paid-start",
                            mode=paid_mode)
                paid_logged = True

            if action == "finalize":
                steps += 1
                current["in_step"] = True
                try:
                    # one cheap executor pass: its all-accepted branch logs
                    # run_complete (final quality state) with no model call
                    summary = api_runner.run_next_step(run_dir, 1, log=log)
                    log(f"[auto] {summary}")
                    xlsx = runs.build_excel(run_dir)
                except SystemExit as e:
                    return terminal("blocked-input", e)
                unresolved = _last_unresolved(run_dir)
                state = "complete" if unresolved == 0 else "complete-with-gaps"
                return terminal(
                    state, f"{before['accepted']} records accepted, "
                           f"{unresolved} unresolved field(s), Excel built",
                    xlsx=str(xlsx))

            # discovery / research / repair / repair-only → ONE executor step
            # (batch=1: at most one company per controller decision, so every
            # limit and stop signal is re-checked between companies)
            ev0 = len(_read_events(run_dir))
            steps += 1
            exc_cat, last_note = "", ""
            current["in_step"] = True
            try:
                summary = api_runner.run_next_step(
                    run_dir, 1, log=log,
                    no_new_research=(action == "repair-only"))
                last_note = summary
                log(f"[auto] {summary}")
            except SystemExit as e:
                return terminal("blocked-input", e)
            except Exception as e:      # an escaped pass error (e.g. discovery)
                exc_cat = api_runner._error_category(e)
                last_note = f"{type(e).__name__}: {str(e)[:160]}"
                log(f"[auto] step {steps} raised [{exc_cat}] {last_note}")
            current["in_step"] = False

            new_events = _read_events(run_dir)[ev0:]
            du = _usage(new_events)
            used["tool_calls"] += du["tool_calls"]
            used["tokens"] += du["tokens"]
            _notify(on_status, phase="step-done", step=steps, action=action,
                    company=company, tool_calls=used["tool_calls"],
                    tokens=used["tokens"], max_steps=lim.max_steps,
                    max_tool_calls=lim.max_tool_calls,
                    max_tokens=lim.max_tokens, note=last_note[:160])

            failed_step, cats = _step_failure(new_events)
            if exc_cat:
                failed_step, cats = True, cats | {exc_cat}
            if failed_step and cats <= _PROVIDER_CATS:
                provider_fails += 1
                if provider_fails >= lim.provider_fail_steps:
                    return terminal(
                        "stopped-provider",
                        f"{provider_fails} consecutive steps produced only "
                        f"{'/'.join(sorted(cats))} failures ({last_note})")
            else:
                provider_fails = 0

            after = snapshot(run_dir)
            if after != before:
                no_progress = 0
                continue
            no_progress += 1
            if after["rejected"]:
                stuck = stuck_rejects(run_dir, after)
                if len(stuck) == after["rejected"]:
                    return terminal(
                        "needs-review",
                        f"all {after['rejected']} remaining reject(s) exhausted "
                        f"their repair/B-rerun caps: {'; '.join(stuck)}")
            if no_progress >= lim.no_progress_steps:
                return terminal(
                    "stopped-no-progress",
                    f"{no_progress} consecutive steps changed nothing "
                    f"(last: {last_note or 'no passes recorded'})")
    except KeyboardInterrupt:
        # completed artifacts are already on disk (files save incrementally);
        # log what we know and exit cleanly — the in-flight pass's exact token
        # usage is unavailable, so its spend is explicitly marked incomplete
        partial = {}
        try:
            partial = api_runner._fail_stats()   # partial SourceLog, if any
        except Exception:
            pass
        b = current.get("before") or {}
        try:
            runs._event(run_dir, "auto_interrupted",
                        action=current.get("action", ""),
                        company=current.get("company", ""), step=steps,
                        pending=b.get("pending"), accepted=b.get("accepted"),
                        rejected=b.get("rejected"),
                        session_tool_calls=used["tool_calls"],
                        session_tokens=used["tokens"],
                        **({"spend_incomplete": 1} if current.get("in_step") else {}),
                        **{f"inflight_{k}": v for k, v in partial.items()
                           if isinstance(v, (int, float)) and v})
        except Exception:
            pass
        note = ("in-flight pass spend recorded partially (inflight_*), exact "
                "token usage unavailable" if current.get("in_step") and partial
                else "interrupted while a pass was running — its spend is NOT "
                     "in the session counters" if current.get("in_step")
                else "no pass was running")
        log(f"[auto] ⏹ interrupted at step {steps} "
            f"({current.get('action') or 'idle'}"
            + (f" · {current.get('company')}" if current.get("company") else "")
            + f") — completed artifacts preserved; {note}")
        return AutoResult("interrupted",
                          f"Ctrl-C during {current.get('action') or 'startup'}"
                          + (f" ({current.get('company')})"
                             if current.get("company") else ""),
                          steps, int(time.time() - t0))
    finally:
        if prev_conc is None:
            os.environ.pop("DS_COMPANY_CONCURRENCY", None)
        else:
            os.environ["DS_COMPANY_CONCURRENCY"] = prev_conc


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Goal-based Auto Research v1 — drive a quantitative run to "
                    "a terminal state on DeepSeek, one company per step. Paid "
                    "work needs --yes/--unattended, --approve-scope, or an "
                    "interactive confirmation.")
    ap.add_argument("run_id", nargs="?", help="existing run id (logs/<run_id>)")
    ap.add_argument("--market", help="create a fresh run for this market first")
    ap.add_argument("--depth", default="medium",
                    help="depth for --market (default: medium)")
    ap.add_argument("--plan", action="store_true",
                    help="read-only: show state, next actions, limits and an "
                         "upper-bound plan — no API calls")
    ap.add_argument("--finalize-only", action="store_true",
                    help="only deterministic gate checks + Excel; refuses if "
                         "research or model repair is needed (zero API calls)")
    ap.add_argument("--yes", "--unattended", dest="unattended",
                    action="store_true",
                    help="approve paid work AND the discovered scope up front "
                         "(the original fully autonomous behavior)")
    ap.add_argument("--approve-scope", action="store_true",
                    help="approve the already-discovered cohort and continue")
    ap.add_argument("--max-steps", type=int, default=AutoLimits.max_steps)
    ap.add_argument("--max-minutes", type=int,
                    default=AutoLimits.max_seconds // 60)
    ap.add_argument("--max-tool-calls", type=int,
                    default=AutoLimits.max_tool_calls)
    ap.add_argument("--max-tokens", type=int, default=AutoLimits.max_tokens)
    args = ap.parse_args()
    if args.market:
        run_dir = create_auto_run(args.market, args.depth)
        print(f"created run: {run_dir.name}")
    elif args.run_id:
        run_dir = runs.run_dir_for(args.run_id)
    else:
        ap.error("give a run_id or --market «…»")
        return
    limits = AutoLimits(max_steps=args.max_steps,
                        max_seconds=args.max_minutes * 60,
                        max_tool_calls=args.max_tool_calls,
                        max_tokens=args.max_tokens)
    if args.plan:
        print(plan(run_dir, limits))
        return
    confirm = None
    if sys.stdin.isatty() and not args.unattended:
        def confirm(msg: str) -> bool:
            return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")
    res = auto_run(run_dir, limits=limits, unattended=args.unattended,
                   approve_scope=args.approve_scope,
                   finalize_only=args.finalize_only, confirm=confirm)
    print(f"{res.state}: {res.reason} ({res.steps} step(s), {res.seconds}s)")
    if res.xlsx:
        print(f"deliverable: {res.xlsx}")
    raise SystemExit(exit_code_for(res.state))


if __name__ == "__main__":
    main()
