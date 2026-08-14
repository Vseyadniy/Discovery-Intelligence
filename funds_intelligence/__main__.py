"""Funds Intelligence CLI — offline by default; live work needs two explicit flags.

    python -m funds_intelligence preview                    # zero calls, read-only
    python -m funds_intelligence live --approve-paid --yes  # the controlled run

`preview` makes ZERO provider calls — it only reads the configured providers,
budgets and limits (it does import the config layer to do so) and prints the
resulting exposure. `live` refuses to start unless BOTH `--approve-paid` (money)
and `--yes` (final confirmation) are given, the providers match the pinned pair
(deepseek + tavily), and the Preview reports no blockers. Research still stops
for landscape scope approval before any manager is researched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research_core import RunStore, RunControl

from .controller import FundsController
from .extraction import MalformedPassOutput
from .pack import FundsMandate
from .preview import build_preview, render_preview, european_pe_plan

LOGS = Path(__file__).resolve().parent.parent / "logs"


def _report_malformed(ex: MalformedPassOutput, *, retry_hint: str) -> int:
    """A paid pass produced unusable output. The response is on disk and the
    stage did not advance — say both, and how to resume."""
    print(f"\nSTOPPED: {ex}")
    print(f"  stage        : {ex.stage}{' / ' + ex.target if ex.target else ''}")
    print(f"  raw response : {ex.raw_path}")
    print(f"  reason       : {ex.reason}")
    print("  run state    : unchanged — no artifact was written.")
    print(f"  retry        : {retry_hint}")
    return 2


def _plan_mandate(plan) -> FundsMandate:
    return FundsMandate(geography=["Europe"], window_from=2022, window_to=2026,
                        filters={"sector": "B2B software & tech-enabled services",
                                 "signal": "active buy-and-build 2022–2026",
                                 "manager_type": "private equity"},
                        depth="standard", target_count=plan.landscape_targets)


def cmd_preview(_args) -> int:
    report = build_preview(european_pe_plan())
    print(render_preview(report))
    return 0 if report["ready_to_run"] else 1


def cmd_live(args) -> int:
    plan = european_pe_plan()
    report = build_preview(plan)
    print(render_preview(report))
    if not report["ready_to_run"]:
        print("\nREFUSED: preview reports blockers (above). Nothing was spent.")
        return 1
    if not (args.approve_paid and args.yes):
        print("\nREFUSED: live research spends money. Re-run with "
              "--approve-paid --yes once the exposure above is acceptable. "
              "Nothing was spent.")
        return 1

    # Imported ONLY on the live path, so `preview` never loads the provider stack.
    from .live_pass import LiveResearchPass

    store = RunStore(LOGS)
    c = FundsController(store, limits=plan.limits)
    h = c.create_run(plan.label, _plan_mandate(plan))
    c.approve_paid_work(h, approved_by="cli", note="preview reviewed; --approve-paid --yes")
    print(f"\nrun: {h.run_id}\n  folder: {h.dir}")

    print("\n[1/3] landscape …")
    try:
        cands = c.run_landscape(
            h, LiveResearchPass(stage="funds_landscape", budget=plan.landscape_budget,
                                allow_extend=plan.allow_extend,
                                max_tokens=plan.max_tokens_per_pass))
    except MalformedPassOutput as ex:
        return _report_malformed(
            ex, retry_hint=f"python -m funds_intelligence relandscape {h.run_id} "
                           f"--approve-paid --yes  (spends one more landscape pass)")
    for i, x in enumerate(cands, 1):
        print(f"   {i}. {x.get('name')}  [{x.get('kind')}]  {x.get('source', '')}")
    print(f"\nSTOPPED for scope approval — status={h.load_meta()['status']}")
    print("Review logs/<run>/landscape.json, then approve the subset to research:")
    print(f"   python -m funds_intelligence approve {h.run_id} --targets \"A\" \"B\"")
    return 0


def cmd_relandscape(args) -> int:
    """Retry the landscape pass on an EXISTING run whose landscape failed.

    Resumability without loss: the previous attempt's raw response stays in
    `raw/` under its own sequence number, the run keeps its id, mandate and
    paid-work approval, and only the one failed stage is repeated."""
    plan = european_pe_plan()
    report = build_preview(plan)
    if not report["ready_to_run"]:
        print(render_preview(report))
        print("\nREFUSED: preview reports blockers (above). Nothing was spent.")
        return 1
    store = RunStore(LOGS)
    c = FundsController(store, limits=plan.limits)
    h = store.open(args.run_id)
    if not c.paid_work_approved(h):
        print("REFUSED: run has no paid-work approval."); return 1
    if h.path("landscape.json").exists():
        print(f"REFUSED: {args.run_id} already has a landscape — retrying would "
              f"overwrite it. Inspect logs/{args.run_id}/landscape.json.")
        return 1
    if not (args.approve_paid and args.yes):
        print("REFUSED: a landscape retry spends one paid pass. Re-run with "
              "--approve-paid --yes. Nothing was spent.")
        return 1

    from .live_pass import LiveResearchPass
    print(f"[retry] landscape for {h.run_id} …")
    try:
        cands = c.run_landscape(
            h, LiveResearchPass(stage="funds_landscape", budget=plan.landscape_budget,
                                allow_extend=plan.allow_extend,
                                max_tokens=plan.max_tokens_per_pass))
    except MalformedPassOutput as ex:
        return _report_malformed(
            ex, retry_hint=f"python -m funds_intelligence relandscape {h.run_id} "
                           f"--approve-paid --yes")
    for i, x in enumerate(cands, 1):
        print(f"   {i}. {x.get('name')}  [{x.get('kind')}]  {x.get('source', '')}")
    print(f"\nSTOPPED for scope approval — status={h.load_meta()['status']}")
    return 0


def cmd_approve(args) -> int:
    store = RunStore(LOGS)
    c = FundsController(store)
    h = store.open(args.run_id)
    approved = c.approve_scope(h, args.targets)
    print(f"approved {len(approved)}: {approved}")
    print(f"   python -m funds_intelligence research {args.run_id} --yes")
    return 0


def cmd_research(args) -> int:
    plan = european_pe_plan()
    store = RunStore(LOGS)
    c = FundsController(store, limits=plan.limits)
    h = store.open(args.run_id)
    if not c.paid_work_approved(h):
        print("REFUSED: run has no paid-work approval."); return 1
    if not args.yes:
        print(f"pending: {c.pending_targets(h)} — re-run with --yes"); return 1

    from .live_pass import LiveResearchPass
    try:
        state = c.run_until_terminal(
            h, lambda t: LiveResearchPass(stage="funds_research",
                                          budget=plan.research_budget,
                                          allow_extend=plan.allow_extend,
                                          max_tokens=plan.max_tokens_per_pass),
            control=RunControl(run_dir=h.dir))
    except MalformedPassOutput as ex:
        # The target stayed in the pending queue (no accepted graph, no rejected
        # record), so re-running research resumes at exactly this manager.
        return _report_malformed(
            ex, retry_hint=f"python -m funds_intelligence research {args.run_id} --yes")
    print(f"terminal: {state}")
    for folder in ("targets", "rejected"):
        d = h.path(folder)
        if d.exists():
            print(f"  {folder}: {sorted(p.stem for p in d.glob('*.json'))}")
    return 0


def cmd_deepdive(args) -> int:
    plan = european_pe_plan()
    store = RunStore(LOGS)
    c = FundsController(store, limits=plan.limits)
    h = store.open(args.run_id)
    if not c.paid_work_approved(h):
        print("REFUSED: parent run has no paid-work approval."); return 1
    child = c.create_deep_dive(h, args.target)
    c.approve_paid_work(child, approved_by="cli", note="deep dive of an accepted manager")
    if not args.yes:
        print(f"child run {child.run_id} created — re-run with --yes to expand"); return 1

    from .live_pass import LiveResearchPass
    try:
        out = c.expand_deep_dive(
            child, LiveResearchPass(stage="funds_deep_dive", budget=plan.deep_dive_budget,
                                    allow_extend=plan.allow_extend,
                                    max_tokens=plan.max_tokens_per_pass))
    except MalformedPassOutput as ex:
        # The child run keeps the inherited parent graph untouched.
        return _report_malformed(
            ex, retry_hint=f"python -m funds_intelligence deepdive {args.run_id} "
                           f"--target \"{args.target}\" --yes")
    print(f"child: {child.run_id}  verdict: {out['verdict']}")
    return 0


def cmd_status(args) -> int:
    store = RunStore(LOGS)
    h = store.open(args.run_id)
    print(json.dumps(h.load_meta(), ensure_ascii=False, indent=2))
    for e in h.ledger.read()[-15:]:
        print(f"  {e['ts']}  {e['event']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="funds_intelligence")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preview", help="zero-call exposure report").set_defaults(fn=cmd_preview)

    p = sub.add_parser("live", help="create the run + landscape (PAID)")
    p.add_argument("--approve-paid", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_live)

    p = sub.add_parser("relandscape",
                       help="retry a FAILED landscape on an existing run (PAID)")
    p.add_argument("run_id")
    p.add_argument("--approve-paid", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_relandscape)

    p = sub.add_parser("approve", help="approve the landscape scope")
    p.add_argument("run_id"); p.add_argument("--targets", nargs="+", required=True)
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("research", help="research approved managers (PAID)")
    p.add_argument("run_id"); p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_research)

    p = sub.add_parser("deepdive", help="deep dive on one accepted manager (PAID)")
    p.add_argument("run_id"); p.add_argument("--target", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_deepdive)

    p = sub.add_parser("status", help="run metadata + recent events")
    p.add_argument("run_id"); p.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
