"""Record/field quality states + per-stage budgets with field-aware stopping.
Run with:  python -m unittest tests.test_quality_and_budgets"""
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src import gate
from src import model_router as mr
from tests.test_deepseek_loop import chunk, search_call


def _f(v, src="https://x.ru/a", conf=None):
    d = {"value": v, "source": src}
    if conf:
        d["confidence"] = conf
    return d


SCHEMA = ["description", "segment", "key_products", "business_model",
          "target_customers", "positioning", "inn", "headcount",
          "total_revenue_2025", "latest_news"]

_FULL = {n: _f("значение") for n in SCHEMA}


class TestRecordQuality(unittest.TestCase):
    def test_complete_record(self):
        q = gate.record_quality({"fields": dict(_FULL)}, SCHEMA)
        self.assertEqual(q["status"], "complete")
        self.assertEqual(q["coverage_pct"], 100)
        self.assertEqual(q["mandatory_gaps"], [])

    def test_gaps_and_unresolved_distinguished(self):
        fields = dict(_FULL)
        fields["latest_news"] = _f(None)                     # plain gap
        fields["headcount"] = {"value": "", "source": ""}    # blanked
        rec = {"fields": fields,
               "review_flags": ["unresolved: headcount — нет квоты"]}
        q = gate.record_quality(rec, SCHEMA)
        self.assertEqual(q["missing"], ["latest_news"])
        self.assertEqual(q["unresolved"], ["headcount"])
        self.assertEqual(q["status"], "gaps 2")
        self.assertEqual(q["coverage_pct"], 80)
        self.assertIn("headcount", q["mandatory_gaps"])      # registry field
        self.assertNotIn("latest_news", q["mandatory_gaps"])

    def test_low_confidence_counted_not_a_gap(self):
        fields = dict(_FULL)
        fields["total_revenue_2025"] = _f("100 млн ₽", conf="low")
        q = gate.record_quality({"fields": fields}, SCHEMA)
        self.assertEqual(q["low_confidence"], ["total_revenue_2025"])
        self.assertEqual(q["status"], "low-conf 1")
        self.assertEqual(q["coverage_pct"], 100)             # filled, just weak

    def test_quality_never_changes_gate_verdict(self):
        rec = {"entity": "Т", "fields": dict(_FULL)}
        before = [i["code"] for i in gate.validate_record(rec, None, None)]
        gate.record_quality(rec, SCHEMA)
        after = [i["code"] for i in gate.validate_record(rec, None, None)]
        self.assertEqual(before, after)                      # purely additive


class TestStageBudgets(unittest.TestCase):
    def test_env_overrides_and_defaults(self):
        with patch.dict(os.environ, {"DS_BUDGET_REPAIR": "5"}):
            self.assertEqual(mr.stage_budget("repair"), 5)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DS_BUDGET_REPAIR", None)
            self.assertEqual(mr.stage_budget("repair"), 12)
            self.assertEqual(mr.stage_budget("collector_a"), 25)
            self.assertEqual(mr.stage_budget("discovery"), 20)
        with patch.dict(os.environ, {"DS_BUDGET_DISCOVERY": "junk"}):
            self.assertEqual(mr.stage_budget("discovery"), 20)   # bad value → default

    def _run(self, iterations, results_seq, **kw):
        fc = MagicMock()
        fc.chat.completions.create = MagicMock(
            side_effect=[iter(i) for i in iterations])
        it = iter(results_seq)
        with patch.object(mr, "_deepseek", return_value=fc), \
             patch("src.web_tools.web_search", side_effect=lambda q, c=8: next(it)):
            return mr._run_deepseek_tools("SYS", "USER", **kw)

    def test_early_stop_when_no_new_evidence(self):
        # budget 10 with extension, but every search returns the SAME url —
        # after the novelty window dries up the pass must finish early
        same = [{"title": "t", "url": "https://a.ru/x", "snippet": "s"}]
        its = [search_call(f"c{i}") for i in range(9)] + \
              [[chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        text, log = self._run(its, [same] * 9, budget=10, allow_extend=True)
        self.assertEqual(text, '{"fields": {}}')
        self.assertEqual(log.stats["early_stop"], 1)
        self.assertLess(log.tool_calls, 10)                  # budget NOT exhausted

    def test_extension_only_while_novel_evidence(self):
        # budget 2, extension allowed: every call finds a NEW url → the pass
        # may run past base, up to base+extend
        fresh = [[{"title": "t", "url": f"https://a.ru/{i}", "snippet": "s"}]
                 for i in range(4)]
        its = [search_call(f"c{i}") for i in range(4)] + \
              [[chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        with patch.dict(os.environ, {"DS_BUDGET_EXTEND": "3"}):
            text, log = self._run(its, fresh, budget=2, allow_extend=True)
        self.assertEqual(log.tool_calls, 4)                  # 2 base + 2 earned
        self.assertEqual(log.stats["extended"], 2)

    def test_no_extension_when_not_allowed(self):
        fresh = [[{"title": "t", "url": f"https://a.ru/{i}", "snippet": "s"}]
                 for i in range(4)]
        its = [search_call(f"c{i}") for i in range(4)] + \
              [[chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        _text, log = self._run(its, fresh, budget=2, allow_extend=False)
        self.assertEqual(log.tool_calls, 2)                  # hard stop at base


if __name__ == "__main__":
    unittest.main()
