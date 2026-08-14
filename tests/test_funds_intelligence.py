"""Offline tests for the Funds Intelligence vertical slice.

No network, no provider, no model call: every research pass is a
`ScriptedResearchPass` over the synthetic fixtures. Covers the data model, the
seven deterministic semantic rules, the landscape → scope-approval →
one-fund-per-step → repair flow, terminal states, stop/resume, the linked
deep-dive child run with evidence inheritance + parent immutability, and the
two defects fixed after the first smoke run.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_core import (
    RunStore, RunControl, RepairPatch, ScriptedResearchPass,
    COMPLETE, NEEDS_REVIEW, STOPPED_USER, STOPPED_NO_PROGRESS, STOPPED_BUDGET,
    Limits,
)

import funds_intelligence as fi
from funds_intelligence import (
    FundsController, FundsMandate, FundsPack, FundGraph, Node, Edge, Claim, Evidence,
    MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, PORTFOLIO_COMPANY, DEAL,
    CONTINUATION_VEHICLE, CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED,
    EMPLOYS, INVESTED_IN,
)
from funds_intelligence import fixtures as fx
from funds_intelligence.model import TIER_PRIMARY, TIER_WEAK, TIER_REPUTABLE


def _controller(tmp) -> tuple[FundsController, RunStore]:
    store = RunStore(Path(tmp))
    return FundsController(store), store


def _full_landscape(c, h):
    c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
    c.approve_scope(h, fx.LANDSCAPE_TARGETS)
    return c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))


# ── data model ────────────────────────────────────────────────────────────────
class TestModel(unittest.TestCase):
    def test_seven_kinds_are_distinct(self):
        self.assertEqual(len(set(fi.NODE_KINDS)), 7)
        for k in (MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, PORTFOLIO_COMPANY,
                  DEAL, CONTINUATION_VEHICLE, fi.EVIDENCE):
            self.assertIn(k, fi.NODE_KINDS)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            Node("fund", "X")           # not a valid kind

    def test_claim_states_and_factuality(self):
        self.assertEqual(set(fi.CLAIM_STATES),
                         {CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED})
        self.assertTrue(Claim("v", CONFIRMED).is_factual)
        self.assertTrue(Claim("v", PARTIALLY_CONFIRMED).is_factual)
        self.assertFalse(Claim("v", INFERRED).is_factual)
        self.assertFalse(Claim("v", UNRESOLVED).is_factual)

    def test_edge_currency_is_time_bound(self):
        self.assertTrue(Edge(EMPLOYS, "a", "b", props={"from": "2016"}).is_current)
        self.assertFalse(Edge(EMPLOYS, "a", "b", props={"from": "2016", "to": "2022"}).is_current)

    def test_graph_roundtrip_preserves_everything(self):
        g = FundGraph()
        ev = g.add_evidence(Evidence("https://x/1", TIER_PRIMARY, "text"))
        mc = g.add_node(Node(MANAGEMENT_COMPANY, "M"))
        mc.set("aum", Claim("$1bn", CONFIRMED, [ev.id]))
        v = g.add_node(Node(FUND_VEHICLE, "F I"))
        g.add_edge(Edge(fi.MANAGES, mc.id, v.id, CONFIRMED, [ev.id]))
        again = FundGraph.from_dict(json.loads(json.dumps(g.to_dict())))
        self.assertEqual(len(again.nodes), 2)
        self.assertEqual(len(again.edges), 1)
        self.assertEqual(again.nodes[mc.id].claim("aum").value, "$1bn")
        self.assertEqual(again.best_tier(again.nodes[mc.id].claim("aum")), TIER_PRIMARY)


# ── deterministic semantic rules ─────────────────────────────────────────────
class TestRules(unittest.TestCase):
    def setUp(self):
        self.reg = FundsPack().registry()

    def _run(self, g: FundGraph, ids=None):
        return self.reg.run(g.to_dict(), ids or fi.rules.ALL_RULE_IDS)

    def _grounded(self, g, node, fname, value, state=CONFIRMED, tier=TIER_PRIMARY,
                  marketing=False, note=""):
        ev = g.add_evidence(Evidence(f"https://src/{fname}", tier, value,
                                     is_marketing=marketing))
        node.set(fname, Claim(value, state, [ev.id], note))
        return ev

    def test_aum_on_vehicle_rejects(self):
        g = FundGraph()
        v = g.add_node(Node(FUND_VEHICLE, "Fund III"))
        self._grounded(g, v, "vintage", 2021)
        self._grounded(g, v, "aum", "$780m")
        self.assertIn("aum-vs-fund-size", self._run(g).codes())

    def test_fund_size_on_manager_rejects(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Alpha Management"))
        self._grounded(g, m, "fund_size", "$250m")
        self.assertIn("aum-vs-fund-size", self._run(g).codes())

    def test_vehicle_size_equal_to_aum_with_many_vehicles_rejects(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Alpha Management"))
        self._grounded(g, m, "aum", "$780m")
        for i, nm in enumerate(("Alpha Fund II", "Alpha Fund III")):
            v = g.add_node(Node(FUND_VEHICLE, nm))
            self._grounded(g, v, "vintage", 2017 + i)
            self._grounded(g, v, "fund_size", "$780m" if i else "$180m")
        self.assertIn("aum-vs-fund-size", self._run(g, ["aum-vs-fund-size"]).codes())

    def test_single_fund_manager_may_have_aum_equal_to_fund_size(self):
        """A one-vehicle manager legitimately has AUM ≈ its only fund's size."""
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Caspian Growth Advisors"))
        self._grounded(g, m, "aum", "$95m")
        v = g.add_node(Node(FUND_VEHICLE, "Caspian Growth Fund I"))
        self._grounded(g, v, "vintage", 2016)
        self._grounded(g, v, "fund_size", "$95m")
        self.assertNotIn("aum-vs-fund-size", self._run(g, ["aum-vs-fund-size"]).codes())

    def test_manager_typed_as_vehicle_rejects(self):
        g = FundGraph()
        n = g.add_node(Node(FUND_VEHICLE, "Boreas Ventures Management"))
        self._grounded(g, n, "fund_size", "EUR 120m")
        codes = self._run(g, ["manco-vs-vehicle"]).codes()
        self.assertIn("manco-vs-vehicle", codes)

    def test_vehicle_with_vintage_and_fund_name_passes(self):
        g = FundGraph()
        n = g.add_node(Node(FUND_VEHICLE, "Boreas Ventures Fund II"))
        self._grounded(g, n, "vintage", 2019)
        self._grounded(g, n, "fund_size", "EUR 120m")
        self.assertNotIn("manco-vs-vehicle", self._run(g, ["manco-vs-vehicle"]).codes())

    def test_departed_person_as_current_rejects(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Boreas"))
        p = g.add_node(Node(PERSON, "Ivan Petrov"))
        self._grounded(g, p, "role", "Managing Partner")
        self._grounded(g, p, "role_status", "current")
        g.add_edge(Edge(EMPLOYS, m.id, p.id, CONFIRMED, [],
                        props={"from": "2016", "to": "2022", "current": True}))
        self.assertIn("stale-person-role", self._run(g, ["stale-person-role"]).codes())

    def test_current_person_passes(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Boreas"))
        p = g.add_node(Node(PERSON, "Maria Lind"))
        self._grounded(g, p, "role", "Managing Partner")
        g.add_edge(Edge(EMPLOYS, m.id, p.id, CONFIRMED, [], props={"from": "2022"}))
        self.assertNotIn("stale-person-role", self._run(g, ["stale-person-role"]).codes())

    def test_portfolio_edge_without_evidence_rejects(self):
        g = FundGraph()
        v = g.add_node(Node(FUND_VEHICLE, "Fund II"))
        pc = g.add_node(Node(PORTFOLIO_COMPANY, "Helios Analytics"))
        g.add_edge(Edge(INVESTED_IN, v.id, pc.id, CONFIRMED, []))
        self.assertIn("unsupported-relationship",
                      self._run(g, ["unsupported-relationship"]).codes())

    def test_marketing_only_strategy_rejects_but_filing_backed_passes(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Caspian"))
        self._grounded(g, m, "active_strategy", "actively deploys capital",
                       tier=TIER_WEAK, marketing=True)
        self.assertIn("marketing-as-strategy", self._run(g, ["marketing-as-strategy"]).codes())

        g2 = FundGraph()
        m2 = g2.add_node(Node(MANAGEMENT_COMPANY, "Caspian"))
        self._grounded(g2, m2, "active_strategy", "deployed 4 rounds in 2025",
                       tier=TIER_PRIMARY, marketing=False)
        self.assertNotIn("marketing-as-strategy",
                         self._run(g2, ["marketing-as-strategy"]).codes())

    # ── defect 2 regression: unsupported ≠ negative ───────────────────────
    def test_absence_as_negative_rejects_negative_from_absent_evidence(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Caspian"))
        self._grounded(g, m, "successor_fund",
                       "No filings after 2021 were located in this register",
                       note="treated as proof no successor fund exists")
        self.assertIn("absence-as-negative", self._run(g, ["absence-as-negative"]).codes())

    def test_evidence_backed_negative_conclusion_is_allowed(self):
        """An explicit, sourced negative is a legitimate factual conclusion."""
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Zephyr"))
        ev = g.add_evidence(Evidence("https://reg.example/zephyr", TIER_PRIMARY,
                                     "The fund was formally dissolved on 2023-04-01."))
        m.set("status", Claim("dissolved on 2023-04-01 per the register",
                              CONFIRMED, [ev.id]))
        codes = self._run(g, ["absence-as-negative", "unsupported-claim"]).codes()
        self.assertNotIn("absence-as-negative", codes)
        self.assertNotIn("unsupported-claim", codes)

    def test_unsupported_factual_claim_is_its_own_code_not_a_negative(self):
        """Regression for defect 2: a factual claim with no evidence is
        UNSUPPORTED, not 'proof of a negative'."""
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Alpha"))
        m.set("aum", Claim("$780m", CONFIRMED, []))        # no evidence at all
        codes = self._run(g, ["absence-as-negative", "unsupported-claim"]).codes()
        self.assertIn("unsupported-claim", codes)
        self.assertNotIn("absence-as-negative", codes)

    def test_unresolved_claim_without_evidence_is_fine(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Alpha"))
        m.set("aum", Claim(None, UNRESOLVED, []))
        self.assertTrue(self._run(g, ["unsupported-claim", "absence-as-negative"]).accepted)

    def test_evidence_integrity_warns_only(self):
        g = FundGraph()
        m = g.add_node(Node(MANAGEMENT_COMPANY, "Alpha"))
        m.set("aum", Claim("$1bn", INFERRED, ["ev_missing"]))
        r = self._run(g, ["evidence-integrity"])
        self.assertIn("evidence-integrity", r.codes())
        self.assertTrue(r.accepted)          # warn, never blocks


# ── landscape + scope approval ───────────────────────────────────────────────
class TestLandscapeAndScope(unittest.TestCase):
    def test_mandate_persisted_and_landscape_stops_for_approval(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            m = FundsMandate(geography=["RU", "Baltics"], window_from=2014,
                             window_to=2025, filters={"stage": "series_a"},
                             depth="standard", target_count=5)
            h = c.create_run("RU Baltics VC", m)
            meta = h.load_meta()
            self.assertEqual(meta["pack_version"], fi.PACK_VERSION)
            mand = meta["spec"]["params"]["mandate"]
            self.assertEqual(mand["geography"], ["RU", "Baltics"])
            self.assertEqual([mand["window_from"], mand["window_to"]], [2014, 2025])
            self.assertEqual(mand["target_count"], 5)
            self.assertEqual(mand["filters"], {"stage": "series_a"})

            cands = c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            self.assertGreaterEqual(len(cands), 4)
            # STOPS for explicit approval — no per-target work yet
            self.assertEqual(h.load_meta()["status"], fi.AWAITING_SCOPE)
            self.assertEqual(c.pending_targets(h), [])
            self.assertIsNotNone(h.ledger.last("awaiting_scope_approval"))

    def test_scope_approval_is_explicit_and_partial(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            approved = c.approve_scope(h, ["Delta Capital", "Not In Landscape"])
            self.assertEqual(approved, ["Delta Capital"])       # unknown dropped
            self.assertEqual(c.pending_targets(h), ["Delta Capital"])
            ev = h.ledger.last("scope_approved")
            self.assertEqual(ev["approved"], ["Delta Capital"])
            self.assertIn("Alpha Capital Partners LLP", ev["rejected"])

    def test_vintage_window_filters_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(window_from=2020, window_to=2025,
                                               target_count=5))
            cands = c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            names = [x["name"] for x in cands]
            self.assertIn("Alpha Capital Fund III", names)      # vintage 2021
            # candidates without a vintage are never filtered out
            self.assertIn("Delta Capital", names)


# ── per-target research, semantics end-to-end ────────────────────────────────
class TestResearchFlow(unittest.TestCase):
    def test_full_flow_catches_every_trap(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("RU Baltics VC", FundsMandate(target_count=5))
            state = _full_landscape(c, h)
            self.assertEqual(state, NEEDS_REVIEW)

            accepted = {p.stem for p in (h.dir / "targets").glob("*.json")}
            rejected = {p.stem for p in (h.dir / "rejected").glob("*.json")}
            self.assertEqual(accepted, {"delta-capital"})
            self.assertEqual(rejected, {"alpha-capital-partners-llp",
                                        "boreas-ventures-management",
                                        "caspian-growth-advisors"})

            def codes(stem):
                p = json.loads((h.dir / "rejected" / f"{stem}.json").read_text(encoding="utf-8"))
                return {i["code"] for i in p["issues"] if i["severity"] == "reject"}

            self.assertIn("aum-vs-fund-size", codes("alpha-capital-partners-llp"))
            self.assertIn("stale-person-role", codes("boreas-ventures-management"))
            self.assertIn("unsupported-relationship", codes("boreas-ventures-management"))
            self.assertIn("marketing-as-strategy", codes("caspian-growth-advisors"))
            self.assertIn("absence-as-negative", codes("caspian-growth-advisors"))

    def test_one_fund_per_safe_step(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, fx.LANDSCAPE_TARGETS)
            self.assertEqual(len(c.pending_targets(h)), 4)
            c.research_one(h, ScriptedResearchPass(fx.target_script("Delta Capital")),
                           "Delta Capital")
            self.assertEqual(len(c.pending_targets(h)), 3)   # exactly one consumed

    def test_continuation_vehicle_stays_distinct_from_predecessor(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, ["Delta Capital"])
            c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))
            g = FundGraph.from_dict(json.loads(
                (h.dir / "targets" / "delta-capital.json").read_text(encoding="utf-8"))["graph"])
            self.assertEqual([n.name for n in g.by_kind(FUND_VEHICLE)], ["Delta Fund I"])
            self.assertEqual([n.name for n in g.by_kind(CONTINUATION_VEHICLE)],
                             ["Delta Continuation Vehicle I"])
            cont = g.edges_of(fi.CONTINUES)
            self.assertEqual(len(cont), 1)          # CV → predecessor, not merged
            self.assertNotEqual(cont[0].src, cont[0].dst)

    def test_ungrounded_value_is_downgraded_not_trusted(self):
        """A number claimed as fact but absent from the fetched page must not
        stay factual (grounding, independent of the semantic rules)."""
        script = [(json.dumps({"management_company": {
            "name": "Ghost Capital",
            "aum": {"value": "$9bn", "state": "confirmed",
                    "source": "https://reg.example/alpha"}}}),
            {"https://reg.example/alpha": fx.PAGES["https://reg.example/alpha"]})]
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate())
            g = fi.build_graph(json.loads(script[0][0]),
                               ScriptedResearchPass(script).run_pass("s", "u").grounding)
            # the pass never saw "$9bn" in the page → must NOT stay factual
            claim = g.by_kind(MANAGEMENT_COMPANY)[0].claim("aum")
            self.assertEqual(claim.state, INFERRED)
            self.assertFalse(claim.is_factual)


# ── scoped repair ────────────────────────────────────────────────────────────
class TestScopedRepair(unittest.TestCase):
    def test_repair_fixes_scope_and_preserves_unrelated_accepted_data(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            _full_landscape(c, h)

            rp = json.loads((h.dir / "rejected" / "alpha-capital-partners-llp.json")
                            .read_text(encoding="utf-8"))
            g = FundGraph.from_dict(rp["graph"])
            veh = g.by_kind(FUND_VEHICLE)[0]
            mgr = g.by_kind(MANAGEMENT_COMPANY)[0]
            ev_before = len(g.evidence)

            fixed = veh.to_dict()
            fixed["claims"].pop("aum")               # drop the mis-attached AUM only
            out = c.repair_target(h, "Alpha Capital Partners LLP",
                                  RepairPatch(scope={veh.id}, fields={veh.id: fixed},
                                              flags=["repaired: aum removed from vehicle"]))
            self.assertEqual(out["verdict"], "accepted")

            g2 = FundGraph.from_dict(out["graph"])
            v2 = g2.by_kind(FUND_VEHICLE)[0]
            m2 = g2.by_kind(MANAGEMENT_COMPANY)[0]
            self.assertNotIn("aum", v2.claims)                       # fixed
            self.assertEqual(v2.claim("fund_size").value, "$250m")   # preserved
            self.assertEqual(v2.claim("vintage").value, 2021)        # preserved
            self.assertEqual(m2.claim("aum").value, "$780m")         # untouched manager
            self.assertEqual(len(g2.evidence), ev_before)            # evidence preserved
            self.assertIn("repaired: aum removed from vehicle", g2.review_flags)

            # promoted out of the repair queue
            self.assertTrue((h.dir / "targets" / "alpha-capital-partners-llp.json").exists())
            self.assertFalse((h.dir / "rejected" / "alpha-capital-partners-llp.json").exists())

    def test_failed_repair_leaves_target_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            _full_landscape(c, h)
            out = c.repair_target(h, "Boreas Ventures Management",
                                  RepairPatch(scope=set(), fields={}))
            self.assertEqual(out["verdict"], "rejected")
            self.assertTrue((h.dir / "rejected" / "boreas-ventures-management.json").exists())


# ── controller: terminal states, stop/resume, no-progress ────────────────────
class TestControllerLifecycle(unittest.TestCase):
    def test_defect1_successful_first_step_is_progress(self):
        """Regression: a rejected first target must not look like no progress."""
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            # Alpha alone is REJECTED by the gate — the run must still progress
            c.approve_scope(h, ["Alpha Capital Partners LLP"])
            state = c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))
            self.assertEqual(state, NEEDS_REVIEW)
            self.assertNotEqual(state, STOPPED_NO_PROGRESS)
            self.assertEqual(c.pending_targets(h), [])       # attempted, not immortal
            self.assertEqual(c.rejected_targets(h), ["Alpha Capital Partners LLP"])

    def test_all_accepted_reaches_complete(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, ["Delta Capital"])
            self.assertEqual(
                c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t))),
                COMPLETE)

    def test_stop_is_honoured_and_resume_is_file_derived(self):
        with tempfile.TemporaryDirectory() as d:
            c, store = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, fx.LANDSCAPE_TARGETS)

            ctl = RunControl(run_dir=h.dir)
            ctl.request_stop()
            self.assertEqual(
                c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)),
                                     control=ctl),
                STOPPED_USER)
            self.assertEqual(len(c.pending_targets(h)), 4)   # nothing consumed

            # resume with a completely fresh store/controller/handle (no memory)
            ctl.clear()
            c2 = FundsController(RunStore(Path(d)))
            h2 = RunStore(Path(d)).open(h.run_id)
            self.assertEqual(len(c2.pending_targets(h2)), 4)
            self.assertEqual(
                c2.run_until_terminal(h2, lambda t: ScriptedResearchPass(fx.target_script(t))),
                NEEDS_REVIEW)

    def test_pause_between_steps(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, fx.LANDSCAPE_TARGETS)
            ctl = RunControl(run_dir=h.dir)
            ctl.request_pause()
            self.assertEqual(
                c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)),
                                     control=ctl),
                "paused")
            self.assertEqual(h.load_meta()["status"], "paused")

    def test_step_limit_stops_on_budget(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            c = FundsController(store, limits=Limits(max_steps=2))
            h = c.create_run("m", FundsMandate(target_count=5))
            c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))
            c.approve_scope(h, fx.LANDSCAPE_TARGETS)
            self.assertEqual(
                c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t))),
                STOPPED_BUDGET)

    def test_rerun_is_idempotent_no_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            _full_landscape(c, h)
            n_acc = len(list((h.dir / "targets").glob("*.json")))
            n_rej = len(list((h.dir / "rejected").glob("*.json")))
            c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))
            self.assertEqual(len(list((h.dir / "targets").glob("*.json"))), n_acc)
            self.assertEqual(len(list((h.dir / "rejected").glob("*.json"))), n_rej)

    def test_whole_flow_is_observable_in_events(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=5))
            _full_landscape(c, h)
            events = [e["event"] for e in h.ledger.read()]
            for expected in ("created", "mandate_defined", "landscape_built",
                             "awaiting_scope_approval", "scope_approved",
                             "target_researched", "auto_terminal"):
                self.assertIn(expected, events)
            researched = [e for e in h.ledger.read() if e["event"] == "target_researched"]
            self.assertEqual(len(researched), 4)
            self.assertTrue(all("verdict" in e and "codes" in e for e in researched))


