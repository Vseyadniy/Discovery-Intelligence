"""DeepSeek company-level concurrency + jittered backoff on correlated
failures. Run with: python -m unittest tests.test_concurrency"""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_runner
from src import model_router as mr

BRANDS = ["Альфа", "Бета"]


class _Log:
    def __init__(self, tokens):
        self.tool_calls = 5
        self.stats = {"tokens_in": tokens, "tokens_out": 10, "searches": 1,
                      "fetches": 1, "search_denied": 0, "budget_rounds": 0,
                      "requests": 2, "early_stop": 0, "extended": 0,
                      "dup_queries": 0, "cache_hits": 0}

    def check_grounding(self, obj, only_fields=None):
        return []


class TestConcurrencyConfig(unittest.TestCase):
    def test_default_and_clamps(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DS_COMPANY_CONCURRENCY", None)
            self.assertEqual(mr.company_concurrency(), 1)     # conservative
        with patch.dict(os.environ, {"DS_COMPANY_CONCURRENCY": "3"}):
            self.assertEqual(mr.company_concurrency(), 3)
        with patch.dict(os.environ, {"DS_COMPANY_CONCURRENCY": "99"}):
            self.assertEqual(mr.company_concurrency(), 4)     # clamp high
        with patch.dict(os.environ, {"DS_COMPANY_CONCURRENCY": "junk"}):
            self.assertEqual(mr.company_concurrency(), 1)


class _Harness(unittest.TestCase):
    def _run(self, rd, collect_fn, conc="2"):
        def verify(system, user, escalate, max_tokens=12000, on_event=None, **kw):
            return '{"fields": {"merged": {"value": 1}}}', "eng"

        with patch.dict(os.environ, {"DS_COMPANY_CONCURRENCY": conc}), \
             patch.object(api_runner.runs, "_load_meta",
                          return_value={"market": "m", "output_language": "Russian"}), \
             patch.object(api_runner.runs, "manifest", return_value=(BRANDS, "", [])), \
             patch.object(api_runner.runs, "load_schema", return_value={}), \
             patch.object(api_runner, "load_config", return_value=({}, {}, [])), \
             patch.object(api_runner.runs, "_pending_brands", return_value=BRANDS), \
             patch.object(api_runner, "_collector_prompt",
                          side_effect=lambda letter, brand, *a: f"{letter}|{brand}"), \
             patch.object(api_runner, "_verifier_prompt", return_value="V"), \
             patch.object(mr, "collect", side_effect=collect_fn), \
             patch.object(mr, "verify", side_effect=verify), \
             patch.object(mr, "MODE", "deepseek"), \
             patch("time.sleep") as sleep:
            summary = api_runner.run_next_step(rd, batch=2, log=lambda *_: None)
        return summary, sleep

    def _events(self, rd, name):
        return [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines()
                if json.loads(l)["event"] == name]


class TestConcurrentIsolation(_Harness):
    def test_two_companies_parallel_with_per_thread_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            tokens = {"Альфа": 1000, "Бета": 2000}
            seen_threads = set()

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                letter, brand = user.split("|")
                seen_threads.add(threading.get_ident())
                mr._set_source_log(_Log(tokens[brand]))   # what the real loop does
                return '{"fields": {"f": {"value": "v", "source": "https://s.ru/p"}}}', "eng"

            summary, _sleep = self._run(rd, collect)
            self.assertIn("researched 2/2", summary)
            self.assertEqual(len(seen_threads), 2)        # genuinely concurrent
            evs = {e["brand"]: e for e in self._events(rd, "api_company")}
            for b in BRANDS:
                # own-thread attribution: A+B passes sum to 2× the brand's fake
                # tokens; a cross-thread leak would produce a MIXED sum instead
                self.assertEqual(evs[b]["tokens_in"], tokens[b] * 2, b)
                self.assertEqual(evs[b]["concurrency"], 2)
            for b in BRANDS:                              # all artifacts saved
                stem = api_runner.runs._slug(b)
                self.assertTrue((rd / "agent_runs" / f"{stem}_record.json").exists())


class TestCorrelatedBackoff(_Harness):
    def test_backoff_then_retry_only_affected_from_saved_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            b_attempts = {b: 0 for b in BRANDS}
            calls = []

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                letter, brand = user.split("|")
                calls.append(user)
                if letter == "b":
                    b_attempts[brand] += 1
                    if b_attempts[brand] == 1:            # correlated first-hit
                        raise ConnectionError("connection reset by peer")
                return '{"fields": {"f": {"value": "v", "source": "https://s.ru/p"}}}', "eng"

            summary, sleep = self._run(rd, collect)
            self.assertIn("researched 2/2", summary)      # both recovered
            # jittered backoff happened exactly once, in the 15–30s band
            sleep.assert_called_once()
            self.assertTrue(15 <= sleep.call_args.args[0] <= 30)
            bo = self._events(rd, "backoff")
            self.assertEqual(len(bo), 1)
            self.assertEqual(bo[0]["companies"], 2)
            self.assertEqual(bo[0]["retry_concurrency"], 1)
            self.assertEqual(bo[0]["reason"], "correlated transient failures")
            # retry resumed from saved artifacts: collector A ran ONCE per
            # company, only B was redone
            self.assertEqual(len([c for c in calls if c.startswith("a|")]), 2)
            self.assertEqual(len([c for c in calls if c.startswith("b|")]), 4)
            self.assertEqual(len(self._events(rd, "api_company_failed")), 2)
            self.assertEqual(len(self._events(rd, "api_company")), 2)

    def test_single_transient_failure_no_backoff(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            (rd / "agent_runs").mkdir()
            failed_once = []

            def collect(system, user, max_tokens=16000, on_event=None, **kw):
                letter, brand = user.split("|")
                if brand == "Бета" and letter == "a" and not failed_once:
                    failed_once.append(1)
                    raise ConnectionError("reset")
                return '{"fields": {"f": {"value": "v", "source": "https://s.ru/p"}}}', "eng"

            summary, sleep = self._run(rd, collect)
            sleep.assert_not_called()                     # 1 failure ≠ correlated
            self.assertEqual(self._events(rd, "backoff"), [])
            self.assertIn("researched 1/2", summary)      # normal retry-later flow


if __name__ == "__main__":
    unittest.main()
