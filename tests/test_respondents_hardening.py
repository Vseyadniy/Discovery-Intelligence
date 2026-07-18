"""Audit fixes for the respondent stage: URL grounding, wider privacy net,
cross-file dedup, bounded repair, telemetry.
Run with: python -m unittest tests.test_respondents_hardening"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src import api_runner, respondents, runs
from src import model_router as mr
from src.web_tools import SourceLog

TODAY = date.today().isoformat()


def _cand(**over):
    c = {"name": "Иван Иванов", "role": "Директор", "org": "Банк N",
         "why_relevant": "внедрял BPM", "addresses": ["H1"], "priority": 1,
         "profile_url": "https://conf.ru/speakers/ivanov",
         "contact_route": "через форму на профиле",
         "sources": ["https://conf.ru/speakers/ivanov"],
         "confidence": "high", "role_current": True, "verified_on": TODAY}
    c.update(over)
    return c


class TestUrlGrounding(unittest.TestCase):
    def _log(self, *urls):
        log = SourceLog()
        log.log_search([{"title": "t", "url": u, "snippet": ""} for u in urls])
        return log

    def test_grounded_candidate_untouched(self):
        doc = {"scope": "market", "candidates": [_cand()]}
        log = self._log("https://conf.ru/speakers/ivanov")
        self.assertEqual(respondents.ground_candidates(doc, log), [])
        self.assertEqual(doc["candidates"][0]["profile_url"],
                         "https://conf.ru/speakers/ivanov")

    def test_fabricated_profile_blanked_then_rejected_by_validator(self):
        doc = {"scope": "market", "candidates": [
            _cand(profile_url="https://linkedin.com/in/never-visited",
                  sources=["https://never-visited.ru/a"])]}
        log = self._log("https://other.ru/x")
        details = respondents.ground_candidates(doc, log)
        self.assertEqual(len(details), 2)
        c = doc["candidates"][0]
        self.assertEqual(c["profile_url"], "")        # blanked, never persisted
        self.assertEqual(c["sources"], [])
        codes = {i["code"] for i in respondents.validate_respondents(
            doc, {"H1"}, "market") if i["severity"] == "reject"}
        self.assertIn("required-empty", codes)        # → routed into repair

    def test_domain_only_match_downgrades_confidence(self):
        doc = {"scope": "market", "candidates": [
            _cand(profile_url="https://conf.ru/speakers/other-page")]}
        log = self._log("https://conf.ru/speakers/ivanov")
        details = respondents.ground_candidates(doc, log)
        self.assertEqual(len(details), 1)
        self.assertEqual(doc["candidates"][0]["confidence"], "low")
        self.assertNotEqual(doc["candidates"][0]["profile_url"], "")


class TestPrivacyNet(unittest.TestCase):
    def _rejects(self, text):
        doc = {"scope": "market", "candidates": [_cand(why_relevant=text)]}
        return {i["code"] for i in respondents.validate_respondents(
            doc, {"H1"}, "market") if i["severity"] == "reject"}

    def test_domestic_phone_formats_caught(self):
        for s in ("8 916 123-45-67", "8 (916) 123-45-67", "89161234567",
                  "79161234567", "+7 916 123-45-67"):
            self.assertIn("private-contact", self._rejects(f"звонить {s}"), s)

    def test_obfuscated_emails_caught(self):
        for s in ("ivanov (at) bank.ru", "ivanov [собака] bank.ru",
                  "ivanov{at}bank.ru"):
            self.assertIn("private-contact", self._rejects(s), s)

    def test_registry_numbers_not_false_positives(self):
        # ИНН (10/12), ОГРН (13) must not trip the phone net
        for s in ("ИНН 7715560268", "ИНН 771556026801", "ОГРН 1027715560268123"[:18]):
            self.assertNotIn("private-contact", self._rejects(s), s)


class TestCrossFileDedup(unittest.TestCase):
    OP = {"entity": "Directum", "angle": "competitor",
          "context": {"hypotheses": [{"id": "H1", "text": "t", "basis": "hypothesis",
                                      "validated_if": "v"}]},
          "interview_brief": {"respondents": []},
          "priorities": {}}

    def _run_gate(self, market_cands, company_cands):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rd = Path(tmp.name)
        (rd / "qual").mkdir(parents=True)
        q = {"accepted": [{"entity": "Directum", "stem": "directum", "op": self.OP,
                           "record": {"entity": "Directum", "fields": {}},
                           "issues": [], "verdict": "accepted"}],
             "rejected": [], "pending": [], "records_gate": {"accepted": []}}
        respondents.resp_path(rd, respondents.MARKET_STEM).write_text(json.dumps(
            {"scope": "market", "candidates": market_cands}, ensure_ascii=False),
            encoding="utf-8")
        respondents.resp_path(rd, "directum").write_text(json.dumps(
            {"scope": "company", "entity": "Directum", "candidates": company_cands},
            ensure_ascii=False), encoding="utf-8")
        with patch.object(respondents.onepager, "gate_qual", return_value=q):
            return respondents.gate_respondents(rd)

    def test_same_person_across_files_rejected_in_later_file(self):
        r = self._run_gate([_cand()], [_cand()])   # same person, both files
        verd = {e["label"]: e["verdict"] for e in r["accepted"] + r["rejected"]}
        self.assertEqual(verd["Market level"], "accepted")   # first file keeps
        self.assertEqual(verd["Directum"], "rejected")
        dup = [i for e in r["rejected"] for i in e["issues"]
               if i["code"] == "duplicate"]
        self.assertTrue(dup and "Market level" in dup[0]["reason"])

    def test_different_people_both_accepted(self):
        r = self._run_gate([_cand()], [_cand(name="Пётр Петров",
                                             org="Comindware")])
        self.assertEqual(len(r["accepted"]), 2)


class TestBoundedRepairAndTelemetry(unittest.TestCase):
    def _step(self, rd, r, collect_fn):
        q = r["qual"]
        with patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "output_language": "Russian",
                                        "run_id": "t"}), \
             patch.object(respondents, "gate_respondents", return_value=r), \
             patch.object(respondents.onepager, "load_meta",
                          return_value={"research_goal": "g", "companies": {}}), \
             patch.object(mr, "collect", side_effect=collect_fn), \
             patch.object(mr, "MODE", "gpt"):
            return api_runner.run_respondent_step(rd, batch=2, log=lambda *_: None)

    def _rejected_state(self, rd):
        e = {"label": "Market level", "scope": "market", "stem": respondents.MARKET_STEM,
             "path": respondents.resp_path(rd, respondents.MARKET_STEM),
             "doc": {"scope": "market", "candidates": []},
             "issues": [{"field": "candidates", "severity": "reject",
                         "code": "counts", "reason": "no candidates"}],
             "verdict": "rejected"}
        q = {"accepted": [{"entity": "X", "stem": "x",
                           "op": {"context": {"hypotheses": []},
                                  "interview_brief": {}},
                           "record": {}, "issues": []}],
             "rejected": [], "pending": [], "records_gate": {"accepted": []}}
        return {"accepted": [], "rejected": [e], "pending": [], "qual": q}

    def test_repair_records_sig_and_sig_after(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "qual").mkdir()
            # theme address: the qual state here has no hypotheses, and invented
            # H* ids are now rejected (orphan-ref) even pre-one-pager
            good = {"scope": "market",
                    "candidates": [_cand(addresses=["buying_behavior"])]}

            def ok(*a, **k):
                return json.dumps(good, ensure_ascii=False), "eng"

            summary = self._step(rd, self._rejected_state(rd), ok)
            self.assertIn("repaired 1", summary)
            ev = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()
                  if json.loads(l)["event"] == "api_respondents"][0]
            self.assertEqual(ev["sig"], "candidates:counts")
            self.assertEqual(ev["sig_after"], "")            # cleared
            self.assertEqual(ev["repair"], 1)

    def test_no_progress_hard_stop_spends_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "qual").mkdir()
            for _ in range(3):
                runs._event(rd, "api_respondents", scope="Market level",
                            repair=1, sig="candidates:counts")
            calls = []
            summary = self._step(rd, self._rejected_state(rd),
                                 lambda *a, **k: calls.append(1))
            self.assertEqual(calls, [])                      # zero API spend
            self.assertIn("MANUAL REVIEW", summary)

    def test_summary_includes_respondents_stage_and_outcomes(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            for e in ({"event": "api_respondents", "scope": "Market level",
                       "seconds": 60, "tokens_in": 9000, "tokens_out": 400,
                       "candidates": 4, "searches": 6, "fetches": 4},
                      {"event": "api_respondents", "scope": "Comindware",
                       "repair": 1, "sig": "a:x,b:y", "sig_after": "a:x",
                       "seconds": 30},
                      {"event": "api_respondents_failed", "scope": "Comindware",
                       "category": "timeout", "tokens_in": 2000, "tokens_out": 5}):
                runs._event(rd, e.pop("event"), **e)
            out = runs.telemetry_summary(rd)
            self.assertIn("| respondents | 2 | 1 |", out)     # passes + failure
            self.assertIn("9000+400", out)                    # spend visible
            self.assertIn("**Repair outcomes**: improved 1", out)   # outcome counted
            self.assertIn("timeout 1", out)                   # category counted


class TestApiPendingPath(unittest.TestCase):
    def test_market_then_company_sourcing_saves_files(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "qual").mkdir()
            op_entry = {"entity": "Directum", "stem": "directum",
                        "op": {"angle": "competitor",
                               "context": {"hypotheses": [
                                   {"id": "H1", "text": "t", "validated_if": "v"}]},
                               "interview_brief": {"respondents": []}},
                        "record": {"entity": "Directum", "fields": {}},
                        "issues": []}
            q = {"accepted": [op_entry], "rejected": [], "pending": [],
                 "records_gate": {"accepted": []}}
            r = {"accepted": [], "rejected": [],
                 "pending": ["Market level", "Directum"], "qual": q}
            returned = {"scope": "market", "candidates": [_cand()]}

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                self.assertIn("MARKET level", user)          # market goes first
                self.assertIn("NO email addresses", user)
                return json.dumps(returned, ensure_ascii=False), "eng"

            with patch.object(api_runner.runs, "_load_meta",
                              return_value={"market": "m", "run_id": "t",
                                            "output_language": "Russian"}), \
                 patch.object(respondents, "gate_respondents", return_value=r), \
                 patch.object(respondents.onepager, "load_meta",
                              return_value={"research_goal": "g", "companies": {}}), \
                 patch.object(mr, "collect", side_effect=collect), \
                 patch.object(mr, "MODE", "gpt"):
                summary = api_runner.run_respondent_step(rd, 2, log=lambda *_: None)
            self.assertIn("sourced 1: Market level", summary)
            saved = json.loads(respondents.resp_path(
                rd, respondents.MARKET_STEM).read_text())
            self.assertEqual(len(saved["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()


class TestRepairGroundingScope(unittest.TestCase):
    """Root-cause regression for the 2026-07-13 run: a repair pass that opens
    almost nothing must NOT wipe URLs grounded in the original sourcing pass."""

    def test_unchanged_candidates_survive_repair_grounding(self):
        prev = {"scope": "market", "candidates": [_cand()]}
        new = {"scope": "market", "candidates": [dict(_cand(), priority=2)]}
        log = SourceLog()                      # repair opened NOTHING
        details = respondents.ground_candidates(new, log, prev_doc=prev)
        self.assertEqual(details, [])          # no stripping
        self.assertEqual(new["candidates"][0]["profile_url"],
                         "https://conf.ru/speakers/ivanov")

    def test_new_or_changed_candidates_still_audited(self):
        prev = {"scope": "market", "candidates": [_cand()]}
        new = {"scope": "market", "candidates": [
            _cand(),                                        # unchanged → skipped
            _cand(name="Новый Человек",                     # new → audited
                  profile_url="https://fake.ru/new")]}
        details = respondents.ground_candidates(new, SourceLog(), prev_doc=prev)
        self.assertEqual(len(details), 2)      # new profile blanked + source
        self.assertEqual(new["candidates"][1]["profile_url"], "")
        self.assertEqual(new["candidates"][0]["profile_url"],
                         "https://conf.ru/speakers/ivanov")


class TestAutofixDoc(unittest.TestCase):
    def test_deterministic_healing(self):
        doc = {"scope": "market", "candidates": [
            _cand(priority=5, contact_route="пишите на pr@x.ru или +7 916 123-45-67"),
            _cand(name="Стар Роль", role_current=False),
            _cand(),                                        # dup of candidate 1
        ], "note": "общий контакт media@t1.ru"}
        notes = respondents.autofix_doc(doc)
        c = doc["candidates"][0]
        self.assertEqual(c["priority"], 3)                  # clamped
        self.assertNotIn("pr@x.ru", json.dumps(doc, ensure_ascii=False))
        self.assertNotIn("+7 916", json.dumps(doc, ensure_ascii=False))
        self.assertEqual(len(doc["candidates"]), 1)         # stale + dup dropped
        self.assertNotIn("media@t1.ru", doc["note"])
        self.assertGreaterEqual(len(notes), 4)
        # healed doc now passes the validator outright
        rejects = [i for i in respondents.validate_respondents(
            doc, {"H1"}, "market") if i["severity"] == "reject"]
        self.assertEqual(rejects, [])


class TestSpendBounds(unittest.TestCase):
    def test_sourcing_never_extends_and_format_repairs_run_tiny(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "qual").mkdir()
            kwargs_seen = []

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                kwargs_seen.append(kw)
                return json.dumps({"scope": "market",
                                   "candidates": [_cand()]},
                                  ensure_ascii=False), "eng"

            q = {"accepted": [{"entity": "X", "stem": "x",
                               "op": {"context": {"hypotheses": []},
                                      "interview_brief": {}},
                               "record": {}, "issues": []}],
                 "rejected": [], "pending": [], "records_gate": {"accepted": []}}
            # pending market sourcing
            r1 = {"accepted": [], "rejected": [],
                  "pending": ["Market level"], "qual": q}
            # format-only rejected file
            e = {"label": "Market level", "scope": "market",
                 "stem": respondents.MARKET_STEM,
                 "path": respondents.resp_path(rd, respondents.MARKET_STEM),
                 "doc": {"scope": "market", "candidates": [_cand(priority=9)]},
                 "issues": [{"field": "x.priority", "severity": "reject",
                             "code": "bad-enum", "reason": "r"}],
                 "verdict": "rejected"}
            r2 = {"accepted": [], "rejected": [e], "pending": [], "qual": q}
            for r in (r1, r2):
                with patch.object(api_runner.runs, "_load_meta",
                                  return_value={"market": "m", "run_id": "t",
                                                "output_language": "Russian"}), \
                     patch.object(respondents, "gate_respondents", return_value=r), \
                     patch.object(respondents.onepager, "load_meta",
                                  return_value={"research_goal": "g", "companies": {}}), \
                     patch.object(mr, "collect", side_effect=collect), \
                     patch.object(mr, "MODE", "gpt"):
                    api_runner.run_respondent_step(rd, 2, log=lambda *_: None)
            self.assertEqual(kwargs_seen[0]["allow_extend"], False)   # sourcing
            self.assertEqual(kwargs_seen[0]["budget"],
                             mr.stage_budget("respondents"))
            self.assertEqual(kwargs_seen[1]["budget"], 4)             # format-only
            self.assertEqual(kwargs_seen[1]["allow_extend"], False)
