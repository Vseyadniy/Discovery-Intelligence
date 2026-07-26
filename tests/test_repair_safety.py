"""Repair safety regressions from the 2026-07-22 live run (СберКорус):
1) a repair response that returns schema fields at the record's TOP level
   and/or drops unrelated fields must not damage the saved record
   (lift_misplaced_fields + scoped merge_repair, autofix lift for records
   already damaged on disk);
2) a cap-exhausted reject must not block the repair queue, and Auto must
   pick the accurate terminal state (needs-review with a quota note, not
   stopped-no-progress).
Run with: python -m unittest tests.test_repair_safety"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner, auto, gate, runs
from src import model_router as mr
from src.auto import AutoControl

ENV = {"DEEPSEEK_API_KEY": "k", "SEARCH_API_KEY": "s"}
SCHEMA = {"fields": [{"name": "segment"}, {"name": "inn"},
                     {"name": "website"}, {"name": "deployment_type_ratio"}]}


# ── unit: the deterministic helpers ───────────────────────────────────────────
class TestLiftMisplacedFields(unittest.TestCase):
    def test_moves_dict_and_scalar_fields(self):
        rec = {"entity": "X",
               "segment": {"value": "S", "source": "https://x.ru/p"},
               "inn": "7707083893",
               "fields": {"website": {"value": "https://x.ru", "source": "https://x.ru"}}}
        moved = gate.lift_misplaced_fields(rec, ["segment", "inn", "website"])
        self.assertEqual(sorted(moved), ["inn", "segment"])
        self.assertNotIn("segment", rec)
        self.assertEqual(rec["fields"]["segment"]["value"], "S")
        self.assertEqual(rec["fields"]["inn"],
                         {"value": "7707083893", "source": ""})   # scalar wrapped
        self.assertEqual(rec["entity"], "X")                      # meta untouched

    def test_never_overwrites_existing_value_and_skips_unknown(self):
        rec = {"segment": {"value": "TOP"}, "mystery": 1,
               "fields": {"segment": {"value": "KEEP", "source": "https://a/b"}}}
        moved = gate.lift_misplaced_fields(rec, ["segment"])
        self.assertEqual(moved, [])
        self.assertEqual(rec["fields"]["segment"]["value"], "KEEP")
        self.assertIn("mystery", rec)                 # not a schema field

    def test_heals_the_sberkorus_shape(self):
        # the real damaged record: six schema fields stranded at top level,
        # their `fields` slots empty/absent → the gate saw merge-loss forever
        rec = {"entity": "СберКорус",
               "deployment_type_ratio": {"value": "80/20",
                                         "source": "https://sberkorus.ru/x"},
               "fields": {"inn": {"value": "7736663049", "source": "https://e/1"}}}
        moved = gate.lift_misplaced_fields(rec, ["deployment_type_ratio", "inn"])
        self.assertEqual(moved, ["deployment_type_ratio"])
        self.assertEqual(rec["fields"]["deployment_type_ratio"]["value"], "80/20")


class TestRepairScopeAndMerge(unittest.TestCase):
    def test_scope_splits_comma_joined_labels(self):
        issues = [{"field": "brand_name, legal_entity_name, inn", "code": "merge-loss",
                   "severity": "reject", "reason": "r"},
                  {"field": "segment", "code": "segment-taxonomy",
                   "severity": "reject", "reason": "r"}]
        self.assertEqual(gate.repair_scope(issues),
                         {"brand_name", "legal_entity_name", "inn", "segment"})

    def test_merge_keeps_dropped_and_out_of_scope_fields(self):
        old = {"entity": "X", "review_flags": ["unresolved: news — x"],
               "fields": {"inn": {"value": "1", "source": "https://a"},
                          "website": {"value": "https://x.ru", "source": "https://x.ru"},
                          "segment": {"value": "", "source": ""}}}
        new = {"fields": {"segment": {"value": "Sales", "source": "https://b"},
                          "website": {"value": "", "source": ""}},   # tries to blank
               "review_flags": ["fixed segment"]}
        merged = gate.merge_repair(old, new, {"segment"})
        f = merged["fields"]
        self.assertEqual(f["segment"]["value"], "Sales")        # scope: taken
        self.assertEqual(f["inn"]["value"], "1")                # dropped: kept
        self.assertEqual(f["website"]["value"], "https://x.ru")  # blank refused
        self.assertEqual(merged["review_flags"],
                         ["unresolved: news — x", "fixed segment"])  # union

    def test_merge_accepts_gap_fills_outside_scope(self):
        old = {"fields": {"inn": {"value": "", "source": ""}}}
        new = {"fields": {"inn": {"value": "7707083893", "source": "https://e"}}}
        merged = gate.merge_repair(old, new, set())
        self.assertEqual(merged["fields"]["inn"]["value"], "7707083893")


# ── integration: run_next_step repair path ────────────────────────────────────
def _entry(rd, brand, stem, record, issues):
    p = rd / "agent_runs" / f"{stem}_record.json"
    p.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return {"entity": brand, "stem": stem, "path": p, "issues": issues,
            "record": record, "verdict": "rejected"}


class _RepairFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = Path(self.tmp.name)
        (self.rd / "agent_runs").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, entries, response, batch=1, no_new_research=False,
             pending=None):
        calls = []

        def collect(system, user, max_tokens=16000, on_event=None, **kw):
            calls.append(user)
            if isinstance(response, Exception):
                raise response
            return response, "eng"

        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"run_id": "r", "market": "m",
                                        "output_language": "Russian"}), \
             patch.object(api_runner.runs, "manifest",
                          return_value=([e["entity"] for e in entries], "", [])), \
             patch.object(api_runner.runs, "load_schema", return_value=SCHEMA), \
             patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
             patch.object(api_runner.runs, "_pending_brands",
                          return_value=pending or []), \
             patch.object(api_runner.runs, "salvage_records", return_value={}), \
             patch.object(api_runner.runs, "autofix_records", return_value={}), \
             patch.object(api_runner.runs, "run_gate",
                          return_value={"rejected": entries, "accepted": []}), \
             patch.object(mr, "collect", side_effect=collect):
            summary = api_runner.run_next_step(
                self.rd, batch=batch, log=lambda *a: None,
                no_new_research=no_new_research)
        return summary, calls

    REC = {"entity": "X",
           "fields": {"inn": {"value": "7707083893", "source": "https://e/1"},
                      "website": {"value": "https://x.ru", "source": "https://x.ru"},
                      "segment": {"value": "", "source": ""}}}
    ISSUES = [{"field": "segment", "severity": "reject",
               "code": "required-empty", "reason": "r"}]

    def _saved(self, stem="x"):
        return json.loads((self.rd / "agent_runs" / f"{stem}_record.json")
                          .read_text(encoding="utf-8"))


class TestRepairCannotDamageRecord(_RepairFixture):
    def test_misplaced_top_level_fields_are_lifted(self):
        e = _entry(self.rd, "X", "x", json.loads(json.dumps(self.REC)), self.ISSUES)
        # the model answers with `segment` at the TOP level and empty fields —
        # exactly the СберКорус failure shape
        resp = '{"entity": "X", "segment": {"value": "Sales", "source": "https://x.ru/p"}, "fields": {}}'
        summary, calls = self._run([e], resp)
        self.assertEqual(len(calls), 1)
        rec = self._saved()
        self.assertEqual(rec["fields"]["segment"]["value"], "Sales")
        self.assertNotIn("segment", [k for k in rec if k != "fields"])
        self.assertEqual(rec["fields"]["inn"]["value"], "7707083893")
        self.assertEqual(rec["fields"]["website"]["value"], "https://x.ru")

    def test_unrelated_fields_survive_a_dropping_response(self):
        e = _entry(self.rd, "X", "x", json.loads(json.dumps(self.REC)), self.ISSUES)
        # response drops `inn` entirely and tries to blank `website`
        resp = ('{"fields": {"segment": {"value": "Sales", "source": "https://x.ru/p"},'
                ' "website": {"value": "", "source": ""}}}')
        self._run([e], resp)
        rec = self._saved()
        self.assertEqual(rec["fields"]["segment"]["value"], "Sales")
        self.assertEqual(rec["fields"]["inn"]["value"], "7707083893")   # not lost
        self.assertEqual(rec["fields"]["website"]["value"], "https://x.ru")

    def test_malformed_response_preserves_previous_record(self):
        e = _entry(self.rd, "X", "x", json.loads(json.dumps(self.REC)), self.ISSUES)
        before = (self.rd / "agent_runs" / "x_record.json").read_text(encoding="utf-8")
        summary, _ = self._run([e], "это не JSON { сломано")
        after = (self.rd / "agent_runs" / "x_record.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)                  # record untouched
        self.assertIn("FAILED", summary)
        ev = [json.loads(l) for l in
              (self.rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        fails = [x for x in ev if x["event"] == "api_repair_failed"]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["category"], "parse")


class TestCappedRecordDoesNotBlockQueue(_RepairFixture):
    CAP_SIG = "deployment_type_ratio:merge-loss"

    def _seed_cap(self, brand="Стак"):
        for _ in range(api_runner._REPAIR_LIMIT):
            runs._event(self.rd, "api_repair", brand=brand, sig=self.CAP_SIG)

    def _capped_entry(self):
        rec = {"entity": "Стак", "fields": {}}
        issues = [{"field": "deployment_type_ratio", "severity": "reject",
                   "code": "merge-loss", "reason": "r"}]
        return _entry(self.rd, "Стак", "стак", rec, issues)

    def test_repairable_second_record_gets_the_slot(self):
        self._seed_cap()
        capped = self._capped_entry()
        fixable = _entry(self.rd, "X", "x", json.loads(json.dumps(self.REC)),
                         self.ISSUES)
        resp = '{"fields": {"segment": {"value": "Sales", "source": "https://x.ru/p"}}}'
        summary, calls = self._run([capped, fixable], resp, batch=1)
        self.assertEqual(len(calls), 1)                  # one PAID call: X only
        self.assertIn("Repair ONE record for «X»", calls[0])
        self.assertIn("MANUAL REVIEW", summary)          # capped surfaced too
        self.assertIn("Стак", summary)
        ev = [json.loads(l) for l in
              (self.rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        repaired = [x for x in ev if x["event"] == "api_repair"
                    and x.get("brand") == "X"]
        self.assertEqual(len(repaired), 1)
        self.assertEqual(self._saved()["fields"]["segment"]["value"], "Sales")

    def test_quota_repair_only_still_repairs_behind_a_capped_record(self):
        self._seed_cap()
        capped = self._capped_entry()
        fixable = _entry(self.rd, "X", "x", json.loads(json.dumps(self.REC)),
                         self.ISSUES)
        resp = '{"fields": {"segment": {"value": "Sales", "source": "https://x.ru/p"}}}'
        with patch.object(api_runner.web_tools, "QUOTA_EXHAUSTED", True):
            summary, calls = self._run([capped, fixable], resp, batch=1,
                                       no_new_research=True,
                                       pending=["P1", "P2"])
        self.assertEqual(len(calls), 1)                  # no research started
        self.assertIn("Repair ONE record for «X»", calls[0])
        self.assertIn("MANUAL REVIEW", summary)


# ── autofix heals records already damaged on disk ─────────────────────────────
class TestAutofixLiftsMisplacedFields(unittest.TestCase):
    def test_saved_damaged_record_heals_without_model_calls(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            rec = {"entity": "X",
                   "inn": {"value": "7707083893", "source": "https://e/1"},
                   "fields": {"website": {"value": "https://x.ru",
                                          "source": "https://x.ru"}}}
            p = rd / "agent_runs" / "x_record.json"
            p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            entry = {"entity": "X", "stem": "x", "path": p, "record": rec,
                     "issues": [{"field": "inn", "severity": "reject",
                                 "code": "required-empty", "reason": "r"}],
                     "verdict": "rejected"}
            with patch.object(runs, "manifest", return_value=(["X"], "", [])), \
                 patch.object(runs, "run_gate",
                              return_value={"rejected": [entry],
                                            "accepted": []}), \
                 patch.object(runs, "load_schema",
                              return_value={"fields": [{"name": "inn"},
                                                       {"name": "website"}]}):
                fixed = runs.autofix_records(rd)
            self.assertIn("X", fixed)
            self.assertTrue(any("lifted" in n for n in fixed["X"]))
            saved = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(saved["fields"]["inn"]["value"], "7707083893")
            self.assertNotIn("inn", [k for k in saved if k != "fields"])


# ── Auto terminal-state selection ─────────────────────────────────────────────
def _snap(brands=0, pending=0, pending_brands=(), accepted=0, rejected=0,
          sigs=None, b_codes=None):
    return {"brands": brands, "pending": pending,
            "pending_brands": list(pending_brands), "accepted": accepted,
            "rejected": rejected, "sigs": sigs or {}, "b_codes": b_codes or {},
            "built": False}


def _mkrun(td, cohort=None):
    rd = Path(td)
    (rd / "agent_runs").mkdir(exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "r", "market": "m", "depth": "superficial",
         "model": "chatgpt", "output_language": "Russian",
         "status": "discovery"}), encoding="utf-8")
    if cohort:
        (rd / "companies.json").write_text(json.dumps(
            {"companies": [{"brand": b} for b in cohort], "segments": ["S"]},
            ensure_ascii=False), encoding="utf-8")
    return rd


class TestTerminalStateSelection(unittest.TestCase):
    def test_quota_capped_plus_repairable_ends_needs_review_not_no_progress(self):
        # the exact live sequence: quota dead, СберКорус capped, СКБ Контур
        # repairable — the fixed queue repairs Контур first, then the run
        # ends as needs-review (with the quota context), not no-progress
        cap_sig = "deployment_type_ratio:merge-loss"
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td)
            for _ in range(api_runner._REPAIR_LIMIT):
                runs._event(rd, "api_repair", brand="СберКорус", sig=cap_sig)
            both = _snap(brands=6, pending=2, pending_brands=["P1", "P2"],
                         accepted=3, rejected=2,
                         sigs={"СберКорус": cap_sig,
                               "СКБ Контур": "segment:segment-taxonomy"},
                         b_codes={"СберКорус": [], "СКБ Контур": []})
            capped_only = _snap(brands=6, pending=2,
                                pending_brands=["P1", "P2"], accepted=4,
                                rejected=1, sigs={"СберКорус": cap_sig},
                                b_codes={"СберКорус": []})
            with patch.object(auto, "snapshot",
                              side_effect=[both, capped_only,
                                           capped_only, capped_only]), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", True), \
                 patch.object(api_runner, "run_next_step",
                              return_value="repaired…") as step:
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True)
            self.assertEqual(res.state, "needs-review")
            self.assertIn("СберКорус", res.reason)
            self.assertIn("search quota exhausted", res.reason)
            self.assertIn("2 company(ies) remain unresearched", res.reason)
            for call in step.call_args_list:      # quota → never new research
                self.assertTrue(call.kwargs.get("no_new_research"))

    def test_resume_researches_only_the_missing_company(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, ENV):
            rd = _mkrun(td, cohort=["Alfa", "Beta", "Gamma"])
            for brand, stem in (("Alfa", "alfa"), ("Beta", "beta")):
                (rd / "agent_runs" / f"{stem}_record.json").write_text(
                    json.dumps({"entity": brand, "fields": {}}),
                    encoding="utf-8")
            accepted = [{"entity": "Alfa"}, {"entity": "Beta"}]
            with patch.object(auto.runs, "run_gate",
                              return_value={"accepted": accepted,
                                            "rejected": []}), \
                 patch.object(auto.web_tools, "QUOTA_EXHAUSTED", False), \
                 patch.object(api_runner, "run_next_step",
                              side_effect=SystemExit("halt")) as step:
                res = auto.auto_run(rd, log=lambda *a: None, unattended=True)
            self.assertEqual(res.state, "blocked-input")     # halt sentinel
            step.assert_called_once()                        # ONE paid step
            ev = [json.loads(l) for l in
                  (rd / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            dec = [x for x in ev if x["event"] == "auto_decision"]
            self.assertEqual(dec[0]["action"], "research")
            self.assertEqual(dec[0]["company"], "Gamma")     # not Alfa/Beta


if __name__ == "__main__":
    unittest.main()
