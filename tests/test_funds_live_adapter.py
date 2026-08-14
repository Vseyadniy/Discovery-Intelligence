"""Offline tests for the production ResearchPass adapter (funds_intelligence.live_pass).

Every test runs with the socket layer HARD-BLOCKED: `socket.socket` is replaced
for the whole module, so a genuine network call raises instead of leaking. The
production stack is exercised through mocks of `model_router.collect` only —
its browsing/tool loop is never re-implemented here.

Covers: prompt/result passthrough, SourceLog→Grounding mapping (incl. URL
normalisation), usage telemetry, failure classification + spend attribution,
provider pinning, the paid-work approval gate, resume-does-not-repeat-work,
and the zero-call Preview.
"""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_core import RunStore, ResearchPass, ResearchPassResult, ScriptedResearchPass

import funds_intelligence as fi
from funds_intelligence import FundsController, FundsMandate
from funds_intelligence import fixtures as fx
from funds_intelligence.live_pass import (
    LiveResearchPass, SourceLogGrounding, PassUsage, ProviderMismatch, preflight,
)
from funds_intelligence.preview import (
    LivePlan, build_preview, render_preview, european_pe_plan, EUROPEAN_PE_MANDATE,
)
from src import web_tools as wt


# ── hard network block for this whole module ─────────────────────────────────
class _BlockedSocket(socket.socket):
    def __init__(self, *a, **k):
        raise AssertionError("network access attempted in an offline test")


def setUpModule():
    global _real_socket, _real_create
    _real_socket, _real_create = socket.socket, socket.create_connection
    socket.socket = _BlockedSocket

    def _blocked(*a, **k):
        raise AssertionError("network access attempted in an offline test")
    socket.create_connection = _blocked


def tearDownModule():
    socket.socket = _real_socket
    socket.create_connection = _real_create


def _fake_log(pages=None, seen_extra=(), stats=None, tool_calls=0):
    """A real production SourceLog populated the way the tool loop would."""
    log = wt.SourceLog()
    for url, text in (pages or {}).items():
        log.log_fetch(url, {"text": text, "final_url": url})
    if seen_extra:
        log.log_search([{"url": u} for u in seen_extra])
    log.stats.update(stats or {})
    log.tool_calls = tool_calls
    return log


class TestNetworkIsBlocked(unittest.TestCase):
    def test_socket_is_blocked_in_this_module(self):
        with self.assertRaises(AssertionError):
            socket.socket()
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.com", 80))


# ── grounding adapter ────────────────────────────────────────────────────────
class TestSourceLogGrounding(unittest.TestCase):
    def test_maps_seen_and_fetched_to_the_core_protocol(self):
        log = _fake_log(pages={"https://reg.example/alpha": "Fund III closed at $250m"},
                        seen_extra=["https://press.example/x"])
        g = SourceLogGrounding(log)
        from research_core import Grounding
        self.assertIsInstance(g, Grounding)
        self.assertTrue(g.has_source("https://reg.example/alpha"))
        self.assertTrue(g.has_source("https://press.example/x"))     # search-seen
        self.assertFalse(g.has_source("https://never.seen/page"))
        self.assertTrue(g.supports_value("$250m"))
        self.assertFalse(g.supports_value("$9bn"))                   # fabricated

    def test_url_matching_uses_production_normalisation(self):
        log = _fake_log(pages={"https://reg.example/alpha": "text"})
        g = SourceLogGrounding(log)
        # scheme/www/trailing slash/query differences must still match
        self.assertTrue(g.has_source("http://www.reg.example/alpha/"))
        self.assertTrue(g.has_source("https://reg.example/alpha?utm=1"))

    def test_empty_log_is_safe(self):
        g = SourceLogGrounding(None)
        self.assertFalse(g.has_source("https://x"))
        self.assertFalse(g.supports_value("anything"))
        self.assertEqual(g.stats, {})

    def test_write_through_records_into_the_production_log(self):
        log = wt.SourceLog()
        g = SourceLogGrounding(log)
        g.fetched("https://a.example/p", "hello world")
        g.seen("https://b.example/q")
        self.assertTrue(g.has_source("https://a.example/p"))
        self.assertTrue(g.has_source("https://b.example/q"))
        self.assertTrue(g.supports_value("hello"))