# ── linked deep dive ─────────────────────────────────────────────────────────
class TestDeepDive(unittest.TestCase):
    def _parent(self, d):
        c, _ = _controller(d)
        h = c.create_run("RU Baltics VC", FundsMandate(target_count=5))
        _full_landscape(c, h)
        return c, h

    def test_child_run_is_linked_and_inherits_parent_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            c, h = self._parent(d)
            child = c.create_deep_dive(h, "Delta Capital")
            meta = child.load_meta()
            self.assertEqual(meta["parent_run_id"], h.run_id)
            self.assertEqual(meta["parent_target"], "Delta Capital")
            self.assertEqual(meta["kind"], "deep_dive")
            self.assertEqual(meta["pack_version"], fi.PACK_VERSION)

            inherited = FundGraph.from_dict(json.loads(
                (child.dir / "targets" / "delta-capital.json").read_text(encoding="utf-8"))["graph"])
            self.assertGreater(len(inherited.nodes), 0)
            self.assertGreater(len(inherited.evidence), 0)
            ev = child.ledger.last("deep_dive_created")
            self.assertEqual(ev["parent_run_id"], h.run_id)

    def test_expansion_adds_deals_people_portfolio_and_reuses_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            c, h = self._parent(d)
            child = c.create_deep_dive(h, "Delta Capital")
            before = FundGraph.from_dict(json.loads(
                (child.dir / "targets" / "delta-capital.json").read_text(encoding="utf-8"))["graph"])
            out = c.expand_deep_dive(child, ScriptedResearchPass(fx.deep_dive_script()))
            after = FundGraph.from_dict(out["graph"])

            self.assertEqual(out["verdict"], "accepted")
            self.assertEqual([n.name for n in after.by_kind(DEAL)], ["Nordwind Series B"])
            self.assertEqual([n.name for n in after.by_kind(PORTFOLIO_COMPANY)],
                             ["Nordwind Robotics"])
            self.assertEqual([n.name for n in after.by_kind(PERSON)], ["Anna Sorokin"])
            # parent nodes + evidence preserved, not replaced
            self.assertGreater(len(after.nodes), len(before.nodes))
            self.assertGreaterEqual(len(after.evidence), len(before.evidence))
            for nid in before.nodes:
                self.assertIn(nid, after.nodes)
            for eid in before.evidence:
                self.assertIn(eid, after.evidence)
            # the deal links a vehicle AND a target (relationship integrity)
            deal = after.by_kind(DEAL)[0]
            self.assertTrue(after.edges_of(fi.DEAL_OF, src=deal.id))
            self.assertTrue(after.edges_of(fi.DEAL_TARGET, src=deal.id))
            self.assertEqual(child.load_meta()["status"], COMPLETE)

    def test_parent_run_is_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            c, h = self._parent(d)
            ppath = h.dir / "targets" / "delta-capital.json"
            before = ppath.read_text(encoding="utf-8")
            child = c.create_deep_dive(h, "Delta Capital")
            c.expand_deep_dive(child, ScriptedResearchPass(fx.deep_dive_script()))
            self.assertEqual(ppath.read_text(encoding="utf-8"), before)

    def test_deep_dive_requires_an_accepted_target(self):
        with tempfile.TemporaryDirectory() as d:
            c, h = self._parent(d)
            with self.assertRaises(FileNotFoundError):
                c.create_deep_dive(h, "Boreas Ventures Management")   # rejected


