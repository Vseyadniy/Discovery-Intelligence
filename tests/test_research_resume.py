"""Research-step resume — a retry reuses saved collector files instead of
redoing finished passes. Run with: python -m unittest tests.test_research_resume"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner
from src import model_router as mr

BRAND = "Тест"


class TestResearchResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = Path(self.tmp.name)
        (self.rd / "agent_runs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, mode):
        calls = []

        def collect(system, user, max_tokens=16000, on_event=None):
            calls.append(user)
            return '{"fields": {"f": {"value": "v", "source": "https://s.ru/p"}}}', "eng"

        def verify(system, user, escalate, max_tokens=12000, on_event=None):
            return '{"fields": {"merged": {"value": 1}}}', "eng"

        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "output_language": "Russian"}), \
             patch.object(api_runner.runs, "manifest",
                          return_value=([BRAND], "", [])), \
             patch.object(api_runner.runs, "load_schema", return_value={}), \
             patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
             patch.object(api_runner.runs, "_pending_brands", return_value=[BRAND]), \
             patch.object(api_runner, "_collector_prompt",
                          side_effect=lambda letter, *a: f"{letter}-PROMPT"), \
             patch.object(api_runner, "_verifier_prompt", return_value="V-PROMPT"), \
             patch.object(mr, "collect", side_effect=collect), \
             patch.object(mr, "verify", side_effect=verify), \
             patch.object(mr, "MODE", mode), \
             patch.object(mr, "LAST_SOURCE_LOG", None):
            summary = api_runner.run_next_step(self.rd, batch=1, log=lambda *_: None)
        return summary, calls

    def _write_a(self):
        (self.rd / "agent_runs" / "тест_A.json").write_text(json.dumps(
            {"entity": BRAND, "collector": "A",
             "fields": {"x": {"value": 1, "source": "https://a.ru/x"}}},
            ensure_ascii=False), encoding="utf-8")

    def test_deepseek_retry_skips_saved_collector_a(self):
        self._write_a()
        summary, calls = self._run("deepseek")
        self.assertEqual(calls, ["b-PROMPT"])          # only B redone
        self.assertIn("researched 1/1", summary)
        rec = json.loads((self.rd / "agent_runs" / "тест_record.json").read_text())
        self.assertEqual(rec["entity"], BRAND)

    def test_parallel_retry_skips_saved_collector_a(self):
        self._write_a()
        _summary, calls = self._run("gpt")
        self.assertEqual(calls, ["b-PROMPT"])
        b = json.loads((self.rd / "agent_runs" / "тест_B.json").read_text())
        self.assertEqual(b["collector"], "B")          # fresh B was saved

    def test_resume_recorded_in_telemetry_event(self):
        self._write_a()
        self._run("gpt")
        evs = [json.loads(l) for l in
               (self.rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        company = [e for e in evs if e["event"] == "api_company"]
        self.assertEqual(len(company), 1)
        self.assertEqual(company[0]["resumed"], "A")   # stage-level resume trace
        self.assertIn("seconds", company[0])

    def test_fresh_company_runs_both_collectors(self):
        _summary, calls = self._run("gpt")
        self.assertEqual(sorted(calls), ["a-PROMPT", "b-PROMPT"])

    def test_verifier_only_when_both_collectors_saved(self):
        self._write_a()
        (self.rd / "agent_runs" / "тест_B.json").write_text(json.dumps(
            {"entity": BRAND, "collector": "B",
             "fields": {"y": {"value": 2, "source": "https://b.ru/y"}}},
            ensure_ascii=False), encoding="utf-8")
        _summary, calls = self._run("gpt")
        self.assertEqual(calls, [])                    # straight to the verifier
        rec = json.loads((self.rd / "agent_runs" / "тест_record.json").read_text())
        self.assertEqual(rec["entity"], BRAND)


if __name__ == "__main__":
    unittest.main()
