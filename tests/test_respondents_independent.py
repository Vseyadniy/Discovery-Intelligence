"""Respondent sourcing as an INDEPENDENT optional stage: runs before/without
one-pagers (market, run-backed and manual targets), refines once one-pagers
appear (update + merge, never discard), continues past partial runs.
Run with: python -m unittest tests.test_respondents_independent"""
import json
import os
import time
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src import api_runner, onepager, respondents, runs
from src import model_router as mr

TODAY = date.today().isoformat()


def _cand(**over):
    c = {"name": "Иван Иванов", "role": "Директор по цифровизации",
         "org": "Банк N", "why_relevant": "внедрял BPM",
         "addresses": ["buying_behavior"], "priority": 1,
         "profile_url": "https://conf.ru/speakers/ivanov",
         "contact_route": "через форму на профиле",
         "sources": ["https://conf.ru/speakers/ivanov"],
         "confidence": "high", "role_current": True, "verified_on": TODAY}
    c.update(over)
    return c


def _mkrun(tmpdir: str) -> Path:
    rd = Path(tmpdir)
    (rd / "qual").mkdir(parents=True, exist_ok=True)
    (rd / "agent_runs").mkdir(exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"run_id": "2026-07-01_1200_m_superficial", "market": "SaaS BPM",
         "depth": "superficial", "model": "chatgpt",
         "output_language": "Russian", "status": "x"}, ensure_ascii=False),
        encoding="utf-8")
    return rd


def _save_resp(rd, stem, doc):
    respondents.resp_path(rd, stem).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ── scenario 1 + 3: market/segment + manual-company sourcing, no one-pagers ──
class TestSourcingWithoutOnePagers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = _mkrun(self.tmp.name)
        onepager.setup(self.rd, "понять критерии выбора BPM",
                       {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM/BPM",
                                            "notes": "переходят в облако"}})

    def test_targets_exist_without_onepagers(self):
        r = respondents.gate_respondents(self.rd)
        self.assertIn("Directum", r["targets"])
        self.assertEqual(r["targets"]["Directum"]["op"], {})
        self.assertEqual(sorted(r["pending"]), ["Directum", "Market level"])

    def test_market_prompt_from_goal_and_user_context(self):
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-market")
        self.assertIn("понять критерии выбора BPM", text)      # research goal
        self.assertIn("ECM/BPM", text)                          # user segment
        self.assertNotIn('"hypotheses"', text)                  # none exist yet
        self.assertIn("without one-pagers yet", text)           # theme guidance
        self.assertIn("buying_behavior", text)

    def test_company_prompt_uses_manual_context_and_themes(self):
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand()]})
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-company")
        self.assertIn("Directum", text)
        self.assertIn("переходят в облако", text)               # user notes
        self.assertIn("No one-pager exists for this company yet", text)
        self.assertNotIn("Hypotheses to address", text)

    def test_invented_hyp_ref_rejected_pre_onepager(self):
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand(addresses=["H1"])]})
        r = respondents.gate_respondents(self.rd)
        codes = {i["code"] for e in r["rejected"] for i in e["issues"]}
        self.assertIn("orphan-ref", codes)

    def test_done_without_onepagers_writes_shortlist(self):
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand()]})
        _save_resp(self.rd, "directum",
                   {"scope": "company", "entity": "Directum",
                    "candidates": [_cand(name="Пётр Петров", org="Directum")]})
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "done")
        self.assertIn("respondents_shortlist.md", text)
        md = respondents.shortlist_path(self.rd).read_text(encoding="utf-8")
        self.assertIn("Иван Иванов", md)
        self.assertIn("Пётр Петров", md)
        p = respondents.progress(self.rd)
        self.assertEqual(p["phase"], "done — 2 candidates")