# ── the adapter itself ───────────────────────────────────────────────────────
class TestLiveResearchPass(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"SEARCH_PROVIDER": "tavily"})
        self.env.start()
        self.mode = patch("src.model_router.MODE", "deepseek")
        self.mode.start()

    def tearDown(self):
        self.mode.stop()
        self.env.stop()

    def test_implements_the_core_contract(self):
        self.assertIsInstance(LiveResearchPass(), ResearchPass)
        self.assertTrue(LiveResearchPass.costs_money)

    def test_prompt_passthrough_and_result_mapping(self):
        log = _fake_log(pages={"https://reg.example/a": "Alpha Capital AUM $780m"},
                        stats={"tokens_in": 1200, "tokens_out": 340,
                               "searches": 3, "fetches": 2},
                        tool_calls=5)
        seen = {}

        def fake_collect(system, user, **kw):
            seen.update(system=system, user=user, **kw)
            return '{"management_company": {"name": "Alpha"}}', "deepseek-chat+tools"

        with patch("src.model_router.collect", side_effect=fake_collect), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log") as reset:
            rp = LiveResearchPass(budget=18, max_tokens=16000)
            res = rp.run_pass("SYSTEM PROMPT", "USER PROMPT")

        reset.assert_called_once()                    # never inherit stale grounding
        self.assertEqual(seen["system"], "SYSTEM PROMPT")
        self.assertEqual(seen["user"], "USER PROMPT")
        self.assertEqual(seen["budget"], 18)          # budget reaches the prod loop
        self.assertFalse(seen["allow_extend"])        # no earned extension on run #1
        self.assertEqual(seen["max_tokens"], 16000)

        self.assertIsInstance(res, ResearchPassResult)
        self.assertEqual(json.loads(res.text)["management_company"]["name"], "Alpha")
        self.assertEqual(res.engine, "deepseek-chat+tools")
        self.assertEqual(res.tokens, 1540)
        self.assertTrue(res.grounding.has_source("https://reg.example/a"))

    def test_per_call_budget_override(self):
        seen = {}

        def fake_collect(system, user, **kw):
            seen.update(kw)
            return "{}", "eng"

        with patch("src.model_router.collect", side_effect=fake_collect), \
             patch("src.model_router.get_source_log", return_value=_fake_log()), \
             patch("src.model_router.reset_source_log"):
            LiveResearchPass(budget=18).run_pass("s", "u", budget=5)
        self.assertEqual(seen["budget"], 5)

    def test_usage_telemetry_is_harvested_not_estimated(self):
        log = _fake_log(pages={"https://a/1": "x", "https://a/2": "y"},
                        seen_extra=["https://b/1"],
                        stats={"tokens_in": 900, "tokens_out": 100, "searches": 4,
                               "fetches": 2, "search_rate_limited": 1,
                               "search_errors": 2, "search_denied": 3},
                        tool_calls=7)
        with patch("src.model_router.collect", return_value=("{}", "eng")), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log"):
            rp = LiveResearchPass()
            rp.run_pass("s", "u")
        u = rp.usages[-1]
        self.assertEqual((u.tokens_in, u.tokens_out, u.tokens), (900, 100, 1000))
        self.assertEqual((u.tool_calls, u.searches, u.fetches), (7, 4, 2))
        self.assertEqual((u.search_rate_limited, u.search_errors, u.search_denied), (1, 2, 3))
        self.assertEqual(u.pages_fetched, 2)
        self.assertEqual(u.urls_seen, 3)
        self.assertIn("tokens", u.as_event())

    def test_totals_accumulate_across_passes(self):
        log = _fake_log(stats={"tokens_in": 10, "tokens_out": 5}, tool_calls=2)
        with patch("src.model_router.collect", return_value=("{}", "eng")), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log"):
            rp = LiveResearchPass()
            rp.run_pass("s", "u")
            rp.run_pass("s", "u")
        tot = rp.total_usage()
        self.assertEqual(tot.tokens, 30)
        self.assertEqual(tot.tool_calls, 4)
        self.assertEqual(rp.calls, 2)

    def test_failure_is_classified_with_the_production_taxonomy(self):
        # NB: the production taxonomy is order-dependent and first-match-wins,
        # so a message must not contain an earlier category's keyword (e.g.
        # "exceeded" belongs to `timeout`). These are realistic messages.
        cases = {"request timed out": "timeout", "search quota exhausted (402)": "quota",
                 "connection reset": "stream", "bad json": "parse",
                 "503 unavailable": "provider", "something odd": "other"}
        for msg, expected in cases.items():
            log = _fake_log(stats={"tokens_in": 50}, tool_calls=1)
            with patch("src.model_router.collect", side_effect=RuntimeError(msg)), \
                 patch("src.model_router.get_source_log", return_value=log), \
                 patch("src.model_router.reset_source_log"):
                rp = LiveResearchPass()
                with self.assertRaises(RuntimeError):
                    rp.run_pass("s", "u")
            self.assertEqual(rp.last_failure.category, expected, msg)
            self.assertEqual(rp.last_failure.spend["tool_calls"], 1)   # spend attributed

    def test_failure_message_masks_urls(self):
        with patch("src.model_router.collect",
                   side_effect=RuntimeError("failed on https://api.example/x?q=secret")), \
             patch("src.model_router.get_source_log", return_value=None), \
             patch("src.model_router.reset_source_log"):
            rp = LiveResearchPass()
            with self.assertRaises(RuntimeError):
                rp.run_pass("s", "u")
        self.assertIn("‹url›", rp.last_failure.message)
        self.assertNotIn("secret", rp.last_failure.message)

    def test_provider_pinning_blocks_wrong_providers(self):
        with patch("src.model_router.MODE", "gpt"):
            with self.assertRaises(ProviderMismatch):
                LiveResearchPass().check_providers()
        with patch.dict("os.environ", {"SEARCH_PROVIDER": "brave"}):
            with self.assertRaises(ProviderMismatch):
                LiveResearchPass().check_providers()
        # and it refuses BEFORE calling the model
        with patch("src.model_router.MODE", "gpt"), \
             patch("src.model_router.collect") as collect:
            with self.assertRaises(ProviderMismatch):
                LiveResearchPass().run_pass("s", "u")
            collect.assert_not_called()

    def test_pinning_can_be_disabled_explicitly(self):
        with patch("src.model_router.MODE", "gpt"):
            info = LiveResearchPass(enforce_providers=False).check_providers()
        self.assertEqual(info["model_mode"], "gpt")


