"""telemetry_summary — per-stage aggregation of the run's events.jsonl.
Run with:  python -m unittest tests.test_telemetry"""
import json
import tempfile
import unittest
from pathlib import Path

from src import runs


class TestTelemetrySummary(unittest.TestCase):
    def _write(self, events):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rd = Path(tmp.name)
        with (rd / "events.jsonl").open("w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return rd

    def test_stages_failures_resumes_and_gate_trajectory(self):
        rd = self._write([
            {"event": "api_discovery", "seconds": 40, "tokens_in": 9000,
             "tokens_out": 800, "searches": 4, "fetches": 7, "requests": 12},
            {"event": "api_company", "brand": "А", "seconds": 200,
             "tokens_in": 40000, "tokens_out": 3000, "searches": 12,
             "fetches": 9, "budget_rounds": 1, "grounding_affected": 2},
            {"event": "api_company_failed", "brand": "Б", "error": "boom"},
            {"event": "api_company", "brand": "Б", "seconds": 90,
             "resumed": "A", "searches": 5, "fetches": 4},
            {"event": "api_repair", "brand": "А", "seconds": 60,
             "search_denied": 3, "fetches": 2, "tokens_in": 15000,
             "tokens_out": 900},
            {"event": "api_repair_failed", "brand": "Б", "error": "stall"},
            {"event": "autofixed", "companies": 2, "fields": 5},
            {"event": "salvaged", "companies": 1, "fields": 3},
            {"event": "gate", "accepted": 10, "rejected": 4,
             "codes": {"unsourced": 3, "b-copy": 1}},
            {"event": "gate", "accepted": 13, "rejected": 1,
             "codes": {"unsourced": 1}},
        ])
        out = runs.telemetry_summary(rd)
        self.assertIn("discovery passes=1 failed=0 resumed=0 time=40s "
                      "tools=0 tokens=9000+800 search=4 (denied 0) fetch=7", out)
        self.assertIn("research  passes=2 failed=1 resumed=1 time=290s "
                      "tools=0 tokens=40000+3000 search=17 (denied 0) fetch=13 "
                      "budget_hits=1 grounding_stripped=2", out)
        self.assertIn("repair    passes=1 failed=1", out)
        self.assertIn("search=0 (denied 3)", out)
        self.assertIn("autofixed_fields=5 salvaged_fields=3", out)
        self.assertIn("10✓/4✗ → 13✓/1✗", out)
        self.assertIn("unsourced×1", out)

    def test_no_events_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertIn("nothing recorded", runs.telemetry_summary(Path(tmp.name)))


if __name__ == "__main__":
    unittest.main()