# ── scenario 2: run-backed company, no one-pagers ────────────────────────────
class TestRunBackedWithoutOnePagers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = _mkrun(self.tmp.name)
        onepager.setup(self.rd, "оценить конкурентов", {"Comindware": "competitor"})
        rec = {"entity": "Comindware", "fields": {
            "segment": {"value": "Low-code BPM", "source": "https://x.ru/a"},
            "website": {"value": "https://comindware.ru", "source": "https://x.ru/a"},
            "positioning": {"value": "локальная low-code платформа",
                            "source": "https://x.ru/a"}}}
        self.q = {"accepted": [], "rejected": [], "pending": ["Comindware"],
                  "records_gate": {"accepted": [
                      {"entity": "Comindware", "stem": "comindware",
                       "record": rec, "issues": []}]}}

    def test_record_context_reaches_the_prompt(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            r = respondents.gate_respondents(self.rd)
            self.assertEqual(r["targets"]["Comindware"]["op"], {})
            _save_resp(self.rd, respondents.MARKET_STEM,
                       {"scope": "market", "candidates": [_cand()]})
            kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-company")
        self.assertIn("https://comindware.ru", text)            # website
        self.assertIn("локальная low-code платформа", text)     # positioning
        self.assertIn("No one-pager exists for this company yet", text)


# ── scenario 4: refinement after one-pagers appear (+ merge semantics) ───────
class TestRefinement(unittest.TestCase):
    OP = {"entity": "Directum", "angle": "competitor",
          "context": {"hypotheses": [{"id": "H1", "text": "гипотеза",
                                      "basis": "hypothesis", "validated_if": "да"}]},
          "interview_brief": {"respondents": [
              {"type": "customer", "who": "ИТ-директор банка", "why": "покупает",
               "priority": 1}]},
          "priorities": {}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = _mkrun(self.tmp.name)
        self.q = {"accepted": [{"entity": "Directum", "stem": "directum",
                                "op": self.OP,
                                "record": {"entity": "Directum", "fields": {}},
                                "issues": [], "verdict": "accepted"}],
                  "rejected": [], "pending": [], "records_gate": {"accepted": []}}
        # market respondents sourced FIRST (old mtime)…
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand()]})
        old = time.time() - 600
        os.utime(respondents.resp_path(self.rd, respondents.MARKET_STEM), (old, old))
        # …then the one-pager was accepted (newer mtime)…
        (self.rd / "qual" / "directum_onepager.json").write_text(
            json.dumps(self.OP, ensure_ascii=False), encoding="utf-8")
        # …and the company file was sourced AFTER it (fresh — no refine needed)
        _save_resp(self.rd, "directum",
                   {"scope": "company", "entity": "Directum",
                    "candidates": [_cand(name="Пётр Петров", org="Directum",
                                         addresses=["H1"])]})

    def test_gate_flags_refine_and_prompt_merges(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            r = respondents.gate_respondents(self.rd)
            self.assertEqual([e["label"] for e in r["refine"]], ["Market level"])
            self.assertEqual(respondents.progress(self.rd)["refine"], 1)
            kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-refine")
        self.assertIn("Иван Иванов", text)      # existing shortlist included
        self.assertIn("H1", text)               # new hypotheses included
        self.assertIn("ИТ-директор банка", text)  # archetypes included

    def test_refine_pass_clears_after_resave(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            _save_resp(self.rd, respondents.MARKET_STEM,
                       {"scope": "market", "candidates": [_cand(addresses=["H1"])]})
            r = respondents.gate_respondents(self.rd)
        self.assertEqual(r["refine"], [])
        self.assertEqual(len(r["accepted"]), 2)   # market (re-saved) + company

    def test_merge_keeps_old_updates_and_adds(self):
        old = {"scope": "market",
               "candidates": [_cand(), _cand(name="Пётр Петров", org="X")]}
        new = {"scope": "market",
               "candidates": [_cand(role="CIO"),                    # updated
                              _cand(name="Анна Сидорова", org="Y")]}  # added
        merged = respondents.merge_candidates(old, new)
        names = [(c["name"], c["role"]) for c in merged["candidates"]]
        self.assertEqual(names, [("Иван Иванов", "CIO"),
                                 ("Пётр Петров", "Директор по цифровизации"),
                                 ("Анна Сидорова", "Директор по цифровизации")])

    def test_api_refine_merges_after_grounding(self):
        new_doc = {"scope": "market",
                   "candidates": [_cand(name="Анна Сидорова", org="Y",
                                        addresses=["H1"])]}

        def collect(system, user, max_tokens=16000, on_event=None, **kw):
            self.assertIn("refinement pass", user)
            return json.dumps(new_doc, ensure_ascii=False), "eng"

        with patch.object(onepager, "gate_qual", return_value=self.q), \
             patch.object(mr, "collect", side_effect=collect), \
             patch.object(mr, "MODE", "gpt"):
            summary = api_runner.run_respondent_step(self.rd, 1, log=lambda *_: None)
        self.assertIn("refined 1: Market level", summary)
        saved = json.loads(respondents.resp_path(
            self.rd, respondents.MARKET_STEM).read_text(encoding="utf-8"))
        names = {c["name"] for c in saved["candidates"]}
        self.assertEqual(names, {"Иван Иванов", "Анна Сидорова"})  # merged


# ── scenario 5: loading & continuing a past partial run ──────────────────────
class TestContinuePartialRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = _mkrun(self.tmp.name)
        onepager.setup(self.rd, "goal",
                       {"AlphaBPM": "competitor", "BetaBPM": "customer"},
                       manual={"AlphaBPM": {"segment": "BPM"},
                               "BetaBPM": {"segment": "BPM"}})
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand()]})
        _save_resp(self.rd, "alphabpm",
                   {"scope": "company", "entity": "AlphaBPM",
                    "candidates": [_cand(name="Пётр Петров", org="AlphaBPM")]})

    def test_only_the_missing_company_is_pending(self):
        r = respondents.gate_respondents(self.rd)
        self.assertEqual(r["pending"], ["BetaBPM"])
        self.assertEqual(len(r["accepted"]), 2)
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-company")
        self.assertIn("BetaBPM", text)
        self.assertNotIn("### 🎯 AlphaBPM", text)   # finished work not redone

    def test_completing_the_run_reaches_done(self):
        _save_resp(self.rd, "betabpm",
                   {"scope": "company", "entity": "BetaBPM",
                    "candidates": [_cand(name="Анна Сидорова", org="BetaBPM")]})
        kind, _ = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "done")
        md = respondents.shortlist_path(self.rd).read_text(encoding="utf-8")
        for name in ("Иван Иванов", "Пётр Петров", "Анна Сидорова"):
            self.assertIn(name, md)