class TestNoDuplicateOrchestration(unittest.TestCase):
    """The adapter must delegate browsing; it may not implement its own loop."""

    def test_adapter_never_calls_search_or_fetch_directly(self):
        src = (Path(fi.__file__).parent / "live_pass.py").read_text(encoding="utf-8")
        for forbidden in ("web_search(", "fetch_url(", "requests.", "tool_choice",
                          "function_call", "while True"):
            self.assertNotIn(forbidden, src,
                             f"live_pass.py must not re-implement browsing: {forbidden}")

    def test_only_collect_is_used_as_the_entry_point(self):
        src = (Path(fi.__file__).parent / "live_pass.py").read_text(encoding="utf-8")
        self.assertIn("mr.collect(", src)
        self.assertNotIn("_run_deepseek_tools", src)     # the private loop stays private


# ── controller integration: paid gate, telemetry, resume ─────────────────────
class TestPaidWorkGate(unittest.TestCase):
    def _live_stub(self, reply='{"candidates": [{"name": "Stub Capital Partners", '
                               '"kind": "management_company", '
                               '"source": "https://reg.example/stub"}]}'):
        """A stand-in that declares itself paid but performs no work.

        The reply must be a schema-valid landscape: an EMPTY candidate list is
        now refused rather than advancing the run to scope approval, so an empty
        stub would fail these tests for reasons unrelated to the paid gate."""
        class _Stub:
            costs_money = True
            usages = []
            def run_pass(self, system, user, *, budget=None):
                return ResearchPassResult(text=reply, grounding=None, engine="stub")
        return _Stub()

    def test_paid_pass_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=3))
            self.assertFalse(c.paid_work_approved(h))
            with self.assertRaises(PermissionError):
                c.run_landscape(h, self._live_stub())
            self.assertIsNotNone(h.ledger.last("paid_work_blocked"))

    def test_paid_pass_allowed_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as d:
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=3))
            c.approve_paid_work(h, approved_by="operator", note="preview reviewed")
            self.assertTrue(c.paid_work_approved(h))
            c.run_landscape(h, self._live_stub())
            self.assertTrue(h.load_meta()["paid_work_approved"])
            self.assertIsNotNone(h.ledger.last("paid_work_approved"))

    def test_approval_persists_across_a_fresh_handle(self):
        with tempfile.TemporaryDirectory() as d:
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=3))
            c.approve_paid_work(h)
            h2 = RunStore(Path(d)).open(h.run_id)
            self.assertTrue(FundsController(RunStore(Path(d))).paid_work_approved(h2))

    def test_offline_passes_are_never_gated(self):
        with tempfile.TemporaryDirectory() as d:
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))   # no approval
            self.assertEqual(h.load_meta()["status"], fi.AWAITING_SCOPE)


