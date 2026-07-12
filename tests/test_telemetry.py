"""telemetry_summary — deterministic markdown run summary from events.jsonl.
Run with:  python -m unittest tests.test_telemetry"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import runs

EVENTS = [
    {"event": "api_discovery", "seconds": 40, "tokens_in": 9000,
     "tokens_out": 800, "searches": 4, "fetches": 7, "requests": 12},
    {"event": "api_company", "brand": "А", "seconds": 200,
     "tokens_in": 40000, "tokens_out": 3000, "searches": 12,
     "fetches": 9, "budget_rounds": 1, "grounding_affected": 2},
    {"event": "api_company_failed", "brand": "Б", "error": "boom"},
    {"event": "api_company", "brand": "Б", "seconds": 90,
     "resumed": "A", "searches": 5, "fetches": 4},
    {"event": "api_repair", "brand": "А", "seconds": 60,
     "search_denied": 3, "fetches": 2, "tokens_in": 15000, "tokens_out": 900},
    {"event": "api_repair_failed", "brand": "Б", "error": "stall"},
    {"event": "autofixed", "companies": 2, "fields": 5},
    {"event": "salvaged", "companies": 1, "fields": 3},
    {"event": "gate", "accepted": 10, "rejected": 4,
     "codes": {"unsourced": 3, "b-copy": 1}},
    {"event": "gate", "accepted": 13, "rejected": 1, "codes": {"unsourced": 1}},
]


class TestTelemetrySummary(unittest.TestCase):
    def _write(self, events):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rd = Path(tmp.name)
        with (rd / "events.jsonl").open("w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return rd

    def test_stage_table_totals_yield_and_gate(self):
        out = runs.telemetry_summary(self._write(EVENTS))
        self.assertIn("# Run summary", out)
        self.assertIn("| discovery | 1 | 0 | 0 | 40s | 0 | 9000+800 | 4 | 0 | 7 | 0 | 0 |", out)
        self.assertIn("| research | 2 | 1 | 1 | 290s | 0 | 40000+3000 | 17 | 0 | 13 | 1 | 2 |", out)
        self.assertIn("| repair | 1 | 1 | 0 | 60s | 0 | 15000+900 | 0 | 3 | 2 | 0 | 0 |", out)
        self.assertIn("tokens 64000+4700", out)
        self.assertIn("failures 2", out)
        self.assertIn("resumes 1", out)
        # source yield derivable (split recorded): 22 fetches / 21 searches
        self.assertIn("22 pages opened over 21 searches (1.0 per search)", out)
        self.assertIn("**Research retries**: 1 companies failed at least once; "
                      "1 recovered on retry", out)
        self.assertIn("**Repairs**: 1 passes over 1 companies (most: А ×1)", out)
        self.assertIn("autofixed fields 5 · salvaged fields 3", out)
        self.assertIn("trajectory: 10✓/4✗ → 13✓/1✗", out)
        self.assertIn("last reject codes: unsourced×1", out)

    def test_missing_historical_metrics_read_na_not_zero(self):
        rd = self._write([{"event": "api_company", "brand": "А",
                           "tool_calls": 50}])   # pre-telemetry shape
        out = runs.telemetry_summary(rd)
        self.assertIn("| research | 1 | 0 | 0 | n/a | 50 | n/a | n/a | n/a | n/a | n/a | 0 |", out)
        self.assertNotIn("0+0", out)
        self.assertIn("Data gaps", out)
        self.assertIn("research: tokens, timings, search/fetch split", out)
        self.assertNotIn("Source yield", out)     # split absent → not derivable

    def test_cost_only_when_pricing_configured(self):
        rd = self._write(EVENTS)
        with patch.dict(os.environ, {"TOKEN_PRICE_IN": "", "TOKEN_PRICE_OUT": ""}):
            self.assertNotIn("Estimated cost", runs.telemetry_summary(rd))
        with patch.dict(os.environ, {"TOKEN_PRICE_IN": "0.27",
                                     "TOKEN_PRICE_OUT": "1.10"}):
            out = runs.telemetry_summary(rd)
            self.assertIn("Estimated cost", out)
            self.assertIn("$0.02", out)   # 64000/1e6*0.27 + 4700/1e6*1.10
        # pricing set but usage unrecorded → no cost line (never guessed)
        rd2 = self._write([{"event": "api_company", "brand": "А", "tool_calls": 5}])
        with patch.dict(os.environ, {"TOKEN_PRICE_IN": "0.27",
                                     "TOKEN_PRICE_OUT": "1.10"}):
            self.assertNotIn("Estimated cost", runs.telemetry_summary(rd2))

    def test_failure_event_error_has_urls_masked(self):
        from src.api_runner import _err_for_event
        e = RuntimeError("402 Client Error for url: https://api.search.brave.com"
                         "/res/v1/web/search?q=секретный+запрос — quota")
        s = _err_for_event(e)
        self.assertNotIn("http", s)
        self.assertNotIn("секретный", s)
        self.assertIn("‹url›", s)
        self.assertIn("RuntimeError", s)

    def test_no_events_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertIn("nothing recorded", runs.telemetry_summary(Path(tmp.name)))


if __name__ == "__main__":
    unittest.main()
