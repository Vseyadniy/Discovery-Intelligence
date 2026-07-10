"""Unit tests for src/web_tools.py — run with:  python -m unittest tests.test_web_tools"""
import unittest
from unittest.mock import MagicMock, patch

from src.web_tools import MAX_PAGE_CHARS, SourceLog, fetch_url


def _record(**sources):
    return {"fields": {name: {"value": "x", "source": src}
                       for name, src in sources.items()}}


class TestCheckGrounding(unittest.TestCase):
    def setUp(self):
        self.log = SourceLog()
        self.log.log_search([{"title": "t", "url": "https://www.site.ru/page?utm=1",
                              "snippet": "s"}])
        self.log.log_fetch("https://old.example.com/a",
                           {"url": "https://old.example.com/a",
                            "final_url": "https://new.example.com/b/",
                            "text": "page body"})

    def test_exact_match_untouched(self):
        rec = _record(revenue="http://site.ru/page/")   # scheme/query/slash ignored
        self.assertEqual(self.log.check_grounding(rec), [])
        self.assertEqual(rec["fields"]["revenue"]["source"], "http://site.ru/page/")
        self.assertNotIn("review_flags", rec)

    def test_domain_only_match_kept_but_flagged(self):
        rec = _record(inn="https://site.ru/other-page")
        details = self.log.check_grounding(rec)
        self.assertEqual(len(details), 1)
        self.assertEqual(rec["fields"]["inn"]["source"], "https://site.ru/other-page")
        self.assertEqual(rec["review_flags"],
                         ["inn: source URL not opened this session (domain seen: site.ru)"])
        # full URL only in the returned details, never in review_flags
        self.assertIn("https://site.ru/other-page", details[0])

    def test_ungrounded_source_stripped(self):
        rec = _record(headcount="https://nowhere.io/facts/2024/report")
        details = self.log.check_grounding(rec)
        self.assertEqual(len(details), 1)
        self.assertEqual(rec["fields"]["headcount"]["source"], "")
        self.assertEqual(rec["review_flags"],
                         ["headcount: ungrounded source removed (nowhere.io)"])
        # the year-bearing URL must not leak into review_flags (gate suppression)
        self.assertNotIn("2024", rec["review_flags"][0])
        self.assertIn("https://nowhere.io/facts/2024/report", details[0])

    def test_redirect_final_url_grounds(self):
        rec = _record(website="https://new.example.com/b")
        self.assertEqual(self.log.check_grounding(rec), [])

    def test_non_url_and_blank_sources_ignored(self):
        rec = {"fields": {"a": {"value": "x", "source": ""},
                          "b": {"value": "x"},
                          "c": "plain string"}}
        self.assertEqual(self.log.check_grounding(rec), [])

    def test_only_fields_scopes_repair_audit(self):
        # repair passes must not strip sources from fields they weren't fixing
        rec = _record(inn="https://untouched.example/card",
                      revenue="https://also-ungrounded.example/x")
        details = self.log.check_grounding(rec, only_fields={"revenue"})
        self.assertEqual(len(details), 1)
        self.assertEqual(rec["fields"]["inn"]["source"],
                         "https://untouched.example/card")   # kept
        self.assertEqual(rec["fields"]["revenue"]["source"], "")  # audited

    def test_multi_url_source_grounded_by_any_part(self):
        rec = _record(desc="https://site.ru/page, https://never-seen.example/x")
        self.assertEqual(self.log.check_grounding(rec), [])   # one part visited
        rec2 = _record(desc="https://never.example/a, https://never.example/b")
        details = self.log.check_grounding(rec2)
        self.assertEqual(len(details), 1)
        self.assertEqual(rec2["fields"]["desc"]["source"], "")
        self.assertEqual(rec2["review_flags"],
                         ["desc: ungrounded source removed (never.example)"])

    def test_no_fields_dict_is_noop(self):
        self.assertEqual(self.log.check_grounding({"companies": [], "segments": []}), [])
        self.assertEqual(self.log.check_grounding({}), [])


class TestFetchUrl(unittest.TestCase):
    @patch("src.web_tools.requests.get")
    def test_truncation_and_visible_text(self, get):
        resp = MagicMock()
        resp.text = ("<html><head><title> Page </title><script>junk()</script></head>"
                     "<body><nav>menu</nav><p>" + "слово " * 5000 + "</p></body></html>")
        resp.url = "https://x.ru/final"
        resp.raise_for_status = MagicMock()
        get.return_value = resp
        out = fetch_url("https://x.ru/a")
        self.assertEqual(out["final_url"], "https://x.ru/final")
        self.assertEqual(out["title"], "Page")
        self.assertNotIn("junk", out["text"])
        self.assertNotIn("menu", out["text"])
        self.assertLessEqual(len(out["text"]), MAX_PAGE_CHARS + len(" …[truncated]"))
        self.assertTrue(out["text"].endswith("[truncated]"))

    @patch("src.web_tools.requests.get")
    def test_failure_returns_error_object(self, get):
        get.side_effect = ConnectionError("boom")
        out = fetch_url("https://bo.nalog.ru/x")
        self.assertEqual(out["url"], "https://bo.nalog.ru/x")
        self.assertIn("ConnectionError", out["error"])
        self.assertNotIn("text", out)


if __name__ == "__main__":
    unittest.main()