class TestUsageReachesEvents(unittest.TestCase):
    def test_pass_usage_is_logged_per_stage(self):
        log = _fake_log(pages={"https://reg.example/a": "Delta Capital"},
                        stats={"tokens_in": 500, "tokens_out": 120, "searches": 2},
                        tool_calls=3)
        landscape = json.dumps({"candidates": [{"name": "Delta Capital",
                                                "kind": "management_company"}]})
        with tempfile.TemporaryDirectory() as d, \
             patch("src.model_router.MODE", "deepseek"), \
             patch.dict("os.environ", {"SEARCH_PROVIDER": "tavily"}), \
             patch("src.model_router.collect", return_value=(landscape, "deepseek-chat+tools")), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log"):
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=3))
            c.approve_paid_work(h)
            c.run_landscape(h, LiveResearchPass(budget=12))

            # read INSIDE the tempdir scope — the ledger file lives in it
            ev = h.ledger.last("pass_usage")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["stage"], "landscape")
            self.assertEqual(ev["tokens"], 620)
            self.assertEqual(ev["tool_calls"], 3)
            self.assertEqual(ev["engine"], "deepseek-chat+tools")


class TestGroundingReachesFundsValidation(unittest.TestCase):
    def test_ungrounded_figure_from_the_live_adapter_is_downgraded(self):
        """A fabricated AUM next to a real URL must not survive as `confirmed`
        once the adapter's SourceLog grounding is applied."""
        log = _fake_log(pages={"https://reg.example/a":
                               "Alpha Capital Partners LLP. AUM: $780m as of 2025."})
        reply = {"management_company": {
            "name": "Alpha Capital Partners LLP",
            "aum": {"value": "$9bn", "state": "confirmed",     # not in the page
                    "source": "https://reg.example/a"}}}
        with patch("src.model_router.MODE", "deepseek"), \
             patch.dict("os.environ", {"SEARCH_PROVIDER": "tavily"}), \
             patch("src.model_router.collect",
                   return_value=(json.dumps(reply), "deepseek-chat+tools")), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log"):
            res = LiveResearchPass().run_pass("s", "u")
        g = fi.build_graph(json.loads(res.text), res.grounding)
        claim = g.by_kind(fi.MANAGEMENT_COMPANY)[0].claim("aum")
        self.assertEqual(claim.state, fi.INFERRED)
        self.assertFalse(claim.is_factual)

    def test_grounded_figure_survives_as_confirmed(self):
        log = _fake_log(pages={"https://reg.example/a": "AUM: $780m as of 2025."})
        reply = {"management_company": {
            "name": "Alpha", "aum": {"value": "$780m", "state": "confirmed",
                                     "source": "https://reg.example/a"}}}
        with patch("src.model_router.MODE", "deepseek"), \
             patch.dict("os.environ", {"SEARCH_PROVIDER": "tavily"}), \
             patch("src.model_router.collect",
                   return_value=(json.dumps(reply), "eng")), \
             patch("src.model_router.get_source_log", return_value=log), \
             patch("src.model_router.reset_source_log"):
            res = LiveResearchPass().run_pass("s", "u")
        g = fi.build_graph(json.loads(res.text), res.grounding)
        self.assertEqual(g.by_kind(fi.MANAGEMENT_COMPANY)[0].claim("aum").state, fi.CONFIRMED)


