"""Optional respondent-sourcing stage: own validation (public data only),
prompts, gate, report wiring. Run with: python -m unittest tests.test_respondents"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src import onepager, respondents, runs

TODAY = date.today().isoformat()


def _cand(**over):
    c = {"name": "Иван Иванов", "role": "Директор по цифровизации",
         "org": "Банк N", "why_relevant": "внедрял BPM в 2025",
         "addresses": ["H1"], "priority": 1,
         "profile_url": "https://conf.ru/speakers/ivanov",
         "contact_route": "через форму на профиле",
         "sources": ["https://conf.ru/speakers/ivanov", "https://bank-n.ru/team"],
         "confidence": "high", "role_current": True, "verified_on": TODAY}
    c.update(over)
    return c


def _doc(*cands, scope="company", entity="Directum"):
    d = {"scope": scope, "candidates": list(cands) or [_cand()]}
    if scope == "company":
        d["entity"] = entity
    return d


def _codes(doc, hyp_ids={"H1", "H2"}, scope="company", entity="Directum"):
    return {(i["code"], i["severity"])
            for i in respondents.validate_respondents(doc, hyp_ids, scope, entity)}


class TestPrivacyRules(unittest.TestCase):
    def test_valid_candidate_passes(self):
        self.assertEqual([i for i in respondents.validate_respondents(
            _doc(), {"H1"}, "company", "Directum")
            if i["severity"] == "reject"], [])

    def test_email_anywhere_is_rejected(self):
        for doc in (_doc(_cand(contact_route="ivanov@bank-n.ru")),
                    _doc(_cand(why_relevant="писал на ivanov@bank-n.ru")),
                    {"scope": "company", "entity": "Directum",
                     "candidates": [_cand()], "note": "ivan@x.ru"}):
            self.assertIn(("private-contact", "reject"), _codes(doc))

    def test_phone_is_rejected(self):
        self.assertIn(("private-contact", "reject"),
                      _codes(_doc(_cand(contact_route="+7 916 123-45-67"))))

    def test_private_keys_rejected_even_when_empty(self):
        self.assertIn(("private-contact", "reject"),
                      _codes(_doc(_cand(email=""))))
        self.assertIn(("private-contact", "reject"),
                      _codes(_doc(_cand(personal_phone=None))))


class TestCandidateRules(unittest.TestCase):
    def test_required_fields(self):
        for k in ("name", "role", "org", "why_relevant", "profile_url",
                  "sources", "confidence", "verified_on"):
            self.assertIn(("required-empty", "reject"),
                          _codes(_doc(_cand(**{k: ""}))), k)

    def test_dedup(self):
        self.assertIn(("duplicate", "reject"), _codes(_doc(_cand(), _cand())))
        # same name, different org → not a duplicate
        self.assertNotIn(("duplicate", "reject"),
                         _codes(_doc(_cand(), _cand(org="Банк M"))))

    def test_orphan_hypothesis_ref(self):
        self.assertIn(("orphan-ref", "reject"),
                      _codes(_doc(_cand(addresses=["H9"])), hyp_ids={"H1"}))
        # theme refs are allowed
        self.assertNotIn(("orphan-ref", "reject"),
                         _codes(_doc(_cand(addresses=["buying_behavior"]))))

    def test_search_url_and_bad_source(self):
        self.assertIn(("search-url", "reject"), _codes(
            _doc(_cand(profile_url="https://www.google.com/search?q=ivanov"))))
        self.assertIn(("bad-source", "reject"),
                      _codes(_doc(_cand(sources=["docs/gold/x.md"]))))

    def test_verification_date_and_stale_role(self):
        self.assertIn(("bad-date", "reject"), _codes(_doc(_cand(verified_on="вчера"))))
        future = (date.today() + timedelta(days=2)).isoformat()
        self.assertIn(("bad-date", "reject"), _codes(_doc(_cand(verified_on=future))))
        self.assertIn(("stale-role", "reject"), _codes(_doc(_cand(role_current=False))))

    def test_enums_and_low_confidence_warn(self):
        self.assertIn(("bad-enum", "reject"), _codes(_doc(_cand(priority=7))))
        self.assertIn(("bad-enum", "reject"), _codes(_doc(_cand(confidence="maybe"))))
        self.assertIn(("low-confidence", "warn"), _codes(_doc(_cand(confidence="low"))))

    def test_scope_and_entity_guard(self):
        self.assertIn(("bad-enum", "reject"),
                      _codes(_doc(scope="market"), scope="company"))
        self.assertIn(("bad-enum", "reject"),
                      _codes(_doc(entity="Другая"), entity="Directum"))
        self.assertEqual([i for i in respondents.validate_respondents(
            {"scope": "market", "candidates": [_cand()]}, {"H1"}, "market")
            if i["severity"] == "reject"], [])


class _QualRun(unittest.TestCase):
    """A run with one accepted one-pager — the state the optional stage needs."""

    OP = {"entity": "Directum", "angle": "competitor",
          "context": {"summary": {"text": "s", "basis": "fact",
                                  "source_fields": ["segment"]},
                      "hypotheses": [{"id": "H1", "text": "гипотеза",
                                      "basis": "hypothesis", "validated_if": "да"}]},
          "interview_brief": {"respondents": [
              {"type": "customer", "who": "ИТ-директор банка", "why": "покупает",
               "priority": 1}]},
          "priorities": {"validate": ["H1"], "risks": ["r"],
                         "next_step": {"action": "interview", "why": "w"}}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = Path(self.tmp.name)
        (self.rd / "qual").mkdir(parents=True)
        (self.rd / "run.json").write_text(json.dumps(
            {"run_id": "2026-07-01_1200_m_superficial", "market": "рынок",
             "depth": "superficial", "model": "chatgpt",
             "output_language": "Russian", "status": "x"}, ensure_ascii=False),
            encoding="utf-8")
        self.entry = {"entity": "Directum", "stem": "directum", "op": self.OP,
                      "record": {"entity": "Directum", "fields": {
                          "segment": {"value": "ECM", "source": "https://x.ru/a"}}},
                      "issues": [], "verdict": "accepted"}
        self.q = {"accepted": [self.entry], "rejected": [], "pending": [],
                  "records_gate": {"accepted": []}}
        self.qmeta = {"research_goal": "выбрать вендора",
                      "companies": {"Directum": {"angle": "competitor"}}}


class TestPromptsAndGate(_QualRun):
    def test_market_and_company_prompts_carry_context_and_rules(self):
        meta_run = runs._load_meta(self.rd)
        mk = respondents.build_respondent_prompt(self.rd, [self.entry], meta_run,
                                                 self.qmeta, market=True)
        co = respondents.build_respondent_prompt(self.rd, [self.entry], meta_run,
                                                 self.qmeta, market=False)
        for text in (mk, co):
            self.assertIn("NO email addresses and NO phone numbers", text)
            self.assertIn("verified_on", text)
            self.assertIn("выбрать вендора", text)          # research goal
            self.assertIn("H1", text)                       # hypotheses
            self.assertIn("ИТ-директор банка", text)        # archetypes
        self.assertIn("MARKET level", mk)
        self.assertIn("_market_respondents.json", mk)
        self.assertIn("COMPANY level", co)
        self.assertIn("directum_respondents.json", co)

    def test_gate_flow_pending_rejected_accepted(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            r = respondents.gate_respondents(self.rd)
            self.assertEqual(sorted(r["pending"]), ["Directum", "Market level"])

            # market file first, then the company file
            respondents.resp_path(self.rd, respondents.MARKET_STEM).write_text(
                json.dumps({"scope": "market", "candidates": [_cand()]},
                           ensure_ascii=False), encoding="utf-8")
            r = respondents.gate_respondents(self.rd)
            self.assertEqual(r["pending"], ["Directum"])
            self.assertEqual(len(r["accepted"]), 1)

            # a rejected company file — empty candidates cannot be autofixed
            respondents.resp_path(self.rd, "directum").write_text(
                json.dumps({"scope": "company", "entity": "Directum",
                            "candidates": []}, ensure_ascii=False),
                encoding="utf-8")
            r = respondents.gate_respondents(self.rd)
            self.assertEqual(len(r["rejected"]), 1)
            self.assertEqual(respondents.progress(self.rd)["phase"], "repair — 1 rejected")

            kind, text = respondents.next_respondent_prompt(self.rd, 2)
            self.assertEqual(kind, "respondents-repair")
            self.assertIn("counts", text)

            # fixed — a DIFFERENT person than the market file (cross-file dedup)
            respondents.resp_path(self.rd, "directum").write_text(
                json.dumps(_doc(_cand(name="Пётр Петров", org="Directum")),
                           ensure_ascii=False), encoding="utf-8")
            r = respondents.gate_respondents(self.rd)
            self.assertEqual(len(r["accepted"]), 2)
            self.assertEqual(r["pending"], [])
            p = respondents.progress(self.rd)
            self.assertEqual(p["candidates"], 2)
            self.assertEqual(p["phase"], "done — 2 candidates")

    def test_prompt_stage_order_market_then_company(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            kind, text = respondents.next_respondent_prompt(self.rd, 2)
            self.assertEqual(kind, "respondents-market")
            respondents.resp_path(self.rd, respondents.MARKET_STEM).write_text(
                json.dumps({"scope": "market", "candidates": [_cand()]},
                           ensure_ascii=False), encoding="utf-8")
            kind, _ = respondents.next_respondent_prompt(self.rd, 2)
            self.assertEqual(kind, "respondents-company")

    def test_only_accepted_docs_reach_the_report(self):
        with patch.object(onepager, "gate_qual", return_value=self.q):
            respondents.resp_path(self.rd, "directum").write_text(
                json.dumps({"scope": "company", "entity": "Directum",
                            "candidates": []}, ensure_ascii=False),
                encoding="utf-8")
            self.assertEqual(respondents.accepted_docs(self.rd), {})  # rejected → absent
            respondents.resp_path(self.rd, "directum").write_text(
                json.dumps(_doc(_cand(name="Пётр Петров", org="Directum")),
                           ensure_ascii=False), encoding="utf-8")
            docs = respondents.accepted_docs(self.rd)
            self.assertIn("Directum", docs)

    def test_one_pager_generation_still_does_not_browse(self):
        # the optional stage must not leak into the qual prompt
        text = onepager.build_qual_prompt(
            self.rd, [self.entry], runs._load_meta(self.rd), self.qmeta,
            {"accepted": []})
        self.assertIn("Do NOT browse", text)
        self.assertNotIn("respondents.json", text)
        self.assertIn("respondents", text)      # archetypes stay in the brief


if __name__ == "__main__":
    unittest.main()
