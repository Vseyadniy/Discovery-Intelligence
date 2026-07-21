"""Goal-based Auto Research v1 — the QUANTITATIVE track only.

A thin controller loop over the EXISTING file-derived state machine:
`api_runner.run_next_step` stays the only executor (discovery → per-company
research → gate → bounded repair → blank/flag) and `runs.build_excel` the only
builder. The controller adds NO pipeline logic — it decides, before each step,
whether pressing ⚡ again is still worth it, and stops with a deterministic
terminal state when it is not. Every decision and the terminal reason go to
events.jsonl (`auto_start` / `auto_decision` / `auto_terminal`).

Terminal states (AutoResult.state):
  complete            all records accepted, 0 unresolved fields, Excel built
  complete-with-gaps  all accepted, unresolved (blanked+flagged) fields
                      remain, Excel built
  needs-review        every remaining reject has exhausted the pipeline's own
                      repair / Collector-B caps — more API spend cannot help
  stopped-quota       Brave search quota exhausted; already-researched records
                      finished their repair/blank resolution path, but NO new
                      company research was started
  stopped-provider    consecutive steps produced nothing but transient or
                      provider failures (timeout/stream/provider/quota)
  stopped-budget      a run-level limit was hit (steps, wall time, tool
                      calls, tokens)
  stopped-no-progress consecutive steps changed nothing observable, past the
                      pipeline's own bounded-repair guards
  blocked-input       a setup problem the controller cannot fix (missing keys,
                      non-DeepSeek provider, SystemExit from the pipeline)

v1 invariants: DeepSeek only (the one provider with grounding + measurable
spend), strictly sequential (company concurrency forced to 1 for the session),
quantitative research + Excel only, no Pause/Resume/Stop. Progress is detected
by comparing state snapshots around each step, never by parsing summary text.
"""
from __future__ import annotations

import argparse
import json
import os
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


@dataclass
class AutoLimits:
    """Run-level ceilings for one auto session. Per-stage tool budgets stay in
    model_router — these bound the WHOLE session on top of them."""
    max_steps: int = 60          # controller iterations (one ⚡ press each)
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


# ── observation ───────────────────────────────────────────────────────────────
def snapshot(run_dir: Path) -> dict:
    """Deterministic view of the run's gate state (file reads only, no model
    calls). Two equal snapshots around a step ⇒ the step changed nothing."""
    brands, _note, _segments = runs.manifest(run_dir)
    try:
        built = bool(runs._load_meta(run_dir).get("xlsx"))
    except Exception:
        built = False
    snap = {"brands": len(brands), "pending": 0, "accepted": 0, "rejected": 0,
            "sigs": {}, "b_codes": {}, "built": built}
    if brands:
        g = runs.run_gate(run_dir, write_report=False)
        snap["pending"] = len(runs._pending_brands(run_dir, brands))
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