class TestResumeDoesNotRepeatPaidWork(unittest.TestCase):
    def test_completed_targets_are_not_re_researched(self):
        with tempfile.TemporaryDirectory() as d:
            c = FundsController(RunStore(Path(d)))
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, fx.LANDSCAPE_TARGETS)
            c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))

            # a resumed session with a PAID pass must find nothing left to do
            c2 = FundsController(RunStore(Path(d)))
            h2 = RunStore(Path(d)).open(h.run_id)
            self.assertEqual(c2.pending_targets(h2), [])
            calls = {"n": 0}

            class _Stub:
                costs_money = True
                usages = []
                def run_pass(self, *a, **k):
                    calls["n"] += 1
                    return ResearchPassResult(text="{}", grounding=None, engine="stub")

            c2.run_until_terminal(h2, lambda t: _Stub())
            self.assertEqual(calls["n"], 0)      # zero paid passes on resume


# ── zero-call Preview ────────────────────────────────────────────────────────
class TestPreview(unittest.TestCase):
    def test_preview_makes_no_call(self):
        with patch("src.model_router.collect") as collect, \
             patch("src.web_tools.web_search") as search, \
             patch("src.web_tools.fetch_url") as fetch:
            report = build_preview(european_pe_plan())
            render_preview(report)
        collect.assert_not_called()
        search.assert_not_called()
        fetch.assert_not_called()

    def test_exposure_math_is_derived_from_the_plan(self):
        plan = LivePlan(label="t", landscape_targets=3, research_targets=3,
                        deep_dives=1, landscape_budget=12, research_budget=18,
                        deep_dive_budget=12, max_tokens_per_pass=16000)
        self.assertEqual(plan.paid_passes, 5)               # 1 + 3 + 1
        self.assertEqual(plan.tool_call_ceiling, 12 + 3 * 18 + 12)   # 78
        self.assertEqual(plan.token_ceiling, 16000 * 5)     # 80_000
        ex = build_preview(plan)["exposure"]
        self.assertEqual(ex["paid_passes"], 5)
        self.assertEqual(ex["tool_call_ceiling"], 78)
        self.assertEqual(ex["budget_extension_per_pass"], 0)   # extension disabled

    def test_no_cost_claim_without_configured_pricing(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("TOKEN_PRICE_IN", None)
            os.environ.pop("TOKEN_PRICE_OUT", None)
            report = build_preview(european_pe_plan())
        self.assertIn("not reported", report["cost"])

    def test_blockers_are_reported_not_silently_fixed(self):
        with patch("src.model_router.MODE", "gpt"), \
             patch.dict("os.environ", {"SEARCH_PROVIDER": "brave"}):
            report = build_preview(european_pe_plan())
        self.assertFalse(report["ready_to_run"])
        joined = " ".join(report["blockers"])
        self.assertIn("AGENT_MODE=gpt", joined)
        self.assertIn("SEARCH_PROVIDER=brave", joined)

    def test_european_pe_plan_matches_the_agreed_scope(self):
        plan = european_pe_plan()
        self.assertIn("European private equity", plan.mandate_text)
        self.assertIn("buy-and-build", plan.mandate_text)
        self.assertEqual(plan.landscape_targets, 3)
        self.assertEqual(plan.research_targets, 3)
        self.assertEqual(plan.deep_dives, 1)
        self.assertFalse(plan.allow_extend)
        self.assertEqual(plan.limits.max_steps, 4)

    def test_render_includes_exposure_and_stop_conditions(self):
        text = render_preview(build_preview(european_pe_plan()))
        for token in ("MAXIMUM LIVE EXPOSURE", "paid passes", "tool-call ceiling",
                      "STOP CONDITIONS", "ARTIFACTS", "EVIDENCE TO INSPECT"):
            self.assertIn(token, text)

    def test_preflight_never_leaks_key_values(self):
        with patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-SECRET-123"}):
            cfg = preflight()
        self.assertTrue(cfg["search_key_present"])
        self.assertNotIn("SECRET", json.dumps(cfg))


class TestCliSafety(unittest.TestCase):
    """The CLI must refuse paid work unless both flags are given, and must never
    reach a provider while refusing."""

    def _run(self, argv):
        from funds_intelligence.__main__ import main
        with patch("src.model_router.collect") as collect, \
             patch("src.web_tools.web_search") as search:
            rc = main(argv)
        return rc, collect, search

    def test_preview_is_zero_call_and_reports_ready(self):
        rc, collect, search = self._run(["preview"])
        self.assertEqual(rc, 0)
        collect.assert_not_called()
        search.assert_not_called()

    def test_live_refuses_without_both_flags(self):
        for argv in (["live"], ["live", "--yes"], ["live", "--approve-paid"]):
            rc, collect, search = self._run(argv)
            self.assertEqual(rc, 1, argv)
            collect.assert_not_called()
            search.assert_not_called()

    def test_live_refuses_when_preview_reports_blockers(self):
        with patch("src.model_router.MODE", "gpt"):
            rc, collect, _ = self._run(["live", "--approve-paid", "--yes"])
        self.assertEqual(rc, 1)
        collect.assert_not_called()


class TestExtractorMatchesCompanyPrimitive(unittest.TestCase):
    """`funds_intelligence.extract_json` is a PORT of `model_router.extract_json`,
    not an import — the pack may not depend on `src/` outside `live_pass.py`.

    A port silently drifting from its original is exactly the kind of decay the
    isolation boundary invites, so pin them together: both sides see the same
    provider-shaped battery and must agree, value for value and failure for
    failure. If either implementation changes, this test fails."""

    CASES = [
        '{"candidates": []}',
        '{"a": 1, "b": {"c": [1, 2, {"d": "e"}]}}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the result:\n\n{"a": 1}\n\nHope that helps.',
        'Preamble ```json\n{"a": {"nested": {"deep": true}}}\n``` trailing text',
        '{"unicode": "€8.5bn — Verdane"}',
        '{"a": 1} {"b": 2}',                       # first object wins
        '',                                        # no object at all
        'no json here',
        '{"truncated": ',                          # unbalanced
        '{bad json}',                              # balanced but not JSON
    ]

    def test_identical_results_and_identical_failures(self):
        from src.model_router import extract_json as company_extract
        from funds_intelligence.extraction import extract_json as funds_extract
        for text in self.CASES:
            with self.subTest(text=text[:40]):
                try:
                    expected = company_extract(text)
                    err = None
                except Exception as ex:            # noqa: BLE001 — compare failures too
                    expected, err = None, type(ex)
                if err is None:
                    self.assertEqual(funds_extract(text), expected)
                else:
                    with self.assertRaises(err):
                        funds_extract(text)

    def test_the_port_is_textually_the_same_algorithm(self):
        """Cheap guard against a rewrite that happens to pass the battery."""
        import inspect
        from src import model_router
        from funds_intelligence import extraction
        def body(fn):
            src = inspect.getsource(fn)
            src = src[src.index('"""', src.index('"""') + 3) + 3:]
            return "".join(src.split())
        self.assertEqual(body(extraction.extract_json),
                         body(model_router.extract_json))


if __name__ == "__main__":
    unittest.main()
