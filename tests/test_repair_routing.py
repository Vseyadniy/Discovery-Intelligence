"""Repair routing — collector-B gate codes get a fresh B pass + re-merge, not a
record repair; a code surviving 2 reruns hard-stops.
Run with:  python -m unittest tests.test_repair_routing"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, runs
from src import model_router as mr

BRAND = "Контур.Эльба"


def _reject(code, field="collector_B"):
    return {"field": field, "severity": "reject", "code": code, "reason": "x"}


class TestRepairRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = Path(self.tmp.name)
        (self.rd / "agent_runs").mkdir()
        self.rec_path = self.rd / "agent_runs" / "эльба_record.json"
        for stem in ("эльба_A", "эльба_B"):
            (self.rd / "agent_runs" / f"{stem}.json").write_text(
                '{"fields": {}}', encoding="utf-8")
        self.rec_path.write_text('{"fields": {}}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, issues):
        entry = {"entity": BRAND, "stem": "эльба", "path": self.rec_path,
                 "issues": issues, "record": {"fields": {}}, "verdict": "rejected"}
        calls = {"collect": [], "verify": []}

        def collect(system, user, max_tokens=16000, on_event=None):
            calls["collect"].append(user)
            return '{"fields": {"f": {"value": "v", "source": "https://s.ru/p"}}}', "eng"

        def verify(system, user, escalate, max_tokens=12000, on_event=None):
            calls["verify"].append(user)
            return '{"fields": {"merged": {"value": 1}}}', "eng"

        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "output_language": "Russian"}), \
             patch.object(api_runner.runs, "manifest",
                          return_value=([BRAND], "", [])), \
             patch.object(api_runner.runs, "load_schema", return_value={}), \
             patch.object(api_runner, "load_config",
                          return_value=({}, {}, [])), \
             patch.object(api_runner.runs, "_pending_brands", return_value=[]), \
             patch.object(api_runner.runs, "salvage_records"), \
             patch.object(api_runner.runs, "run_gate",
                          return_value={"rejected": [entry], "accepted": []}), \
             patch.object(api_runner, "_collector_prompt", return_value="B-PROMPT"), \
             patch.object(api_runner, "_verifier_prompt", return_value="V-PROMPT"), \
             patch.object(mr, "collect", side_effect=collect), \
             patch.object(mr, "verify", side_effect=verify), \
             patch.object(mr, "MODE", "gpt"):
            summary = api_runner.run_next_step(self.rd, batch=3, log=lambda *_: None)
        return summary, calls

    def _events(self):
        f = self.rd / "events.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] \
            if f.exists() else []

    def test_bcopy_routes_to_fresh_b_pass_and_remerge(self):
        summary, calls = self._run([_reject("b-copy")])
        self.assertEqual(calls["collect"], ["B-PROMPT"])       # fresh B, no record repair
        self.assertEqual(calls["verify"], ["V-PROMPT"])        # verifier re-merge
        b = json.loads((self.rd / "agent_runs" / "эльба_B.json").read_text())
        self.assertEqual(b["collector"], "B")                  # _B.json rewritten
        rec = json.loads(self.rec_path.read_text())
        self.assertEqual(rec["entity"], BRAND)                 # record re-merged
        ev = [e for e in self._events() if e["event"] == "api_collector_b_rerun"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["codes"], "b-copy")
        self.assertIn("B rerun", summary)

    def test_record_codes_keep_normal_repair_path(self):
        summary, calls = self._run([_reject("unsourced", field="revenue")])
        self.assertEqual(len(calls["collect"]), 1)
        self.assertIn("Repair ONE record", calls["collect"][0])
        self.assertEqual(calls["verify"], [])                  # no re-merge
        self.assertEqual([e for e in self._events()
                          if e["event"] == "api_collector_b_rerun"], [])
        self.assertIn("repaired 1", summary)

    def test_hard_stop_after_two_reruns(self):
        for _ in range(2):
            runs._event(self.rd, "api_collector_b_rerun", brand=BRAND, codes="b-copy")
        summary, calls = self._run([_reject("b-copy")])
        self.assertEqual(calls["collect"], [])                 # no API calls spent
        self.assertEqual(calls["verify"], [])
        self.assertIn("MANUAL REVIEW", summary)
        self.assertIn("b-copy", summary)

    def test_different_code_not_blocked_by_other_reruns(self):
        for _ in range(2):
            runs._event(self.rd, "api_collector_b_rerun", brand=BRAND, codes="b-no-new-source")
        _summary, calls = self._run([_reject("b-copy")])
        self.assertEqual(calls["collect"], ["B-PROMPT"])       # b-copy gets its own reruns


class TestRecordRepairCap(unittest.TestCase):
    """3 exhausted repair attempts → no more API calls; evidence fields are
    blanked + flagged `unresolved:` so the record can pass and export."""

    def test_cap_blanks_instead_of_burning_calls(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            rec_path = rd / "agent_runs" / "эльба_record.json"
            record = {"entity": BRAND,
                      "fields": {"headcount": {"value": "120", "source": ""}}}
            rec_path.write_text(json.dumps(record, ensure_ascii=False),
                                encoding="utf-8")
            for stem in ("эльба_A", "эльба_B"):
                (rd / "agent_runs" / f"{stem}.json").write_text(
                    '{"fields": {}}', encoding="utf-8")
            issues = [{"field": "headcount", "code": "unsourced",
                       "severity": "reject"}]
            sig = "headcount:unsourced"
            for _ in range(3):
                runs._event(rd, "api_repair", brand=BRAND, sig=sig)
            entry = {"entity": BRAND, "stem": "эльба", "path": rec_path,
                     "issues": issues, "record": record, "verdict": "rejected"}
            calls = []
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
                 patch.object(mr, "collect", side_effect=lambda *a, **k: calls.append(1)):
                summary = api_runner.run_next_step(rd, batch=3, log=lambda *_: None)
            self.assertEqual(calls, [])                    # zero API spend
            saved = json.loads(rec_path.read_text())
            self.assertEqual(saved["fields"]["headcount"]["value"], "")
            self.assertTrue(saved["review_flags"][0].startswith("unresolved: headcount"))
            self.assertIn("unresolved fields blanked", summary)


class TestPromptModeRouting(unittest.TestCase):
    """runs.next_prompt must issue a «Redo Collector B» prompt for B_CODES
    rejects and the normal repair prompt for record-level rejects."""

    _PROSE = ("Тест — облачная платформа для автоматизации бухгалтерии малого "
              "бизнеса, запущена в 2015 году; тарифы от 2 000 ₽ в месяц, "
              "интеграции с банками и ФНС, свыше 50 000 клиентов.")

    def _make_run(self, b_prose):
        import json
        self.tmp = tempfile.TemporaryDirectory()
        rd = Path(self.tmp.name)
        (rd / "agent_runs").mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({
            "run_id": "t", "market": "m", "depth": "superficial",
            "model": "chatgpt", "output_language": "Russian",
            "status": "research"}, ensure_ascii=False), encoding="utf-8")
        (rd / "companies.json").write_text(json.dumps({
            "market": "m", "segments": ["сегмент"],
            "companies": [{"brand": "Тест", "segment": "сегмент"}]},
            ensure_ascii=False), encoding="utf-8")
        mk = lambda prose, extra_src: {"entity": "Тест", "fields": {
            "description": {"value": prose, "source": "https://a.ru/x"},
            "other": {"value": "y", "source": extra_src}}}
        (rd / "agent_runs" / "тест_A.json").write_text(
            json.dumps(mk(self._PROSE, "https://a.ru/y"), ensure_ascii=False),
            encoding="utf-8")
        (rd / "agent_runs" / "тест_B.json").write_text(
            json.dumps(mk(b_prose, "https://b-only.ru/z"), ensure_ascii=False),
            encoding="utf-8")
        (rd / "agent_runs" / "тест_record.json").write_text(
            json.dumps({"entity": "Тест", "fields": {}}, ensure_ascii=False),
            encoding="utf-8")
        return rd

    def tearDown(self):
        self.tmp.cleanup()

    def test_bcopy_gets_redo_collector_b_prompt(self):
        rd = self._make_run(b_prose=self._PROSE)          # B copied A verbatim
        kind, text = runs.next_prompt(rd)
        self.assertEqual(kind, "repair")
        self.assertIn("Redo Collector B", text)
        self.assertIn("тест_B.json", text)
        self.assertNotIn("Fix ONLY the fields listed", text)

    def test_record_rejects_keep_normal_repair_prompt(self):
        rd = self._make_run(b_prose="Совсем другой независимый текст про Тест "
                                    "от собственного исследования прессы.")
        kind, text = runs.next_prompt(rd)                 # record fails, B is fine
        self.assertEqual(kind, "repair")
        self.assertIn("Repair pass", text)
        self.assertNotIn("Redo Collector B", text)


if __name__ == "__main__":
    unittest.main()
