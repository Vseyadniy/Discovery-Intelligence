"""Auto v1 safety hardening: read-only --plan, strict --finalize-only,
scope-approval boundary, paid-work confirmation, one-company steps, and
KeyboardInterrupt handling. The core guarantee proven here: plan and
finalize-only can NEVER invoke DeepSeek, Brave, or any other provider —
every provider/search entry point is armed to blow up if touched.
Run with: python -m unittest tests.test_auto_safety"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, auto, runs, web_tools
from src import model_router as mr
from src.auto import AutoLimits

ENV = {"DEEPSEEK_API_KEY": "k", "SEARCH_API_KEY": "s"}


def _boom(*a, **k):
    raise AssertionError("provider/search call attempted in a no-spend mode")


def _guards():
    """Arm every paid entry point: any provider or search call fails the test."""
    return [patch.object(mr, "collect", side_effect=_boom),
            patch.object(mr, "verify", side_effect=_boom),
            patch.object(web_tools, "web_search", side_effect=_boom),
            patch.object(web_tools, "fetch_url", side_effect=_boom),
            patch.object(web_tools, "_search_brave", side_effect=_boom)]


class _Guarded(unittest.TestCase):
    def setUp(self):
        self._gs = _guards()
        for g in self._gs:
            g.start()
        self.addCleanup(lambda: [g.stop() for g in self._gs])


def _mkrun(td, cohort=None):
    rd = Path(td)
    (rd / "agent_runs").mkdir(exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "r", "market": "m", "depth": "superficial",
         "output_language": "Russian", "status": "discovery"}),
        encoding="utf-8")
    if cohort:
        (rd / "companies.json").write_text(json.dumps(
            {"companies": [{"brand": b} for b in cohort],
             "segments": ["S1", "S2"]}, ensure_ascii=False), encoding="utf-8")
    return rd


def _snap(brands=0, pending=0, pending_brands=(), accepted=0, rejected=0,
          sigs=None, b_codes=None, built=False):
    return {"brands": brands, "pending": pending,
            "pending_brands": list(pending_brands), "accepted": accepted,
            "rejected": rejected, "sigs": sigs or {}, "b_codes": b_codes or {},
            "built": built}


def _events(rd, name=None):
    try:
        out = [json.loads(l) for l in
               (rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        return []
    return [e for e in out if name is None or e["event"] == name]


class TestPlanReadOnly(_Guarded):
    def test_plan_research_phase_lists_companies_no_calls(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha", "Beta"])
            with patch.object(api_runner, "run_next_step", side_effect=_boom):
                text = auto.plan(rd)
        self.assertIn("read-only", text)
        self.assertIn("Alpha", text)
        self.assertIn("research «Alpha»", text)
        self.assertIn("NOT granted", text)          # scope boundary shown
        self.assertIn("upper bound", text)
        self.assertIn("limits:", text)
        self.assertIn("2 pending research", text)

    def test_plan_discovery_phase(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)                          # no companies.json yet
            with patch.object(api_runner, "run_next_step", side_effect=_boom):
                text = auto.plan(rd)
        self.assertIn("discovery", text)
        self.assertIn("unknown until discovery", text)

    def test_plan_finalize_phase_points_to_finalize_only(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha"])
            clean = _snap(brands=1, accepted=1)
            with patch.object(auto, "snapshot", return_value=clean), \
                 patch.object(api_runner, "run_next_step", side_effect=_boom):
                text = auto.plan(rd)
        self.assertIn("only finalization", text)
        self.assertIn("--finalize-only", text)

    def test_plan_no_events_written(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha"])
            auto.plan(rd)
            self.assertEqual(_events(rd), [])        # observation leaves no trace


class TestFinalizeOnlyGuarantee(_Guarded):
    def test_refuses_when_discovery_needed(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            res = auto.auto_run(rd, finalize_only=True, log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("discovery", res.reason)

    def test_refuses_when_research_needed(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha", "Beta"])
            res = auto.auto_run(rd, finalize_only=True, log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("research", res.reason)
        self.assertIn("Alpha", res.reason)

    def test_refuses_when_repair_needed(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha"])
            rejected = _snap(brands=1, rejected=1,
                             sigs={"Alpha": "news:unsourced"},
                             b_codes={"Alpha": []})
            with patch.object(auto, "snapshot", return_value=rejected):
                res = auto.auto_run(rd, finalize_only=True, log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("repair", res.reason)

    def test_clean_gate_builds_through_real_executor_zero_calls(self):
        # runs the REAL run_next_step (all-accepted branch) with providers
        # armed — completing proves the branch makes no model/search calls
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha"])
            xlsx = rd / "r_research.xlsx"
            accepted = [{"entity": "Alpha", "record": {"fields": {}}}]
            with patch.object(api_runner.runs, "run_gate",
                              return_value={"accepted": accepted,
                                            "rejected": []}), \
                 patch.object(api_runner.runs, "_pending_brands",
                              return_value=[]), \
                 patch.object(auto.runs, "build_excel", return_value=xlsx):
                res = auto.auto_run(rd, finalize_only=True, log=lambda *a: None)
            self.assertEqual(res.state, "complete")
            self.assertTrue(res.xlsx.endswith(".xlsx"))
            rc = _events(rd, "run_complete")
            self.assertEqual(len(rc), 1)
            self.assertEqual(rc[0]["unresolved_fields"], 0)

    def test_needs_no_api_keys(self):
        # finalize-only must work on a keyless machine (deterministic only)
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            env = {"DEEPSEEK_API_KEY": "", "SEARCH_API_KEY": ""}
            with patch.dict(os.environ, env):
                res = auto.auto_run(rd, finalize_only=True, log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")   # refused on content,
        self.assertNotIn("KEY", res.reason)            # never on missing keys


class TestScopeApproval(unittest.TestCase):
    def _research_run(self, td):
        return _mkrun(td, cohort=["Alpha", "Beta"])

    def test_stops_awaiting_approval_non_interactive(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)
            with patch.object(api_runner, "run_next_step", side_effect=_boom), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None)
            self.assertEqual(res.state, "awaiting-scope-approval")
            self.assertIn("--approve-scope", res.reason)
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            self.assertNotIn("scope_approved", meta)   # nothing auto-approved
            self.assertEqual(auto.exit_code_for(res.state), 1)

    def test_interactive_decline_stops(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)
            with patch.object(api_runner, "run_next_step", side_effect=_boom), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    confirm=lambda msg: False)
        self.assertEqual(res.state, "awaiting-scope-approval")

    def test_interactive_approval_continues_and_persists(self):
        asked = []
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)

            def confirm(msg):
                asked.append(msg)
                return True

            with patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None, confirm=confirm)
            self.assertEqual(res.state, "blocked-input")     # our halt sentinel
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["scope_approved"]["mode"], "interactive")
            kinds = {(e["kind"], e["mode"]) for e in _events(rd, "auto_approval")}
            self.assertIn(("scope", "interactive"), kinds)
            self.assertIn(("paid-start", "interactive"), kinds)
        self.assertEqual(len(asked), 2)      # scope + paid-start confirmations
        self.assertIn("Alpha", "".join(asked) + "Alpha")  # scope msg has count

    def test_approve_scope_flag(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)
            with patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None, approve_scope=True)
            self.assertEqual(res.state, "blocked-input")
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["scope_approved"]["mode"], "flag")

    def test_unattended_keeps_original_autonomous_behavior(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)
            with patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")) as step, \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True)
            self.assertEqual(res.state, "blocked-input")
            step.assert_called_once()
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["scope_approved"]["mode"], "unattended")

    def test_persisted_approval_not_asked_again(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = self._research_run(td)
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            meta["scope_approved"] = {"at": "2026-07-22", "mode": "interactive"}
            (rd / "run.json").write_text(json.dumps(meta), encoding="utf-8")
            asked = []

            def confirm(msg):
                asked.append(msg)
                return True

            with patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                auto.auto_run(rd, log=lambda *a: None, confirm=confirm)
        self.assertEqual(len(asked), 1)      # only the paid-start confirmation
        self.assertIn("paid", asked[0])


class TestPaidConfirmation(unittest.TestCase):
    def test_discovery_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)                          # no cohort → discovery next
            with patch.object(api_runner, "run_next_step",
                              side_effect=_boom) as step, \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None)
            self.assertEqual(res.state, "blocked-input")
            self.assertIn("--yes", res.reason)
            step.assert_not_called()                 # zero spend
            self.assertEqual(_events(rd, "auto_approval"), [])

    def test_interactive_yes_starts_discovery(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")) as step, \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    confirm=lambda m: True)
            self.assertEqual(res.state, "blocked-input")   # halt sentinel
            step.assert_called_once()
            ev = _events(rd, "auto_approval")
            self.assertEqual((ev[0]["kind"], ev[0]["mode"]),
                             ("paid-start", "interactive"))


class TestOneCompanyPerDecision(unittest.TestCase):
    def test_executor_always_called_with_batch_1(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            snaps = [_snap(brands=2, pending=2, pending_brands=["A", "B"]),
                     _snap(brands=2, pending=1, pending_brands=["B"],
                           accepted=1),
                     _snap(brands=2, pending=1, pending_brands=["B"],
                           accepted=1),
                     _snap(brands=2, accepted=2),
                     _snap(brands=2, accepted=2)]
            with patch.object(auto, "snapshot", side_effect=snaps), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              return_value="researched 1/1") as step, \
                 patch.object(auto.runs, "build_excel",
                              return_value=rd / "x.xlsx"):
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True)
            self.assertTrue(res.state.startswith("complete"))
            for call in step.call_args_list:
                self.assertEqual(call.args[1], 1)    # one company per decision
            # a decision (with fresh limits/snapshot checks) preceded each call
            decisions = _events(rd, "auto_decision")
            self.assertEqual(len(decisions), len(step.call_args_list))
            self.assertEqual(decisions[0].get("company"), "A")


class TestInterrupt(unittest.TestCase):
    def test_ctrl_c_logged_and_clean(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            frozen = _snap(brands=2, pending=2, pending_brands=["B1", "B2"])
            with patch.object(auto, "snapshot", return_value=frozen), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=KeyboardInterrupt):
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True)
            self.assertEqual(res.state, "interrupted")
            self.assertEqual(res.steps, 1)
            self.assertEqual(auto.exit_code_for(res.state), 130)
            ev = _events(rd, "auto_interrupted")
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["action"], "research")
            self.assertEqual(ev[0]["company"], "B1")
            self.assertEqual(ev[0]["pending"], 2)
            # exact in-flight token usage unavailable → explicitly marked
            self.assertEqual(ev[0].get("spend_incomplete"), 1)
            self.assertIn("session_tool_calls", ev[0])
            self.assertEqual(_events(rd, "auto_terminal"), [])

    def test_ctrl_c_during_confirmation_not_marked_incomplete(self):
        def confirm(msg):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td, cohort=["Alpha"])
            with patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", side_effect=_boom):
                res = auto.auto_run(rd, log=lambda *a: None, confirm=confirm)
            self.assertEqual(res.state, "interrupted")
            ev = _events(rd, "auto_interrupted")[0]
            self.assertNotIn("spend_incomplete", ev)   # no pass was running


class TestExitCodes(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(auto.exit_code_for("complete"), 0)
        self.assertEqual(auto.exit_code_for("complete-with-gaps"), 0)
        self.assertEqual(auto.exit_code_for("interrupted"), 130)
        for s in ("needs-review", "awaiting-scope-approval", "stopped-quota",
                  "stopped-provider", "stopped-budget", "stopped-no-progress",
                  "blocked-input"):
            self.assertEqual(auto.exit_code_for(s), 1)


class TestSnapshotBrands(unittest.TestCase):
    def test_pending_brands_listed(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td, cohort=["Alpha", "Beta"])
            snap = auto.snapshot(rd)
            self.assertEqual(snap["pending_brands"], ["Alpha", "Beta"])
            self.assertEqual(snap["pending"], 2)


if __name__ == "__main__":
    unittest.main()
