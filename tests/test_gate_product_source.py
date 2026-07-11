"""product-source-missing check — product revenue figures need their method.
Run with:  python -m unittest tests.test_gate_product_source"""
import unittest

from src import gate


def _rec(**fields):
    return {"entity": "Тест",
            "fields": {n: {"value": v, "source": "https://x.ru/a"}
                       for n, v in fields.items()}}


def _codes(rec):
    return {i["code"] for i in gate.validate_record(rec, None, None)}


class TestProductSourceMissing(unittest.TestCase):
    def test_revenue_without_method_rejected(self):
        rec = _rec(product_revenue_2024="~700 млн ₽")
        self.assertIn("product-source-missing", _codes(rec))

    def test_yoy_alone_also_requires_method(self):
        rec = _rec(product_rev_yoy_24_25="24%")
        self.assertIn("product-source-missing", _codes(rec))

    def test_revenue_with_method_ok(self):
        rec = _rec(product_revenue_2024="~700 млн ₽",
                   product_revenue_source="оценка: доля Эльбы ~2% выручки СКБ "
                                          "Контур по структуре продуктов TAdviser")
        self.assertNotIn("product-source-missing", _codes(rec))

    def test_placeholder_method_still_rejected(self):
        rec = _rec(product_revenue_2024="~700 млн ₽", product_revenue_source="н/д")
        self.assertIn("product-source-missing", _codes(rec))

    def test_no_product_figures_no_requirement(self):
        rec = _rec(total_revenue_2024="40 400 млн ₽")
        self.assertNotIn("product-source-missing", _codes(rec))


class TestPlaceholderNuance(unittest.TestCase):
    def test_methodology_mentioning_disclosure_gap_is_not_placeholder(self):
        self.assertFalse(gate.is_placeholder(
            "оценка: рассчитано как ~1,5–2% консолидированной выручки СКБ "
            "Контур; выручка продукта не раскрывается отдельно в отчётности"))

    def test_short_disclosure_stub_still_placeholder(self):
        self.assertTrue(gate.is_placeholder("не раскрывается"))

    def test_long_excuse_without_any_figure_still_placeholder(self):
        self.assertTrue(gate.is_placeholder(
            "информация о выручке не найдена в открытых источниках, компания "
            "не раскрывает данные и не публикует отчётность"))

    def test_exact_stub_tokens_still_placeholder(self):
        self.assertTrue(gate.is_placeholder("н/д"))


if __name__ == "__main__":
    unittest.main()