# ── the controller ────────────────────────────────────────────────────────────
def auto_run(run_dir: Path, provider: str = "deepseek", batch: int = 3,
             limits: AutoLimits | None = None, log=print) -> AutoResult:
    """Drive the quantitative run to a terminal state by repeatedly calling
    the existing step executor. Strictly sequential; every decision logged."""
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

    # v1 pre-flight — clear blocked states instead of downstream crashes
    if (provider or "deepseek").strip().lower() != "deepseek":
        return terminal("blocked-input",
                        f"Auto v1 drives DeepSeek only (asked for «{provider}»)")
    if not (run_dir / "run.json").exists():
        return terminal("blocked-input", f"not a run folder: {run_dir}")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return terminal("blocked-input",
                        "DEEPSEEK_API_KEY is missing — set it in Settings")
    if not os.environ.get("SEARCH_API_KEY"):
        return terminal("blocked-input",
                        "SEARCH_API_KEY is missing — DeepSeek research runs on "
                        "the app-side web_search tool (Brave key in Settings)")

    mr.set_mode("deepseek")
    prev_conc = os.environ.get("DS_COMPANY_CONCURRENCY")
    os.environ["DS_COMPANY_CONCURRENCY"] = "1"   # v1: strictly sequential
    runs._event(run_dir, "auto_start", provider="deepseek", batch=batch,
                max_steps=lim.max_steps, max_seconds=lim.max_seconds,
                max_tool_calls=lim.max_tool_calls, max_tokens=lim.max_tokens)
    no_progress = 0
    provider_fails = 0
    try:
        while True:
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
            runs._event(run_dir, "auto_decision", step=steps + 1, action=action,
                        pending=before["pending"], accepted=before["accepted"],
                        rejected=before["rejected"],
                        **({"quota": 1} if web_tools.QUOTA_EXHAUSTED else {}))
            log(f"[auto] step {steps + 1}: {action} (pending {before['pending']}, "
                f"accepted {before['accepted']}, rejected {before['rejected']})")

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

            if action == "finalize":
                steps += 1
                try:
                    # one cheap executor pass: its all-accepted branch logs
                    # run_complete (final quality state) with no model call
                    summary = api_runner.run_next_step(run_dir, batch, log=log)
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

            # discovery / research / repair / repair-only → one executor step
            ev0 = len(_read_events(run_dir))
            steps += 1
            exc_cat, last_note = "", ""
            try:
                summary = api_runner.run_next_step(
                    run_dir, batch, log=log,
                    no_new_research=(action == "repair-only"))
                last_note = summary
                log(f"[auto] {summary}")
            except SystemExit as e:
                return terminal("blocked-input", e)
            except KeyboardInterrupt:
                raise
            except Exception as e:      # an escaped pass error (e.g. discovery)
                exc_cat = api_runner._error_category(e)
                last_note = f"{type(e).__name__}: {str(e)[:160]}"
                log(f"[auto] step {steps} raised [{exc_cat}] {last_note}")

            new_events = _read_events(run_dir)[ev0:]
            du = _usage(new_events)
            used["tool_calls"] += du["tool_calls"]
            used["tokens"] += du["tokens"]

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
    finally:
        if prev_conc is None:
            os.environ.pop("DS_COMPANY_CONCURRENCY", None)
        else:
            os.environ["DS_COMPANY_CONCURRENCY"] = prev_conc


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Goal-based Auto Research v1 — drive a quantitative run to "
                    "a terminal state on DeepSeek (strictly sequential).")
    ap.add_argument("run_id", nargs="?", help="existing run id (logs/<run_id>)")
    ap.add_argument("--market", help="create a fresh run for this market first")
    ap.add_argument("--depth", default="medium",
                    help="depth for --market (default: medium)")
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=AutoLimits.max_steps)
    ap.add_argument("--max-minutes", type=int,
                    default=AutoLimits.max_seconds // 60)
    ap.add_argument("--max-tool-calls", type=int,
                    default=AutoLimits.max_tool_calls)
    ap.add_argument("--max-tokens", type=int, default=AutoLimits.max_tokens)
    args = ap.parse_args()
    if args.market:
        run_dir = runs.create_run(args.market, args.depth, "deepseek")
        print(f"created run: {run_dir.name}")
    elif args.run_id:
        run_dir = runs.run_dir_for(args.run_id)
    else:
        ap.error("give a run_id or --market «…»")
        return
    res = auto_run(run_dir, batch=args.batch,
                   limits=AutoLimits(max_steps=args.max_steps,
                                     max_seconds=args.max_minutes * 60,
                                     max_tool_calls=args.max_tool_calls,
                                     max_tokens=args.max_tokens))
    print(f"{res.state}: {res.reason} ({res.steps} step(s), {res.seconds}s)")
    if res.xlsx:
        print(f"deliverable: {res.xlsx}")
    raise SystemExit(0 if res.state.startswith("complete") else 1)


if __name__ == "__main__":
    main()
