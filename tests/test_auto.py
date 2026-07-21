"""Auto controller v1 (quantitative track): decision policy, no-progress
detection, run-level limits, quota/provider stops, needs-review, terminal
Excel states. Offline — the executor and snapshots are stubbed.
Run with: python -m unittest tests.test_auto"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, auto, runs
from src import model_router as mr
from src.auto import AutoLimits

ENV = {"DEEPSEEK_API_KEY": "k", "SEARCH_API_KEY": "s"}


def _snap(brands=0, pending=0, accepted=0, rejected=0, sigs=None,
          b_codes=None, built=False):
    return {"brands": brands, "pending": pending, "accepted": accepted,
            "rejected": rejected, "sigs": sigs or {}, "b_codes": b_codes or {},
            "built": built}


def _mkrun(td):
    rd = Path(td)
    (rd / "agent_runs").mkdir(exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "r", "market": "m", "depth": "medium",
         "status": "discovery"}), encoding="utf-8")
    return rd


def _events(rd, name=None):
    out = [json.loads(l) for l in
           (rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [e for e in out if name is None or e["event"] == name]


class TestDecidePolicy(unittest.TestCase):
    def test_all_branches(self):
        # no cohort yet
        self.assertEqual(auto.decide(_snap(), False), "discovery")
        self.assertEqual(auto.decide(_snap(), True), "stop-quota")
        # research while companies are pending
        self.assertEqual(auto.decide(_snap(brands=3, pending=2), False),
                         "research")
        # quota: repair-only resolution path while rejects remain…
        self.assertEqual(
            auto.decide(_snap(brands=3, pending=2, rejected=1), True),
            "repair-only")
        # …and stop BEFORE starting new company research once they settle
        self.assertEqual(auto.decide(_snap(brands=3, pending=2), True),
                         "stop-quota")
        # normal repair, then finalize
        self.assertEqual(auto.decide(_snap(brands=3, rejected=2), False),
                         "repair")
        self.assertEqual(auto.decide(_snap(brands=3, accepted=3), False),
                         "finalize")
        # quota never blocks the pure repair/finalize phases (no new research)
        self.assertEqual(auto.decide(_snap(brands=3, rejected=2), True),
                         "repair")
        self.assertEqual(auto.decide(_snap(brands=3, accepted=3), True),
                         "finalize")


class TestPreflightBlocked(unittest.TestCase):
    def test_non_deepseek_provider(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            res = auto.auto_run(_mkrun(td), provider="gpt", log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("DeepSeek only", res.reason)

    def test_missing_keys(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            env = dict(ENV, DEEPSEEK_API_KEY="")
            with patch.dict(os.environ, env):
                res = auto.auto_run(rd, log=lambda *a: None)
            self.assertEqual(res.state, "blocked-input")
            self.assertIn("DEEPSEEK_API_KEY", res.reason)
            env = dict(ENV, SEARCH_API_KEY="")
            with patch.dict(os.environ, env):
                res = auto.auto_run(rd, log=lambda *a: None)
            self.assertEqual(res.state, "blocked-input")
            self.assertIn("SEARCH_API_KEY", res.reason)

    def test_not_a_run_folder(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            res = auto.auto_run(Path(td) / "nope", log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("not a run folder", res.reason)

    def test_systemexit_from_pipeline_becomes_blocked(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot", return_value=_snap()), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("discovery response lacked "
                                                     "companies/segments")):
                res = auto.auto_run(rd, log=lambda *a: None)
        self.assertEqual(res.state, "blocked-input")
        self.assertIn("companies/segments", res.reason)
        self.assertEqual(res.steps, 1)


class TestCompletion(unittest.TestCase):
    def _run_to_build(self, rd, snaps, unresolved):
        xlsx = rd / "r_research.xlsx"

        def fake_step(run_dir, batch=3, log=print, no_new_research=False):
            # the real all-accepted branch logs run_complete before Excel
            runs._event(run_dir, "run_complete", accepted=2,
                        unresolved_fields=unresolved)
            return "all 2 records accepted — build the Excel"

        with patch.dict(os.environ, ENV), \
             patch.object(auto, "snapshot", side_effect=snaps), \
             patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
             patch.object(api_runner, "run_next_step", side_effect=fake_step), \
             patch.object(auto.runs, "build_excel", return_value=xlsx):
            return auto.auto_run(rd, log=lambda *a: None)

    def test_complete_no_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            res = self._run_to_build(rd, [_snap(brands=2, accepted=2)], 0)
            self.assertEqual(res.state, "complete")
            self.assertTrue(res.xlsx.endswith(".xlsx"))
            term = _events(rd, "auto_terminal")[-1]
            self.assertEqual(term["state"], "complete")
            self.assertEqual([e["action"] for e in _events(rd, "auto_decision")],
                             ["finalize"])

    def test_complete_with_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            res = self._run_to_build(rd, [_snap(brands=2, accepted=2)], 3)
            self.assertEqual(res.state, "complete-with-gaps")
            self.assertIn("3 unresolved", res.reason)

    def test_research_progress_then_complete(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            researching = _snap(brands=2, pending=2)
            done = _snap(brands=2, accepted=2)
            res = self._run_to_build(rd, [researching, done, done], 0)
            self.assertEqual(res.state, "complete")
            self.assertEqual(res.steps, 2)   # one research step + finalize
            self.assertEqual([e["action"] for e in _events(rd, "auto_decision")],
                             ["research", "finalize"])


class TestNoProgressAndLimits(unittest.TestCase):
    def test_stopped_no_progress(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            frozen = _snap(brands=2, pending=1, accepted=1)
            with patch.object(auto, "snapshot", return_value=frozen), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              return_value="researched 0/1: —"):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(no_progress_steps=2))
        self.assertEqual(res.state, "stopped-no-progress")
        self.assertEqual(res.steps, 2)

    def test_partial_stuck_is_no_progress_not_review(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            # X exhausted its repair cap; Z was never repaired — not all stuck
            for _ in range(3):
                runs._event(rd, "api_repair", brand="X", sig="inn:invalid-inn")
            frozen = _snap(brands=2, rejected=2,
                           sigs={"X": "inn:invalid-inn", "Z": "news:unsourced"},
                           b_codes={"X": [], "Z": []})
            with patch.object(auto, "snapshot", return_value=frozen), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", return_value="repaired 0"):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(no_progress_steps=2))
        self.assertEqual(res.state, "stopped-no-progress")

    def test_step_limit(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(max_steps=0))
        self.assertEqual(res.state, "stopped-budget")
        self.assertIn("step limit", res.reason)

    def test_wall_time_limit(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(max_seconds=0))
        self.assertEqual(res.state, "stopped-budget")
        self.assertIn("wall-time", res.reason)

    def test_tool_call_and_token_limits(self):
        def burning_step(tool_calls=0, tokens=0):
            def step(run_dir, batch=3, log=print, no_new_research=False):
                runs._event(run_dir, "api_company", brand="A",
                            tool_calls=tool_calls, tokens_in=tokens,
                            tokens_out=0)
                return "researched 1/1: A"
            return step

        moving = [_snap(brands=2, pending=2), _snap(brands=2, pending=1,
                                                    accepted=1)]
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot", side_effect=list(moving)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=burning_step(tool_calls=500)):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(max_tool_calls=100))
            self.assertEqual(res.state, "stopped-budget")
            self.assertIn("tool-call limit", res.reason)
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot", side_effect=list(moving)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=burning_step(tokens=90000)):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(max_tokens=50000))
            self.assertEqual(res.state, "stopped-budget")
            self.assertIn("token limit", res.reason)


class TestQuota(unittest.TestCase):
    def test_stop_before_new_research(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot",
                              return_value=_snap(brands=3, pending=2, accepted=1)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", True), \
                 patch.object(api_runner, "run_next_step") as step:
                res = auto.auto_run(rd, log=lambda *a: None)
        self.assertEqual(res.state, "stopped-quota")
        self.assertIn("no new company research", res.reason)
        step.assert_not_called()          # nothing new was started
        self.assertEqual(res.steps, 0)

    def test_resolution_path_finishes_then_stops(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            rejected = _snap(brands=3, pending=1, accepted=1, rejected=1,
                             sigs={"X": "news:unsourced"}, b_codes={"X": []})
            settled = _snap(brands=3, pending=1, accepted=2)
            with patch.object(auto, "snapshot",
                              side_effect=[rejected, settled, settled]), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", True), \
                 patch.object(api_runner, "run_next_step",
                              return_value="repaired 1: X") as step:
                res = auto.auto_run(rd, log=lambda *a: None)
            self.assertEqual(res.state, "stopped-quota")
            self.assertEqual(res.steps, 1)
            self.assertTrue(step.call_args.kwargs.get("no_new_research"))
            self.assertEqual([e["action"] for e in _events(rd, "auto_decision")],
                             ["repair-only", "stop-quota"])


class TestProviderFailures(unittest.TestCase):
    def test_consecutive_exceptions_stop(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot",
                              return_value=_snap(brands=2, pending=2)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=TimeoutError("read timed out")):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(provider_fail_steps=2))
        self.assertEqual(res.state, "stopped-provider")
        self.assertIn("timeout", res.reason)
        self.assertEqual(res.steps, 2)

    def test_failed_events_without_exception_stop(self):
        def step(run_dir, batch=3, log=print, no_new_research=False):
            runs._event(run_dir, "api_company_failed", brand="A",
                        category="stream", error="connection reset")
            return "researched 0/1 · FAILED: A"

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot",
                              return_value=_snap(brands=1, pending=1)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", side_effect=step):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(provider_fail_steps=2))
        self.assertEqual(res.state, "stopped-provider")
        self.assertIn("stream", res.reason)

    def test_parse_failures_do_not_count_as_provider(self):
        def step(run_dir, batch=3, log=print, no_new_research=False):
            runs._event(run_dir, "api_company_failed", brand="A",
                        category="parse", error="No JSON object")
            return "researched 0/1 · FAILED: A"

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            with patch.object(auto, "snapshot",
                              return_value=_snap(brands=1, pending=1)), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", side_effect=step):
                res = auto.auto_run(rd, log=lambda *a: None,
                                    limits=AutoLimits(provider_fail_steps=2,
                                                      no_progress_steps=3))
        self.assertEqual(res.state, "stopped-no-progress")


class TestNeedsReview(unittest.TestCase):
    def test_all_rejects_capped(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            # X: record-repair cap (3 attempts); Y: Collector-B rerun cap (2)
            for _ in range(3):
                runs._event(rd, "api_repair", brand="X", sig="inn:invalid-inn")
            for _ in range(2):
                runs._event(rd, "api_collector_b_rerun", brand="Y",
                            codes="b-copy")
            frozen = _snap(brands=2, rejected=2,
                           sigs={"X": "inn:invalid-inn",
                                 "Y": "b_coverage:b-copy"},
                           b_codes={"X": [], "Y": ["b-copy"]})
            with patch.object(auto, "snapshot", return_value=frozen), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              return_value="repaired 0 · MANUAL REVIEW: X; Y"):
                res = auto.auto_run(rd, log=lambda *a: None)
        self.assertEqual(res.state, "needs-review")
        self.assertEqual(res.steps, 1)
        self.assertIn("X", res.reason)
        self.assertIn("Y", res.reason)

    def test_stuck_rejects_helper(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            # identical last two signatures also count as capped
            for _ in range(2):
                runs._event(rd, "api_repair", brand="X", sig="news:unsourced")
            snap = _snap(brands=2, rejected=2,
                         sigs={"X": "news:unsourced", "Z": "news:unsourced"},
                         b_codes={"X": [], "Z": []})
            stuck = auto.stuck_rejects(rd, snap)
            self.assertEqual(len(stuck), 1)
            self.assertIn("X", stuck[0])


class TestNoNewResearchFlag(unittest.TestCase):
    def test_skips_research_and_never_logs_partial_run_complete(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            with patch.object(api_runner.runs, "_load_meta",
                              return_value={"run_id": "r", "market": "m",
                                            "output_language": "Russian"}), \
                 patch.object(api_runner.runs, "manifest",
                              return_value=(["A", "B"], "", [])), \
                 patch.object(api_runner.runs, "load_schema",
                              return_value={}), \
                 patch.object(api_runner, "load_config",
                              return_value=({}, {}, [])), \
                 patch.object(api_runner.runs, "_pending_brands",
                              return_value=["B"]), \
                 patch.object(api_runner.runs, "salvage_records",
                              return_value={}), \
                 patch.object(api_runner.runs, "autofix_records",
                              return_value={}), \
                 patch.object(api_runner.runs, "run_gate",
                              return_value={"accepted": [], "rejected": []}), \
                 patch.object(mr, "collect",
                              side_effect=AssertionError("must not browse")):
                s = api_runner.run_next_step(rd, batch=3, log=lambda *a: None,
                                             no_new_research=True)
            self.assertIn("unresearched", s)
            self.assertFalse(_events(rd, "run_complete"))


class TestTelemetryTrail(unittest.TestCase):
    def test_start_decisions_terminal_logged(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            frozen = _snap(brands=1, pending=1)
            with patch.object(auto, "snapshot", return_value=frozen), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              return_value="researched 0/1"):
                auto.auto_run(rd, log=lambda *a: None,
                              limits=AutoLimits(no_progress_steps=1))
            self.assertEqual(len(_events(rd, "auto_start")), 1)
            self.assertEqual(len(_events(rd, "auto_decision")), 1)
            term = _events(rd, "auto_terminal")
            self.assertEqual(len(term), 1)
            self.assertEqual(term[0]["state"], "stopped-no-progress")
            self.assertIn("reason", term[0])


if __name__ == "__main__":
    unittest.main()