# ── pack / core contract ─────────────────────────────────────────────────────
class TestPackContract(unittest.TestCase):
    def test_pack_satisfies_the_core_protocol(self):
        from research_core import ResearchPack, PackRegistry
        pack = FundsPack()
        self.assertIsInstance(pack, ResearchPack)
        self.assertTrue(pack.id and pack.version)
        reg = PackRegistry()
        reg.register(pack)
        self.assertEqual(reg.ids(), ["funds"])

    def test_all_declared_rules_are_registered(self):
        reg = FundsPack().registry()
        for rid in fi.rules.ALL_RULE_IDS:
            self.assertTrue(reg.has(rid), rid)

    def test_spec_selects_rules_and_outputs(self):
        spec = FundsPack().default_spec(FundsMandate(geography=["RU"]))
        self.assertEqual(spec.pack_id, "funds")
        self.assertEqual(spec.geo, "RU")
        self.assertEqual(set(spec.rule_ids), set(fi.rules.ALL_RULE_IDS))
        self.assertIn("fund_graph", spec.outputs)

    def test_company_stack_is_confined_to_the_adapter_module(self):
        """The production stack may be imported ONLY by the adapter
        (`live_pass.py`). Every other Funds module — model, rules, pack,
        controller, fixtures — must stay provider-free, so the domain logic and
        the offline flow never depend on `src/`."""
        pkg = Path(fi.__file__).parent
        offenders = []
        for py in sorted(list(pkg.glob("*.py")) + list(pkg.glob("*/*.py"))):
            if py.name == "live_pass.py":
                continue                      # the one allowed boundary
            for ln in py.read_text(encoding="utf-8").splitlines():
                s = ln.strip()
                if s.startswith("import src") or s.startswith("from src ") or s.startswith("from src."):
                    offenders.append(f"{py.name}: {s}")
        self.assertEqual(offenders, [], f"src/ imported outside live_pass.py: {offenders}")

    def test_offline_flow_never_imports_the_provider_stack(self):
        """A fresh interpreter running the whole offline Funds flow must pull in
        no `src.*` module — proof the offline slice is provider-free."""
        import subprocess
        import sys
        repo = Path(fi.__file__).parent.parent
        code = (
            "import tempfile, sys; from pathlib import Path\n"
            "from research_core import RunStore, ScriptedResearchPass\n"
            "from funds_intelligence import FundsController, FundsMandate\n"
            "from funds_intelligence import fixtures as fx\n"
            "c = FundsController(RunStore(Path(tempfile.mkdtemp())))\n"
            "h = c.create_run('m', FundsMandate(target_count=5))\n"
            "c.run_landscape(h, ScriptedResearchPass(fx.landscape_script()))\n"
            "c.approve_scope(h, fx.LANDSCAPE_TARGETS)\n"
            "c.run_until_terminal(h, lambda t: ScriptedResearchPass(fx.target_script(t)))\n"
            "bad=[m for m in sys.modules if m=='src' or m.startswith('src.')]\n"
            "print('BAD' if bad else 'CLEAN', bad)\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(repo),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("CLEAN"), r.stdout + r.stderr)


# ── the production contract: raw persistence, extraction, prompts ────────────
#
# These cover the defect that killed the first live landscape pass: the
# controller did `json.loads(res.text)` on raw provider text and kept no copy,
# so a fenced or prose-wrapped reply destroyed a fully-paid pass. Every test
# here fails against the pre-fix controller.
class _FakePass:
    """A pass that returns EXACTLY the text given — the provider-shaped inputs
    a ScriptedResearchPass never produces (it always emits clean JSON, which is
    precisely why the offline suite missed this)."""

    def __init__(self, text: str, *, paid: bool = False):
        self.text = text
        self.costs_money = paid
        self.usages = []

    def run_pass(self, system, user, *, budget=None):
        from research_core import ResearchPassResult
        self.system, self.user = system, user
        return ResearchPassResult(text=self.text, grounding=None, engine="fake")


_LANDSCAPE_OBJ = {"candidates": [
    {"name": "Verdane Capital Advisors", "kind": "management_company",
     "source": "https://reg.example/verdane"}]}
_RESEARCH_OBJ = {
    "management_company": {"name": "Verdane Capital Advisors",
                           "aum": {"value": "€8.5bn", "state": "confirmed",
                                   "source": "https://reg.example/verdane"}},
    "vehicles": [{"name": "Verdane Edda II", "vintage": 2021,
                  "fund_size": {"value": "€300m", "state": "confirmed",
                                "source": "https://reg.example/verdane"}}],
}


class TestProviderShapedOutputIsAccepted(unittest.TestCase):
    """A real provider does not return a bare JSON document."""

    def _landscape_with(self, text):
        d = tempfile.mkdtemp()
        c, _ = _controller(d)
        h = c.create_run("m", FundsMandate(target_count=3))
        return c, h, c.run_landscape(h, _FakePass(text))

    def test_plain_json_object(self):
        _, _, cands = self._landscape_with(json.dumps(_LANDSCAPE_OBJ))
        self.assertEqual([x["name"] for x in cands], ["Verdane Capital Advisors"])

    def test_fenced_json_is_extracted(self):
        text = "```json\n" + json.dumps(_LANDSCAPE_OBJ) + "\n```"
        _, _, cands = self._landscape_with(text)
        self.assertEqual([x["name"] for x in cands], ["Verdane Capital Advisors"])

    def test_unlabelled_fence_is_extracted(self):
        text = "```\n" + json.dumps(_LANDSCAPE_OBJ) + "\n```"
        _, _, cands = self._landscape_with(text)
        self.assertEqual(len(cands), 1)

    def test_prose_preamble_and_trailing_commentary_are_stripped(self):
        text = ("Here is what I found after searching several sources.\n\n"
                + json.dumps(_LANDSCAPE_OBJ)
                + "\n\nLet me know if you would like more detail.")
        _, _, cands = self._landscape_with(text)
        self.assertEqual(len(cands), 1)

    def test_nested_braces_do_not_truncate_the_object(self):
        obj = {"candidates": [{"name": "A", "kind": "management_company",
                               "aum": {"value": "€1bn", "state": "confirmed",
                                       "source": "https://reg.example/a"}}]}
        _, _, cands = self._landscape_with("```json\n" + json.dumps(obj) + "\n```")
        self.assertEqual(cands[0]["aum"]["value"], "€1bn")


class TestMalformedOutputFailsSafe(unittest.TestCase):
    """Unusable output must be diagnosable, non-advancing and retryable."""

    def _attempt(self, text, tmp):
        c, _ = _controller(tmp)
        h = c.create_run("m", FundsMandate(target_count=3))
        with self.assertRaises(fi.MalformedPassOutput) as ctx:
            c.run_landscape(h, _FakePass(text))
        return c, h, ctx.exception

    def test_prose_only_reply_raises_and_keeps_the_raw_response(self):
        with tempfile.TemporaryDirectory() as d:
            text = "I was unable to complete this research within the tool budget."
            c, h, ex = self._attempt(text, d)
            self.assertTrue(ex.raw_path.exists())
            self.assertEqual(ex.raw_path.read_text(encoding="utf-8"), text)
            self.assertEqual(ex.stage, "landscape")

    def test_run_state_does_not_advance(self):
        with tempfile.TemporaryDirectory() as d:
            c, h, _ = self._attempt("no json here at all", d)
            self.assertEqual(h.load_meta()["status"], "created")
            self.assertNotEqual(h.load_meta().get("status"), fi.AWAITING_SCOPE)
            self.assertFalse(h.path("landscape.json").exists())
            self.assertIsNone(h.ledger.last("landscape_built"))
            self.assertIsNone(h.ledger.last("awaiting_scope_approval"))

    def test_failure_is_recorded_in_the_ledger_with_the_raw_filename(self):
        with tempfile.TemporaryDirectory() as d:
            c, h, ex = self._attempt("nothing structured", d)
            ev = h.ledger.last("pass_output_unparsable")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["raw"], ex.raw_path.name)
            self.assertIsNotNone(h.ledger.last("pass_output_saved"))

    def test_truncated_json_is_refused_not_silently_half_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            text = '{"candidates": [{"name": "Half Written Capital"'
            c, h, _ = self._attempt(text, d)
            self.assertFalse(h.path("landscape.json").exists())

    def test_schema_valid_json_of_the_wrong_shape_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            c, h, ex = self._attempt('{"managers": ["Some Firm"]}', d)
            self.assertIn("candidates", ex.reason)
            self.assertIsNotNone(h.ledger.last("pass_output_invalid"))
            self.assertFalse(h.path("landscape.json").exists())

    def test_empty_candidate_list_does_not_advance_to_scope_approval(self):
        with tempfile.TemporaryDirectory() as d:
            c, h, _ = self._attempt('{"candidates": []}', d)
            self.assertEqual(h.load_meta()["status"], "created")

    def test_retry_after_a_failure_succeeds_and_preserves_both_responses(self):
        """The paid-response-loss scenario, end to end."""
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            with self.assertRaises(fi.MalformedPassOutput):
                c.run_landscape(h, _FakePass("sorry, prose only"))
            cands = c.run_landscape(h, _FakePass(json.dumps(_LANDSCAPE_OBJ)))
            self.assertEqual(len(cands), 1)
            self.assertEqual(h.load_meta()["status"], fi.AWAITING_SCOPE)
            raws = fi.list_raw(h)
            self.assertEqual(len(raws), 2, "the failed attempt must not be overwritten")
            self.assertEqual(raws[0].read_text(encoding="utf-8"), "sorry, prose only")


