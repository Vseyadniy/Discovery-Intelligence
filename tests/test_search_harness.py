"""Search-only comparison harness: fixture validation, dry-run (no API),
provider selection with no fallback, max-queries cap, output schemas, paired
metrics, offline re-analysis, secret-free outputs, optional fetch-check, and
the guarantee that NO model is ever invoked.
Run with: python -m unittest tests.test_search_harness"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import search_harness as sh
from src import web_tools as wt
from src.web_tools import SearchTelemetry


def _tel(provider, outcome="ok", results=0, cat="", status=200):
    return SearchTelemetry(provider, outcome, 12, results=results,
                           error_category=cat, http_status=status)


def _fixture(tmp, data, suffix=".json"):
    p = Path(tmp) / f"fx{suffix}"
    if suffix == ".json":
        p.write_text(json.dumps(data), encoding="utf-8")
    else:
        import yaml
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


VALID = {"queries": [
    {"id": "q1", "category": "registry", "query": "СберКорус ИНН",
     "expected_domains": ["rusprofile.ru"], "preferred_domain": "rusprofile.ru"},
    {"id": "q2", "category": "product", "query": "ELMA365",
     "expected_domains": ["elma365.com"]},
]}


class TestFixtureValidation(unittest.TestCase):
    def test_valid_json_and_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            for suf in (".json", ".yaml"):
                qs = sh.load_fixture(_fixture(td, VALID, suf))
                self.assertEqual([q.id for q in qs], ["q1", "q2"])
                self.assertEqual(qs[0].expected_domains, ["rusprofile.ru"])
                self.assertEqual(qs[0].preferred_domain, "rusprofile.ru")

    def test_bare_list_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            qs = sh.load_fixture(_fixture(td, VALID["queries"]))
            self.assertEqual(len(qs), 2)

    def test_domains_normalised(self):
        data = {"queries": [{"id": "q", "query": "x",
                             "expected_domains": ["HTTPS://WWW.Rusprofile.RU/x"]}]}
        with tempfile.TemporaryDirectory() as td:
            q = sh.load_fixture(_fixture(td, data))[0]
        self.assertEqual(q.expected_domains, ["rusprofile.ru"])

    def test_errors(self):
        bad = [
            ({"queries": []}, "no queries"),
            ({"queries": [{"query": "x"}]}, "missing `id`"),
            ({"queries": [{"id": "a"}]}, "missing `query`"),
            ({"queries": [{"id": "a", "query": "x"},
                          {"id": "a", "query": "y"}]}, "duplicate"),
            ({"queries": [{"id": "a", "query": "x",
                           "expected_domains": "nope"}]}, "must be a list"),
            ({"nope": 1}, "list of queries"),
        ]
        with tempfile.TemporaryDirectory() as td:
            for data, frag in bad:
                with self.assertRaises(sh.SearchHarnessError) as cm:
                    sh.load_fixture(_fixture(td, data))
                self.assertIn(frag, str(cm.exception))

    def test_missing_file(self):
        with self.assertRaises(sh.SearchHarnessError):
            sh.load_fixture("/no/such/fixture.json")


class TestDryRunNoApi(unittest.TestCase):
    def test_dry_run_makes_no_calls_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, VALID)
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry",
                              side_effect=AssertionError("no API in dry-run")):
                res = sh.run_harness(fx, provider="both", out_dir=out,
                                     dry_run=True, log=lambda *a: None)
            self.assertTrue(res["dry_run"])
            self.assertEqual(res["plan"]["providers"], ["brave", "tavily"])
            self.assertEqual(res["plan"]["n_queries"], 2)
            self.assertFalse(out.exists())               # nothing written


class TestRunAndSchemas(unittest.TestCase):
    def _search(self, query, count=8, provider=None):
        # brave: q1 hits rusprofile at rank1, q2 misses; tavily: mirror-ish
        table = {
            ("brave", "СберКорус ИНН"): [
                {"title": "t", "url": "https://rusprofile.ru/id/1", "snippet": "s"},
                {"title": "t", "url": "https://other.ru/x", "snippet": "s"}],
            ("brave", "ELMA365"): [
                {"title": "t", "url": "https://wiki.ru/e", "snippet": "s"}],
            ("tavily", "СберКорус ИНН"): [
                {"title": "t", "url": "https://list-org.com/x", "snippet": "s"},
                {"title": "t", "url": "https://rusprofile.ru/id/1", "snippet": "s"}],
            ("tavily", "ELMA365"): [
                {"title": "t", "url": "https://elma365.com/csp", "snippet": "s"}],
        }
        results = table.get((provider, query), [])
        outcome = "ok" if results else "empty"
        return results, _tel(provider, outcome, results=len(results))

    def test_both_providers_run_paired_and_schema(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, VALID)
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry", side_effect=self._search):
                res = sh.run_harness(fx, provider="both", out_dir=out,
                                     log=lambda *a: None)
            self.assertEqual(res["n_records"], 4)        # 2 queries × 2 providers
            results_doc = json.loads((out / "results.json").read_text("utf-8"))
            metrics = json.loads((out / "metrics.json").read_text("utf-8"))
            self.assertTrue((out / "report.md").exists())

            # per-provider schema + hit metrics
            bp = metrics["by_provider"]
            self.assertEqual(set(bp), {"brave", "tavily"})
            self.assertEqual(bp["brave"]["hit1_rate"], 0.5)   # q1 hits, q2 misses
            self.assertEqual(bp["tavily"]["hit3_rate"], 1.0)  # both hit within 3
            self.assertEqual(bp["tavily"]["hit1_rate"], 0.5)  # elma rank1, sber rank2
            for m in bp.values():
                for k in ("success_rate", "empty_rate", "error_rate",
                          "latency_p50_ms", "latency_p95_ms", "unique_domains",
                          "duplicate_urls", "error_categories"):
                    self.assertIn(k, m)

            # paired by id
            ps = metrics["paired_summary"]
            self.assertEqual(ps["n_paired"], 2)
            self.assertEqual({p["id"] for p in metrics["paired"]}, {"q1", "q2"})

    def test_single_provider_no_pairing(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, VALID)
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry", side_effect=self._search):
                sh.run_harness(fx, provider="brave", out_dir=out, log=lambda *a: None)
            metrics = json.loads((out / "metrics.json").read_text("utf-8"))
            self.assertEqual(set(metrics["by_provider"]), {"brave"})
            self.assertEqual(metrics["paired"], [])
            self.assertEqual(metrics["paired_summary"], {})

    def test_max_queries_cap(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, VALID)
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry", side_effect=self._search):
                res = sh.run_harness(fx, provider="brave", out_dir=out,
                                     max_queries=1, log=lambda *a: None)
            self.assertEqual(res["n_records"], 1)

    def test_never_falls_back_between_providers(self):
        # tavily errors on q1; brave must NOT be substituted, error recorded
        def search(query, count=8, provider=None):
            if provider == "tavily":
                return [], _tel("tavily", "error", cat="auth", status=401)
            return [{"title": "t", "url": "https://rusprofile.ru/1", "snippet": "s"}], \
                _tel("brave", "ok", results=1)
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, {"queries": [VALID["queries"][0]]})
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry", side_effect=search):
                sh.run_harness(fx, provider="both", out_dir=out, log=lambda *a: None)
            metrics = json.loads((out / "metrics.json").read_text("utf-8"))
            self.assertEqual(metrics["by_provider"]["tavily"]["error"], 1)
            self.assertEqual(metrics["by_provider"]["tavily"]["error_categories"],
                             {"auth": 1})
            self.assertEqual(metrics["by_provider"]["brave"]["success"], 1)


class TestNoModelCalls(unittest.TestCase):
    def test_harness_never_imports_or_calls_a_model(self):
        import sys
        # model_router must not even be imported as a side effect of a run
        sys.modules.pop("src.model_router", None)
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, VALID)
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry",
                              side_effect=lambda q, c=8, provider=None:
                              ([], _tel(provider, "empty"))):
                sh.run_harness(fx, provider="both", out_dir=out, log=lambda *a: None)
            self.assertNotIn("src.model_router", sys.modules)


class TestFetchCheck(unittest.TestCase):
    def test_fetch_check_off_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, {"queries": [VALID["queries"][0]]})
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry",
                              return_value=([{"title": "t", "url": "https://rusprofile.ru/1",
                                              "snippet": "s"}], _tel("brave", "ok", 1))), \
                 patch.object(wt, "fetch_url",
                              side_effect=AssertionError("must not fetch")):
                sh.run_harness(fx, provider="brave", out_dir=out, log=lambda *a: None)
            rec = json.loads((out / "results.json").read_text("utf-8"))["records"][0]
            self.assertNotIn("fetch", rec)

    def test_fetch_check_uses_fetch_url_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture(td, {"queries": [VALID["queries"][0]]})
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry",
                              return_value=([{"title": "t", "url": "https://rusprofile.ru/1",
                                              "snippet": "s"}], _tel("brave", "ok", 1))), \
                 patch.object(wt, "fetch_url",
                              return_value={"url": "u", "text": "body here"}) as fu:
                sh.run_harness(fx, provider="brave", out_dir=out,
                               fetch_check=True, log=lambda *a: None)
            fu.assert_called_once()
            rec = json.loads((out / "results.json").read_text("utf-8"))["records"][0]
            self.assertTrue(rec["fetch"]["ok"])


class TestOfflineReanalysis(unittest.TestCase):
    def test_analyze_saved_recomputes_without_api(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run"
            out.mkdir()
            records = [
                {"id": "q1", "category": "reg", "provider": "brave",
                 "telemetry": _tel("brave", "ok", 2).public_dict(), "n_results": 2,
                 "hit1": True, "hit3": True, "hit5": True,
                 "pref_hit1": True, "pref_hit3": True, "pref_hit5": True,
                 "unique_domains": ["rusprofile.ru", "x.ru"],
                 "duplicate_urls": 0, "duplicate_domains": 0, "results": []},
                {"id": "q1", "category": "reg", "provider": "tavily",
                 "telemetry": _tel("tavily", "empty", 0).public_dict(), "n_results": 0,
                 "hit1": False, "hit3": False, "hit5": False,
                 "pref_hit1": False, "pref_hit3": False, "pref_hit5": False,
                 "unique_domains": [], "duplicate_urls": 0,
                 "duplicate_domains": 0, "results": []},
            ]
            (out / "results.json").write_text(
                json.dumps({"meta": {"providers": ["brave", "tavily"]},
                            "records": records}), encoding="utf-8")
            with patch.object(wt, "search_with_telemetry",
                              side_effect=AssertionError("no API in analyze")):
                metrics = sh.analyze_saved(out, log=lambda *a: None)
            self.assertEqual(metrics["by_provider"]["brave"]["hit3_rate"], 1.0)
            self.assertEqual(metrics["by_provider"]["tavily"]["empty"], 1)
            self.assertEqual(metrics["paired_summary"]["n_paired"], 1)
            self.assertTrue((out / "metrics.json").exists())
            self.assertTrue((out / "report.md").exists())

    def test_analyze_missing_results(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(sh.SearchHarnessError):
                sh.analyze_saved(td)


class TestOutputsSecretFree(unittest.TestCase):
    def test_no_secret_in_any_output(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"SEARCH_API_KEY": "sekret-XYZ",
                                     "TAVILY_API_KEY": "tvly-SECRET"}):
            fx = _fixture(td, {"queries": [VALID["queries"][0]]})
            out = Path(td) / "out"
            with patch.object(wt, "search_with_telemetry",
                              return_value=([{"title": "t", "url": "https://rusprofile.ru/1",
                                              "snippet": "s"}], _tel("brave", "ok", 1))):
                sh.run_harness(fx, provider="brave", out_dir=out, log=lambda *a: None)
            blob = (out / "results.json").read_text("utf-8") + \
                   (out / "metrics.json").read_text("utf-8") + \
                   (out / "report.md").read_text("utf-8")
            self.assertNotIn("sekret-XYZ", blob)
            self.assertNotIn("tvly-SECRET", blob)


class TestEnvLoader(unittest.TestCase):
    """Regression (2026-07-27 smoke): the standalone harness CLI must load .env
    so it sees the search keys the app saved — without importing a model."""

    def test_loads_keys_with_setdefault_semantics(self):
        import sys
        with tempfile.TemporaryDirectory() as td:
            envf = Path(td) / ".env"
            envf.write_text("TAVILY_API_KEY=tvly-fromfile\n"
                            "SEARCH_PROVIDER=tavily\n"
                            "# comment\n\nBAD LINE NO EQUALS\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TAVILY_API_KEY", None)
                os.environ["SEARCH_PROVIDER"] = "brave"   # already-set wins
                sys.modules.pop("src.model_router", None)
                sh.load_env_file(envf)
                self.assertEqual(os.environ["TAVILY_API_KEY"], "tvly-fromfile")
                self.assertEqual(os.environ["SEARCH_PROVIDER"], "brave")  # setdefault
                # model-free contract preserved
                self.assertNotIn("src.model_router", sys.modules)

    def test_missing_env_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            sh.load_env_file(Path(td) / "nope.env")   # must not raise


class TestPercentile(unittest.TestCase):
    def test_percentile_math(self):
        self.assertEqual(sh._percentile([], 50), 0.0)
        self.assertEqual(sh._percentile([10], 95), 10.0)
        self.assertEqual(sh._percentile([10, 20], 50), 15.0)
        vals = list(range(1, 101))
        self.assertEqual(sh._percentile(vals, 50), 50.5)
        self.assertAlmostEqual(sh._percentile(vals, 95), 95.05, places=1)


if __name__ == "__main__":
    unittest.main()
