"""Rerunning respondent sourcing over the same targets after «done»:
API→Prompt and Prompt→API, preserving + merging accepted results.
Run with: python -m unittest tests.test_respondent_rerun"""
import json
import time
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src import api_runner, onepager, respondents, runs
from src import model_router as mr

TODAY = date.today().isoformat()


def _cand(name="Иван Иванов", org="Банк N", **over):
    c = {"name": name, "role": "CIO", "org": org, "why_relevant": "x",
         "addresses": ["buying_behavior"], "priority": 1,
         "profile_url": "https://conf.ru/p/ivanov",
         "sources": ["https://conf.ru/p/ivanov"], "confidence": "high",
         "role_current": True, "verified_on": TODAY}
    c.update(over)
    return c


def _mkrun(td):
    rd = Path(td)
    (rd / "qual").mkdir(parents=True, exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "2026-07-01_1200_m_superficial", "market": "m",
         "depth": "superficial", "model": "chatgpt",
         "output_language": "Russian", "status": "x"}, ensure_ascii=False),
        encoding="utf-8")
    return rd


def _done_state(rd, market_cands):
    """One accepted market file, no pending/rejected — the «done» state."""
    respondents.resp_path(rd, respondents.MARKET_STEM).write_text(
        json.dumps({"scope": "market", "candidates": market_cands},
                   ensure_ascii=False), encoding="utf-8")
    onepager.setup(rd, "выбрать вендора", {"Directum": "competitor"},
                   manual={"Directum": {"segment": "ECM", "notes": ""}},
                   require_goal=False)
    respondents.resp_path(rd, "directum").write_text(
        json.dumps({"scope": "company", "entity": "Directum",
                    "candidates": [_cand(name="Пётр Петров", org="Directum")]},
                   ensure_ascii=False), encoding="utf-8")


class TestRerunState(unittest.TestCase):
    def test_request_rerun_arms_and_routes_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            self.assertEqual(respondents.gate_respondents(rd)["rerun"], [])  # not armed
            info = respondents.request_rerun(rd, "api")
            self.assertEqual(info["attempt"], 1)
            r = respondents.gate_respondents(rd)
            self.assertEqual({e["label"] for e in r["rerun"]},
                             {"Market level", "Directum"})   # all accepted queued
            ev = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(e["event"] == "respondents_rerun_requested"
                                and e["mode"] == "api" for e in ev))

    def test_rerun_refused_when_work_pending(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [])   # market file empty → rejected, not done
            with self.assertRaises(SystemExit):
                respondents.request_rerun(rd, "api")

    def test_second_request_increments_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            respondents.request_rerun(rd, "api")
            time.sleep(0.01)
            # re-source both files so the first rerun completes and clears
            for stem in (respondents.MARKET_STEM, "directum"):
                p = respondents.resp_path(rd, stem)
                p.write_text(p.read_text(), encoding="utf-8")   # bump mtime
            respondents._clear_rerun_if_done(rd, respondents.gate_respondents(rd))
            self.assertEqual(respondents.request_rerun(rd, "prompt")["attempt"], 2)


class TestPromptRerun(unittest.TestCase):
    def test_prompt_after_done_emits_rerun_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            # at done, the prompt says complete
            kind, _ = respondents.next_respondent_prompt(rd, 2)
            self.assertEqual(kind, "done")
            respondents.request_rerun(rd, "prompt")
            kind, text = respondents.next_respondent_prompt(rd, 2)
            self.assertEqual(kind, "respondents-rerun")
            self.assertIn("NEW respondent pass", text)
            self.assertIn("existing accepted candidates are KEPT", text)


class TestApiRerunMergesAndPreserves(unittest.TestCase):
    def _run(self, rd, collect_fn):
        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "run_id": "t",
                                        "output_language": "Russian"}), \
             patch.object(mr, "collect", side_effect=collect_fn), \
             patch.object(mr, "MODE", "gpt"):
            return api_runner.run_respondent_step(rd, 4, log=lambda *_: None)

    def test_api_rerun_preserves_old_and_adds_new(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand(name="Старый Эксперт", org="X")])
            respondents.request_rerun(rd, "api")

            # the new pass returns a DIFFERENT person per file → merge keeps both
            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                if "market level" in user.lower():
                    scope, ent, new = "market", None, "Новый Эксперт"
                else:
                    scope, ent, new = "company", "Directum", "Новый Директор"
                doc = {"scope": scope, "candidates": [_cand(name=new, org="Y")]}
                if ent:
                    doc["entity"] = ent
                return json.dumps(doc, ensure_ascii=False), "eng"

            s = self._run(rd, collect)
            self.assertIn("rerun #1", s)
            market = json.loads(respondents.resp_path(
                rd, respondents.MARKET_STEM).read_text())
            names = {c["name"] for c in market["candidates"]}
            self.assertIn("Старый Эксперт", names)   # preserved
            self.assertIn("Новый Эксперт", names)    # merged in

    def test_rerun_clears_and_returns_to_done(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            respondents.request_rerun(rd, "api")

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                if "market level" in user.lower():
                    scope, ent, nm = "market", None, "Доп Рыночный"
                else:
                    scope, ent, nm = "company", "Directum", "Доп Компанийный"
                doc = {"scope": scope, "candidates": [_cand(name=nm, org="Z")]}
                if ent:
                    doc["entity"] = ent
                return json.dumps(doc, ensure_ascii=False), "eng"

            self._run(rd, collect)   # re-sources both queued files
            # flag cleared, Excel/report rebuilt, back to done
            final = self._run(rd, collect)
            self.assertIn("complete", final)
            self.assertNotIn("respondents_rerun",
                             onepager.load_meta(rd))
            ev = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(e["event"] == "respondents_rerun_complete" for e in ev))


class TestMergeIdentity(unittest.TestCase):
    def test_merge_preserves_scope_and_entity(self):
        old = {"scope": "company", "entity": "Directum",
               "candidates": [_cand(name="A", org="Directum")]}
        # a bad rerun returns the wrong scope/entity — merge must ignore them
        new = {"scope": "market", "candidates": [_cand(name="B", org="X")]}
        m = respondents.merge_candidates(old, new)
        self.assertEqual(m["scope"], "company")
        self.assertEqual(m["entity"], "Directum")
        self.assertEqual({c["name"] for c in m["candidates"]}, {"A", "B"})

    def test_market_merge_drops_stray_entity(self):
        old = {"scope": "market", "candidates": [_cand(name="A")]}
        new = {"scope": "market", "entity": "Oops", "candidates": [_cand(name="B")]}
        m = respondents.merge_candidates(old, new)
        self.assertNotIn("entity", m)


class TestModeSwitchReruns(unittest.TestCase):
    def test_prompt_then_api_rerun(self):
        # first pass PROMPT (files hand-authored), then a rerun in API mode
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            respondents.request_rerun(rd, "api")
            self.assertEqual((onepager.load_meta(rd)["respondents_rerun"]["mode"]),
                             "api")
            self.assertEqual(respondents.progress(rd)["rerun"], 2)

    def test_api_then_prompt_rerun(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            _done_state(rd, [_cand()])
            respondents.request_rerun(rd, "prompt")
            kind, _ = respondents.next_respondent_prompt(rd, 2)
            self.assertEqual(kind, "respondents-rerun")


if __name__ == "__main__":
    unittest.main()