# ── the research goal is OPTIONAL for sourcing (required for one-pagers) ─────
class TestGoalOptional(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = _mkrun(self.tmp.name)

    def test_setup_without_goal_persists_targets(self):
        onepager.setup(self.rd, "", {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM/BPM"}},
                       require_goal=False)
        meta = onepager.load_meta(self.rd)
        self.assertIn("Directum", meta["companies"])
        self.assertEqual(meta["research_goal"], "")

    def test_setup_without_goal_never_erases_a_saved_goal(self):
        onepager.setup(self.rd, "выбрать вендора", {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM"}})
        onepager.setup(self.rd, "", {"Directum": "customer"}, require_goal=False)
        meta = onepager.load_meta(self.rd)
        self.assertEqual(meta["research_goal"], "выбрать вендора")  # kept
        self.assertEqual(meta["companies"]["Directum"]["angle"], "customer")

    def test_onepager_track_still_requires_the_goal(self):
        with self.assertRaises(ValueError):
            onepager.setup(self.rd, "", {"Directum": "competitor"})   # default
        onepager.setup(self.rd, "", {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM"}},
                       require_goal=False)
        with self.assertRaises(SystemExit):
            onepager.next_qual_prompt(self.rd, 2)   # one-pagers still gated

    def test_sourcing_prompts_work_without_a_goal(self):
        onepager.setup(self.rd, "", {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM/BPM",
                                            "notes": "переходят в облако"}},
                       require_goal=False)
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-market")
        self.assertIn("(not set — judge relevance", text)   # goal fallback
        self.assertIn("ECM/BPM", text)                      # target context
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market", "candidates": [_cand()]})
        kind, text = respondents.next_respondent_prompt(self.rd, 2)
        self.assertEqual(kind, "respondents-company")
        self.assertIn("(not set — judge relevance", text)
        self.assertIn("переходят в облако", text)

    def test_gate_and_privacy_rules_unchanged_without_goal(self):
        onepager.setup(self.rd, "", {"Directum": "competitor"},
                       manual={"Directum": {"segment": "ECM"}},
                       require_goal=False)
        _save_resp(self.rd, respondents.MARKET_STEM,
                   {"scope": "market",
                    "candidates": [_cand(contact_route="ivanov@bank.ru")]})
        r = respondents.gate_respondents(self.rd)
        # the gate now HEALS private contacts deterministically (autofix):
        # the email is stripped from the saved file, the file is accepted
        self.assertEqual(len(r["accepted"]), 1)
        saved = json.loads(respondents.resp_path(
            self.rd, respondents.MARKET_STEM).read_text(encoding="utf-8"))
        self.assertNotIn("ivanov@bank.ru", json.dumps(saved))
        # the validator net itself is unchanged for anything not autofixed
        raw = {"scope": "market", "candidates": [_cand(contact_route="x@y.ru")]}
        codes = {i["code"] for i in respondents.validate_respondents(
            raw, set(), "market") if i["severity"] == "reject"}
        self.assertIn("private-contact", codes)


# ── API mode is equally independent of one-pagers ────────────────────────────
class TestApiWithoutOnePagers(unittest.TestCase):
    def test_market_sourcing_runs_and_completes_without_onepagers(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _mkrun(td)
            onepager.setup(rd, "goal", {"Directum": "competitor"},
                           manual={"Directum": {"segment": "ECM"}})
            market_doc = {"scope": "market", "candidates": [_cand()]}
            company_doc = {"scope": "company", "entity": "Directum",
                           "candidates": [_cand(name="Пётр Петров",
                                                org="Directum")]}
            docs = iter([market_doc, company_doc])

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                return json.dumps(next(docs), ensure_ascii=False), "eng"

            with patch.object(mr, "collect", side_effect=collect), \
                 patch.object(mr, "MODE", "gpt"):
                s1 = api_runner.run_respondent_step(rd, 2, log=lambda *_: None)
                s2 = api_runner.run_respondent_step(rd, 2, log=lambda *_: None)
                s3 = api_runner.run_respondent_step(rd, 2, log=lambda *_: None)
            self.assertIn("sourced 1: Market level", s1)
            self.assertIn("sourced 1: Directum", s2)
            self.assertIn("respondent sourcing complete", s3)
            self.assertIn("Respondents sheet", s3)   # outreach Excel is the deliverable
            self.assertTrue(respondents.shortlist_path(rd).exists())


if __name__ == "__main__":
    unittest.main()
