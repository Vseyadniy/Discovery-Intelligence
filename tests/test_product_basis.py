"""Product-revenue evidence hierarchy: basis tags, gate exemptions, ranges,
confidence inheritance. Run with: python -m unittest tests.test_product_basis"""
import unittest
from pathlib import Path

from src import gate

ROOT = Path(__file__).resolve().parent.parent


def _fields(prs=None, **rev):
    f = {n: {"value": v, "source": ""} for n, v in rev.items()}
    if prs is not None:
        f["product_revenue_source"] = {"value": prs, "source": ""}
    return f


def _codes(fields):
    rec = {"entity": "Т", "fields": fields}
    return {(i["field"], i["code"], i["severity"])
            for i in gate.validate_record(rec, None, None)}


class TestBasisParsing(unittest.TestCase):
    def test_all_tags_and_states(self):
        cases = [("напрямую: https://x.ru/otchet", "direct"),
                 ("Расчёт: моно-продукт, выручка = вся выручка АО", "calculated"),
                 ("оценка: ~2% выручки группы по структуре продуктов", "estimated"),
                 ("Оценка на основе общей выручки и структуры", "estimated"),
                 ("smart-ranking.ru рейтинг 2025; доля ~60%", "untagged")]
        for prs, want in cases:
            f = _fields(prs=prs, product_revenue_2024="~700 млн ₽")
            self.assertEqual(gate.product_revenue_basis(f), want, prs)
        self.assertEqual(gate.product_revenue_basis(
            _fields(product_revenue_2024="~700 млн ₽")), "unavailable")
        self.assertIsNone(gate.product_revenue_basis(_fields(prs="оценка: x")))


class TestGateConsistency(unittest.TestCase):
    def test_tagged_estimate_not_rejected_for_missing_urls(self):
        # the whole point: a transparent estimate must survive the gate
        codes = _codes(_fields(prs="оценка: ~2% выручки СКБ Контур по структуре",
                               product_revenue_2024="~700 млн ₽",
                               product_revenue_2025="~870 млн ₽",
                               product_rev_yoy_24_25="24%"))
        for name in ("product_revenue_2024", "product_revenue_2025",
                     "product_rev_yoy_24_25"):
            self.assertNotIn((name, "unsourced", "reject"), codes, name)
        self.assertNotIn(("product_revenue_source", "product-source-missing",
                          "reject"), codes)

    def test_figures_without_any_method_still_rejected(self):
        codes = _codes(_fields(product_revenue_2024="~700 млн ₽"))
        self.assertIn(("product_revenue_source", "product-source-missing",
                       "reject"), codes)

    def test_untagged_method_is_warn_not_reject(self):
        codes = _codes(_fields(prs="smart-ranking.ru; доля ~60%",
                               product_revenue_2024="~700 млн ₽"))
        self.assertIn(("product_revenue_source", "product-basis-untagged",
                       "warn"), codes)
        self.assertNotIn(("product_revenue_2024", "unsourced", "reject"), codes)

    def test_non_product_fields_still_need_urls(self):
        codes = _codes({**_fields(prs="оценка: x", product_revenue_2024="~1 млн ₽"),
                        "headcount": {"value": "120", "source": ""}})
        self.assertIn(("headcount", "unsourced", "reject"), codes)


class TestRangesAndQuality(unittest.TestCase):
    def test_money_range_canonicalized(self):
        self.assertEqual(gate.normalize_money("700–900 млн ₽"), "700–900 млн ₽")
        self.assertEqual(gate.normalize_money("0,7–0,9 млрд ₽"), "700–900 млн ₽")
        self.assertEqual(gate.normalize_money("48,3 млрд ₽"), "48 300 млн ₽")
        self.assertIsNone(gate.normalize_money("дорого–богато"))

    def test_record_quality_carries_product_basis(self):
        q = gate.record_quality(
            {"fields": _fields(prs="расчёт: моно-продукт",
                               product_revenue_2024="700 млн ₽")},
            ["product_revenue_2024", "product_revenue_source"])
        self.assertEqual(q["product_basis"], "calculated")


class TestPromptsCarryHierarchy(unittest.TestCase):
    def test_collectors_and_verifier(self):
        a = (ROOT / "prompts" / "collector_a.md").read_text(encoding="utf-8")
        b = (ROOT / "prompts" / "collector_b.md").read_text(encoding="utf-8")
        v = (ROOT / "prompts" / "verifier.md").read_text(encoding="utf-8")
        self.assertIn("evidence hierarchy", a)
        self.assertIn("mono-product / pure-play company's product revenue IS its "
                      "total revenue", a)
        self.assertIn("SWITCH to an estimate", b)
        self.assertIn("«напрямую» (published", v)
        self.assertIn("inherit the LOWEST confidence", v)


if __name__ == "__main__":
    unittest.main()
