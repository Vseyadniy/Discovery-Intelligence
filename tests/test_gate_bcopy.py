"""b-copy independence check — brand-name exemption for key_products.
Run with:  python -m unittest tests.test_gate_bcopy"""
import unittest

from src import gate

_PROSE = ("Контур.Эльба — облачная бухгалтерия для ИП и микробизнеса на УСН и "
          "патенте, запущена СКБ Контур в 2010 году; тарифы от 1 900 ₽ в месяц, "
          "автоматически считает налоги и формирует отчётность в ФНС.")


def _issues(a_val, b_val, field="key_products", entity="Контур.Эльба"):
    """Gate a minimal record where collectors A and B share one field value."""
    mk = lambda v: {"fields": {field: {"value": v, "source": "https://x.ru/a"},
                               "other": {"value": "разное" if v == a_val else "иное",
                                         "source": "https://y.ru/b"}}}
    a, b = mk(a_val), mk(b_val)
    b["fields"]["other"]["source"] = "https://b-only.ru/c"   # avoid b-no-new-source
    rec = {"entity": entity, "fields": {}}
    return [i for i in gate.validate_record(rec, a, b) if i["code"] == "b-copy"]


class TestBCopyBrandException(unittest.TestCase):
    def test_identical_brand_name_not_bcopy(self):
        # (a) both collectors honestly name the single product = the brand
        self.assertEqual(_issues("Контур.Эльба", "Контур.Эльба"), [])

    def test_brand_with_short_qualifier_not_bcopy(self):
        self.assertEqual(
            _issues("Контур.Эльба (онлайн-бухгалтерия)",
                    "Контур.Эльба (онлайн-бухгалтерия)"), [])

    def test_copied_description_prose_still_bcopy(self):
        # (b) genuinely copied prose in a prose field must still fire
        self.assertEqual(len(_issues(_PROSE, _PROSE, field="description")), 1)

    def test_copied_latest_news_still_bcopy(self):
        news = "05.2025: СКБ Контур купил MPStats за 5 млрд ₽ (Forbes)"
        self.assertEqual(len(_issues(news, news, field="latest_news")), 1)

    def test_short_nonbrand_key_products_still_bcopy(self):
        # (c) short copied text that is NOT the brand name gets no exemption
        self.assertEqual(
            len(_issues("облачная бухгалтерия для ИП",
                        "облачная бухгалтерия для ИП")), 1)

    def test_long_brand_mentioning_prose_in_key_products_still_bcopy(self):
        # ≥40 chars: containing the brand does not exempt copied prose
        text = "Контур.Эльба — облачная бухгалтерия для ИП и микробизнеса на УСН"
        self.assertEqual(len(_issues(text, text)), 1)


if __name__ == "__main__":
    unittest.main()
