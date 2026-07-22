"""App integration of quantitative Auto mode: AutoControl pause/stop between
companies, on_status progress/spend feed, create_auto_run's legacy-model fix
(auto_provider recorded separately), provider stamping on existing runs, and
a headless UI smoke test (skipped where Tk has no display).
Run with: python -m unittest tests.test_auto_app"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, auto, runs
from src.auto import AutoControl, AutoLimits

ENV = {"DEEPSEEK_API_KEY": "k", "SEARCH_API_KEY": "s"}


def _mkrun(td, cohort=None):
    rd = Path(td)
    (rd / "agent_runs").mkdir(exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "r", "market": "m", "depth": "superficial",
         "model": "chatgpt", "output_language": "Russian",
         "status": "discovery"}), encoding="utf-8")
    if cohort:
        (rd / "companies.json").write_text(json.dumps(
            {"companies": [{"brand": b} for b in cohort],
             "segments": ["S1"]}, ensure_ascii=False), encoding="utf-8")
    return rd


def _snap(brands=0, pending=0, pending_brands=(), accepted=0, rejected=0):
    return {"brands": brands, "pending": pending,
            "pending_brands": list(pending_brands), "accepted": accepted,
            "rejected": rejected, "sigs": {}, "b_codes": {}, "built": False}


def _events(rd, name=None):
    out = [json.loads(l) for l in
           (rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [e for e in out if name is None or e["event"] == name]


class TestAutoControl(unittest.TestCase):
    def test_pause_before_start_is_immediate_and_free(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            ctl = AutoControl()
            ctl.request_pause()
            with patch.object(api_runner, "run_next_step") as step:
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True,
                                    control=ctl)
            self.assertEqual(res.state, "paused")
            self.assertEqual(res.steps, 0)
            step.assert_not_called()

    def test_pause_lands_between_companies(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            ctl = AutoControl()
            snaps = [_snap(brands=3, pending=3, pending_brands=["A", "B", "C"]),
                     _snap(brands=3, pending=2, pending_brands=["B", "C"],
                           accepted=1)]

            def step(run_dir, batch=3, log=print, no_new_research=False):
                ctl.request_pause()          # user clicks ⏸ mid-pass
                return "researched 1/1: A"

            with patch.object(auto, "snapshot", side_effect=snaps), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=step) as mstep:
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True,
                                    control=ctl)
            self.assertEqual(res.state, "paused")
            self.assertEqual(res.steps, 1)      # company A finished, B not begun
            mstep.assert_called_once()
            term = _events(rd, "auto_terminal")[-1]
            self.assertEqual(term["state"], "paused")
            self.assertIn("resume", term["reason"])

    def test_stop_wins_over_pause(self):
        ctl = AutoControl()
        ctl.request_pause()
        ctl.request_stop()
        self.assertEqual(ctl.requested, "stop")
        ctl2 = AutoControl()
        ctl2.request_stop()
        ctl2.request_pause()                    # late pause cannot downgrade
        self.assertEqual(ctl2.requested, "stop")

    def test_stop_state(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            ctl = AutoControl()
            ctl.request_stop()
            with patch.object(api_runner, "run_next_step") as step:
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True,
                                    control=ctl)
            self.assertEqual(res.state, "stopped-user")
            step.assert_not_called()

    def test_resume_after_pause_is_a_fresh_run(self):
        # «resume» = new auto_run on the same folder: the paused session left
        # normal files, so the next session simply continues the state machine
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            ctl = AutoControl()
            ctl.request_pause()
            auto.auto_run(rd, log=lambda *a: None, unattended=True, control=ctl)
            snaps = [_snap(brands=1, accepted=1)]        # clean → finalize

            def fake_step(run_dir, batch=3, log=print, no_new_research=False):
                runs._event(run_dir, "run_complete", accepted=1,
                            unresolved_fields=0)
                return "all 1 records accepted"

            with patch.object(auto, "snapshot", side_effect=snaps), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=fake_step), \
                 patch.object(auto.runs, "build_excel",
                              return_value=rd / "x.xlsx"):
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True,
                                    control=AutoControl())
            self.assertEqual(res.state, "complete")


class TestOnStatusFeed(unittest.TestCase):
    def test_progress_and_spend_reported(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            seen = []
            snaps = [_snap(brands=2, pending=1, pending_brands=["A"],
                           accepted=1),
                     _snap(brands=2, accepted=2),
                     _snap(brands=2, accepted=2)]

            def step(run_dir, batch=3, log=print, no_new_research=False):
                runs._event(run_dir, "api_company", brand="A", tool_calls=40,
                            tokens_in=1000, tokens_out=100)
                if len(seen) > 2:                # second call = finalize pass
                    runs._event(run_dir, "run_complete", accepted=2,
                                unresolved_fields=0)
                return "ok"

            with patch.object(auto, "snapshot", side_effect=snaps), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", side_effect=step), \
                 patch.object(auto.runs, "build_excel",
                              return_value=rd / "x.xlsx"):
                auto.auto_run(rd, log=lambda *a: None, unattended=True,
                              on_status=seen.append)
            phases = [s["phase"] for s in seen]
            self.assertIn("deciding", phases)
            self.assertIn("step-done", phases)
            done = [s for s in seen if s["phase"] == "step-done"][0]
            self.assertEqual(done["tool_calls"], 40)     # spend visible live
            self.assertEqual(done["tokens"], 1100)
            self.assertEqual(done["company"], "A")
            self.assertIn("max_tool_calls", done)        # limits shown alongside

    def test_broken_callback_cannot_break_the_session(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)

            def bad_cb(_st):
                raise RuntimeError("UI died")

            with patch.object(auto, "snapshot",
                              return_value=_snap(brands=1, pending=1,
                                                 pending_brands=["A"])), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step", return_value="0/1"):
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True,
                                    on_status=bad_cb,
                                    limits=AutoLimits(no_progress_steps=1))
            self.assertEqual(res.state, "stopped-no-progress")   # not a crash


class TestCreateAutoRun(unittest.TestCase):
    def test_legacy_model_field_stays_valid(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runs, "LOGS", Path(td)):
            rd = auto.create_auto_run("Тестовый рынок", "superficial")
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            # the legacy field keeps its Prompt-mode meaning and a VALID value…
            self.assertIn(meta["model"], runs.MODELS)
            # …and the Auto execution provider is recorded separately
            self.assertEqual(meta["auto_provider"], "deepseek")
            self.assertEqual(meta["status"], "discovery")
            self.assertTrue((rd / "prompt.md").exists())  # Prompt mode intact

    def test_old_direct_create_with_deepseek_raises(self):
        # the original mismatch: deepseek is NOT a legacy paste target
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runs, "LOGS", Path(td)):
            with self.assertRaises(ValueError):
                runs.create_run("m", "superficial", "deepseek")

    def test_auto_stamps_provider_on_existing_runs(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)                    # legacy run, no auto_provider
            ctl = AutoControl()
            ctl.request_stop()                 # end immediately after stamping
            auto.auto_run(rd, log=lambda *a: None, unattended=True, control=ctl)
            meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(meta.get("auto_provider"), "deepseek")
            self.assertEqual(meta.get("model"), "chatgpt")   # untouched


class TestAppWiring(unittest.TestCase):
    def test_headless_instantiation_has_auto_controls(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception:
            self.skipTest("no display for Tk")
        try:
            root.withdraw()
            import app as app_mod
            a = app_mod.App(root)
            for w in ("auto_plan_btn", "auto_start_btn", "auto_pause_btn",
                      "auto_stop_btn", "auto_lbl"):
                self.assertTrue(hasattr(a, w), w)
            # pause/stop disabled until a session runs; start/plan available
            self.assertEqual(str(a.auto_pause_btn["state"]), "disabled")
            self.assertEqual(str(a.auto_stop_btn["state"]), "disabled")
            self.assertFalse(a._auto_running)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