class TestRawIsPersistedBeforeParsing(unittest.TestCase):
    def test_successful_pass_also_keeps_its_raw_response(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            c.run_landscape(h, _FakePass(json.dumps(_LANDSCAPE_OBJ)))
            raws = fi.list_raw(h)
            self.assertEqual(len(raws), 1)
            meta = json.loads(raws[0].with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(meta["stage"], "landscape")
            self.assertEqual(meta["engine"], "fake")

    def test_raw_is_written_even_when_extraction_will_fail(self):
        """Ordering is the whole point: persist happens BEFORE extract."""
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            with self.assertRaises(fi.MalformedPassOutput):
                c.run_landscape(h, _FakePass("prose"))
            self.assertEqual(len(fi.list_raw(h)), 1)

    def test_every_paid_stage_persists_raw(self):
        """research and deep_dive must not repeat the landscape's mistake."""
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            c.run_landscape(h, _FakePass(json.dumps(_LANDSCAPE_OBJ)))
            c.approve_scope(h, ["Verdane Capital Advisors"])
            c.research_one(h, _FakePass("```json\n" + json.dumps(_RESEARCH_OBJ) + "\n```"),
                           "Verdane Capital Advisors")
            stages = [json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["stage"]
                      for p in fi.list_raw(h)]
            self.assertEqual(stages, ["landscape", "research"])


class TestResearchAndDeepDiveShareTheContract(unittest.TestCase):
    """The identical gap must be closed on the two stages we have not run yet."""

    def _to_scope(self, c, h):
        c.run_landscape(h, _FakePass(json.dumps(_LANDSCAPE_OBJ)))
        c.approve_scope(h, ["Verdane Capital Advisors"])

    def test_research_accepts_fenced_json(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            self._to_scope(c, h)
            out = c.research_one(
                h, _FakePass("```json\n" + json.dumps(_RESEARCH_OBJ) + "\n```"),
                "Verdane Capital Advisors")
            self.assertEqual(out["target"], "Verdane Capital Advisors")

    def test_research_malformed_leaves_the_target_pending_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            self._to_scope(c, h)
            with self.assertRaises(fi.MalformedPassOutput):
                c.research_one(h, _FakePass("I could not find this manager."),
                               "Verdane Capital Advisors")
            # neither accepted nor rejected → still in the research queue,
            # so a retry resumes at exactly this manager
            self.assertEqual(c.pending_targets(h), ["Verdane Capital Advisors"])
            self.assertEqual(c.rejected_targets(h), [])
            self.assertIsNone(h.ledger.last("target_researched"))

    def test_deep_dive_malformed_leaves_the_inherited_graph_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            c, store = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            self._to_scope(c, h)
            c.research_one(h, _FakePass(json.dumps(_RESEARCH_OBJ)),
                           "Verdane Capital Advisors")
            child = c.create_deep_dive(h, "Verdane Capital Advisors")
            gpath = child.path("targets", "verdane-capital-advisors.json")
            before = gpath.read_text(encoding="utf-8")
            with self.assertRaises(fi.MalformedPassOutput):
                c.expand_deep_dive(child, _FakePass("no deals were found, sorry"))
            self.assertEqual(gpath.read_text(encoding="utf-8"), before)
            self.assertIsNone(child.ledger.last("deep_dive_expanded"))
            self.assertEqual(len(fi.list_raw(child)), 1)


class TestProductionPromptsAreRealPrompts(unittest.TestCase):
    """The live path previously sent the literal string «funds landscape» as its
    system prompt — no schema, no entity rules, no evidence discipline."""

    def _mandate(self):
        return FundsMandate(geography=["Europe"], window_from=2022, window_to=2026,
                            filters={"sector": "B2B software"}, target_count=3)

    def test_landscape_prompt_declares_schema_and_constraints(self):
        system, user = fi.prompts.landscape_prompt(self._mandate())
        self.assertIn('"candidates"', system)
        self.assertIn("management_company", system)
        self.assertIn("Europe", user)
        self.assertIn("2022–2026", user)
        self.assertGreater(len(system), 1500)

    def test_research_prompt_declares_every_consumed_schema_key(self):
        system, _ = fi.prompts.research_prompt("Some Manager", self._mandate())
        for key in ("management_company", "vehicles", "continuation_vehicles",
                    "people", "portfolio", "fund_size", "vintage", "role_status",
                    "continues", "marketing"):
            self.assertIn(key, system, f"schema key «{key}» missing from the prompt")

    def test_prompts_state_the_distinctions_the_rules_enforce(self):
        system, _ = fi.prompts.research_prompt("Some Manager", self._mandate())
        low = system.lower()
        for phrase in ("aum", "fund size", "vintage", "unresolved", "marketing",
                       "source"):
            self.assertIn(phrase, low)

    def test_deep_dive_prompt_pins_vehicle_names_verbatim(self):
        system, _ = fi.prompts.deep_dive_prompt(
            "Some Manager", self._mandate(),
            known_vehicles=["Edda II", "Edda III"], known_portfolio=["Acme"])
        self.assertIn("Edda II", system)
        self.assertIn("Edda III", system)
        self.assertIn("Acme", system)

    def test_controller_sends_the_production_prompt_not_a_label(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = _controller(d)
            h = c.create_run("m", FundsMandate(target_count=3))
            p = _FakePass(json.dumps(_LANDSCAPE_OBJ))
            c.run_landscape(h, p)
            self.assertNotEqual(p.system, "funds landscape")
            self.assertIn('"candidates"', p.system)

    def test_every_stage_prompt_is_built_from_the_persisted_mandate(self):
        """A resumed session must research under the run's own mandate."""
        with tempfile.TemporaryDirectory() as d:
            c, store = _controller(d)
            h = c.create_run("m", FundsMandate(geography=["Nordics"],
                                               window_from=2019, window_to=2021,
                                               target_count=3))
            p = _FakePass(json.dumps(_LANDSCAPE_OBJ))
            c.run_landscape(store.open(h.run_id), p)
            self.assertIn("Nordics", p.user)
            self.assertIn("2019–2021", p.user)


class TestPortfolioRelationshipCanBeSourced(unittest.TestCase):
    """A model cannot know the evidence ids build_graph generates, so a
    portfolio entry must be able to carry a source URL instead."""

    def test_portfolio_source_becomes_relationship_evidence(self):
        g = fi.build_graph({
            "management_company": {"name": "M"},
            "vehicles": [{"name": "M Fund I", "vintage": 2021,
                          "fund_size": {"value": "$100m", "state": "confirmed",
                                        "source": "https://reg.example/m"}}],
            "portfolio": [{"company": "Acme", "state": "confirmed",
                           "source": "https://press.example/acme-deal"}],
        }, None)
        edges = g.edges_of(INVESTED_IN)
        self.assertEqual(len(edges), 1)
        self.assertTrue(edges[0].evidence_ids)
        self.assertTrue(g.evidence_for(edges[0]))

    def test_unsourced_portfolio_entry_still_trips_the_rule(self):
        g = fi.build_graph({
            "management_company": {"name": "M"},
            "vehicles": [{"name": "M Fund I", "vintage": 2021}],
            "portfolio": [{"company": "Acme", "state": "confirmed"}],
        }, None)
        result = FundsPack().registry().run(g.to_dict(), fi.rules.ALL_RULE_IDS)
        self.assertIn("unsupported-relationship", result.codes())


if __name__ == "__main__":
    unittest.main()
