"""Autonomy-readiness telemetry: failure categories + spend, repair
before/after, run_complete. Run with: python -m unittest tests.test_autonomy_telemetry"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, runs
from src import model_router as mr

BRAND = "Тест"


class TestErrorCategories(unittest.TestCase):
    def test_taxonomy(self):
        cases = [(TimeoutError("read timed out"), "timeout"),
                 (RuntimeError("pass exceeded 25 min after 3 tool call(s)"), "timeout"),
                 (ConnectionError("connection reset by peer"), "stream"),
                 (RuntimeError("Brave search quota exhausted (HTTP 402)"), "quota"),
                 (RuntimeError("deepseek kept requesting tools after the budget"), "budget"),
                 (ValueError("No JSON object in model output"), "parse"),
                 (RuntimeError("Your account is not active, check billing"), "provider"),
                 (KeyError("weird"), "other")]
        for ex, want in cases:
            self.assertEqual(api_runner._error_category(ex), want, repr(ex))


class _FakeLog:
    tool_calls = 7
    stats = {"tokens_in": 5000, "tokens_out": 300, "searches": 2, "fetches": 1,
             "search_denied": 0, "budget_rounds": 0, "requests": 3,
             "early_stop": 0, "extended": 0, "dup_queries": 0, "cache_hits": 0}


class TestFailureSpendRecorded(unittest.TestCase):
    def test_failed_research_event_carries_category_and_spend(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()

            def boom(*a, **k):
                mr.LAST_SOURCE_LOG = _FakeLog()   # partial log published by loop
                raise TimeoutError("stream stalled: read timed out")

            with patch.object(api_runner.runs, "_load_meta",
                              return_value={"market": "m", "output_language": "Russian"}), \
                 patch.object(api_runner.runs, "manifest", return_value=([BRAND], "", [])), \
                 patch.object(api_runner.runs, "load_schema", return_value={}), \
                 patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
                 patch.object(api_runner.runs, "_pending_brands", return_value=[BRAND]), \
                 patch.object(api_runner, "_collector_prompt", return_value="P"), \
                 patch.object(mr, "collect", side_effect=boom), \
                 patch.object(mr, "MODE", "deepseek"):
                api_runner.run_next_step(rd, batch=1, log=lambda *_: None)
            ev = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()
                  if json.loads(l)["event"] == "api_company_failed"][0]
            self.assertEqual(ev["category"], "timeout")
            self.assertEqual(ev["tool_calls"], 7)          # wasted spend visible
            self.assertEqual(ev["tokens_in"], 5000)
            self.assertIn("seconds", ev)
            self.assertNotIn("http", ev["error"])


class TestRepairBeforeAfter(unittest.TestCase):
    def test_sig_after_and_changed_fields(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            rec_path = rd / "agent_runs" / "тест_record.json"
            record = {"entity": BRAND,
                      "fields": {"headcount": {"value": "120", "source": ""},
                                 "website": {"value": "https://t.ru",
                                             "source": "https://t.ru"}}}
            rec_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            issues = [{"field": "headcount", "code": "unsourced",
                       "severity": "reject", "reason": "r"}]
            entry = {"entity": BRAND, "stem": "тест", "path": rec_path,
                     "issues": issues, "record": record, "verdict": "rejected"}
            fixed_rec = {"fields": {
                "headcount": {"value": "150", "source": "https://s.ru/p"},
                "website": {"value": "https://t.ru", "source": "https://t.ru"}}}

            def ok(*a, **k):
                return json.dumps(fixed_rec, ensure_ascii=False), "eng"

            with patch.object(api_runner.runs, "_load_meta",
                              return_value={"market": "m", "output_language": "Russian"}), \
                 patch.object(api_runner.runs, "manifest", return_value=([BRAND], "", [])), \
                 patch.object(api_runner.runs, "load_schema", return_value={}), \
                 patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
                 patch.object(api_runner.runs, "_pending_brands", return_value=[]), \
                 patch.object(api_runner.runs, "salvage_records"), \
                 patch.object(api_runner.runs, "autofix_records", return_value={}), \
                 patch.object(api_runner.runs, "run_gate",
                              return_value={"rejected": [entry], "accepted": []}), \
                 patch.object(mr, "collect", side_effect=ok), \
                 patch.object(mr, "MODE", "gpt"):
                api_runner.run_next_step(rd, batch=1, log=lambda *_: None)
            ev = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()
                  if json.loads(l)["event"] == "api_repair"][0]
            self.assertEqual(ev["sig"], "headcount:unsourced")
            # the targeted failure is gone from sig_after (other codes may
            # remain for this synthetic record — that's honest reporting)
            self.assertNotIn("headcount:unsourced", ev["sig_after"])
            self.assertEqual(ev["changed"], 1)               # only headcount edited
            self.assertEqual(ev["changed_fields"], "headcount")


class TestRunComplete(unittest.TestCase):
    def _step(self, rd, entry):
        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "output_language": "Russian"}), \
             patch.object(api_runner.runs, "manifest", return_value=([BRAND], "", [])), \
             patch.object(api_runner.runs, "load_schema", return_value={}), \
             patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
             patch.object(api_runner.runs, "_pending_brands", return_value=[]), \
             patch.object(api_runner.runs, "salvage_records"), \
             patch.object(api_runner.runs, "autofix_records", return_value={}), \
             patch.object(api_runner.runs, "run_gate",
                          return_value={"rejected": [], "accepted": [entry]}):
            return api_runner.run_next_step(rd, batch=1, log=lambda *_: None)

    def test_emitted_once_with_unresolved_names(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            record = {"entity": BRAND,
                      "fields": {"headcount": {"value": "", "source": ""}},
                      "review_flags": ["unresolved: headcount — нет квоты"]}
            entry = {"entity": BRAND, "stem": "тест", "record": record,
                     "path": rd / "agent_runs" / "тест_record.json", "issues": []}
            self._step(rd, entry)
            self._step(rd, entry)                       # second press: no dup
            evs = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()
                   if json.loads(l)["event"] == "run_complete"]
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["unresolved_fields"], 1)
            self.assertIn("headcount", evs[0]["unresolved"])


class TestSummaryRendering(unittest.TestCase):
    def test_categories_waste_and_outcomes(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            events = [
                {"event": "api_company_failed", "brand": "А", "category": "timeout",
                 "tool_calls": 7, "tokens_in": 5000, "tokens_out": 300, "seconds": 200},
                {"event": "api_company_failed", "brand": "Б", "error": "old-style"},
                {"event": "api_company", "brand": "А", "seconds": 100},
                {"event": "api_company", "brand": "Б", "seconds": 100},
                {"event": "api_repair", "brand": "А", "sig": "a:x,b:y",
                 "sig_after": "", "changed": 2},
                {"event": "api_repair", "brand": "Б", "sig": "a:x,b:y",
                 "sig_after": "a:x", "changed": 1},
                {"event": "api_repair", "brand": "В", "sig": "a:x",
                 "sig_after": "a:x", "changed": 0},
            ]
            with (rd / "events.jsonl").open("w", encoding="utf-8") as fh:
                for e in events:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            out = runs.telemetry_summary(rd)
            self.assertIn("**Failure waste** (spend of failed passes): "
                          "7 tool calls · 5000+300 tokens · 200s", out)
            self.assertIn("**Failures by category**: timeout 1 · uncategorized 1", out)
            self.assertIn("outcomes: cleared 1 / improved 1 / no change 1", out)


if __name__ == "__main__":
    unittest.main()
