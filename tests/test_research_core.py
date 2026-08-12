"""Offline tests for research_core. No network, no provider, no src import.

Covers: event ledger (shape + concurrency), run store (format + lifecycle),
validation framework (registry, selection, verdict, code-only rules), scoped
repair merge, controller vocabulary (terminal states / limits / snapshot diff /
file-backed pause-stop-resume), gateway null adapters + grounding, spec/pack
registry, an end-to-end example-pack flow, and the src-isolation invariant.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import research_core as rc
from research_core import (
    EventLedger, RunStore, Issue, ValidationResult, RuleRegistry, REJECT, WARN,
    RepairPatch, apply_patch, Limits, ControllerSnapshot, RunControl,
    is_terminal, classify, COMPLETE, NEEDS_REVIEW, STOPPED_BUDGET,
    NullModelGateway, NullRetrievalGateway, InMemorySourceLog,
    ModelGateway, RetrievalGateway, Grounding,
    OutputSpec, ResearchSpec, PackRegistry,
    ResearchPass, ResearchPassResult, ScriptedResearchPass,
)
from research_core.control import STOPPED_NO_PROGRESS, TERMINAL_STATES, SUCCESS_STATES


class TestEventLedger(unittest.TestCase):
    def test_append_shape_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            lg = EventLedger(Path(d) / "events.jsonl")
            rec = lg.append("created", pack_id="funds", n=3)
            self.assertEqual(rec["event"], "created")
            self.assertIn("ts", rec)
            # on-disk shape: one json obj/line with ts+event first
            line = (Path(d) / "events.jsonl").read_text(encoding="utf-8").strip()
            obj = json.loads(line)
            self.assertEqual(obj["event"], "created")
            self.assertEqual(obj["pack_id"], "funds")
            self.assertEqual(list(obj)[:2], ["ts", "event"])

    def test_count_last_and_corrupt_line_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "events.jsonl"
            lg = EventLedger(p)
            lg.append("a"); lg.append("b"); lg.append("a", k=2)
            with p.open("a", encoding="utf-8") as fh:
                fh.write("not json\n")           # must not break readers
            self.assertEqual(lg.count(), 3)
            self.assertEqual(lg.count("a"), 2)
            self.assertEqual(lg.last("a")["k"], 2)

    def test_concurrent_append_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            lg = EventLedger(Path(d) / "events.jsonl")

            def worker(i):
                for j in range(20):
                    lg.append("tick", w=i, j=j)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads: t.start()
            for t in threads: t.join()
            recs = lg.read()
            self.assertEqual(len(recs), 8 * 20)
            self.assertTrue(all("ts" in r and r["event"] == "tick" for r in recs))


class TestRunStore(unittest.TestCase):
    def test_create_open_list_meta(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            h = store.create(pack_id="funds", label="RU Venture Funds",
                             meta={"geo": "ru_cis"})
            self.assertTrue(h.run_id.endswith("_funds"))
            self.assertIn("ru-venture-funds", h.run_id)
            meta = h.load_meta()
            self.assertEqual(meta["pack_id"], "funds")
            self.assertEqual(meta["status"], "created")
            self.assertEqual(meta["geo"], "ru_cis")
            # run.json is pretty-printed (indent=2), like company runs
            raw = (h.dir / "run.json").read_text(encoding="utf-8")
            self.assertIn("\n  ", raw)
            # created event logged
            self.assertEqual(h.ledger.count("created"), 1)
            # open + list
            h2 = store.open(h.run_id)
            self.assertEqual(h2.load_meta()["run_id"], h.run_id)
            self.assertEqual(len(store.list()), 1)
            self.assertTrue(store.exists(h.run_id))

    def test_update_meta_and_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            h = store.create(pack_id="p", label="m")
            h.update_meta(status="running", xlsx=None)
            self.assertEqual(h.load_meta()["status"], "running")
            sub = h.subdir("packs", "stage1")
            self.assertTrue(sub.is_dir())
            self.assertEqual(h.path("run.json").name, "run.json")

    def test_open_missing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                RunStore(Path(d)).open("nope")


class TestValidation(unittest.TestCase):
    def test_issue_shape_and_severity_guard(self):
        i = Issue("revenue", REJECT, "unsourced", "no live source")
        self.assertEqual(i.as_dict(),
                         {"field": "revenue", "severity": "reject",
                          "code": "unsourced", "reason": "no live source"})
        with self.assertRaises(ValueError):
            Issue("x", "fatal", "c")

    def test_result_verdict_mirrors_gate(self):
        r = ValidationResult([Issue("a", WARN, "w"), Issue("b", REJECT, "r")])
        self.assertFalse(r.accepted)
        self.assertEqual(r.verdict, "rejected")
        self.assertEqual(len(r.rejects), 1)
        self.assertEqual(len(r.warnings), 1)
        self.assertTrue(ValidationResult([Issue("a", WARN, "w")]).accepted)

    def test_registry_selection_and_code_only(self):
        reg = RuleRegistry()

        @reg.rule("needs-name")
        def _r(payload):
            return [] if payload.get("name") else [Issue("name", REJECT, "needs-name", "blank")]

        reg.register("needs-geo",
                     lambda p: [] if p.get("geo") else [{"field": "geo", "severity": "warn", "code": "needs-geo"}])

        self.assertEqual(reg.ids(), ["needs-geo", "needs-name"])
        # select a subset
        res = reg.run({"name": "", "geo": "ru"}, ["needs-name"])
        self.assertEqual(res.codes(), {"needs-name"})
        # all rules, dict-return coerced to Issue
        res2 = reg.run({"name": "X", "geo": ""})
        self.assertEqual(res2.codes(), {"needs-geo"})
        self.assertTrue(res2.accepted)  # only a warn
        # unknown id raises (a spec cannot silently drop a rule)
        with self.assertRaises(KeyError):
            reg.run({}, ["no-such-rule"])
        # non-callable / duplicate rejected
        with self.assertRaises(TypeError):
            reg.register("bad", "not a function")
        with self.assertRaises(ValueError):
            reg.register("needs-geo", lambda p: [])


class TestRepair(unittest.TestCase):
    def _rec(self):
        return {"fields": {"inn": {"value": "7700000000"},
                           "revenue": {"value": ""},
                           "headcount": {"value": "50"}},
                "review_flags": ["revenue: coverage gap"]}

    def test_in_scope_overwrite_gapfill_and_preserve(self):
        base = self._rec()
        patch = RepairPatch(
            scope={"inn"},
            fields={"inn": {"value": "7800000001"},       # in-scope → overwrite
                    "revenue": {"value": "250 млн ₽"},     # gap-fill (was empty)
                    "headcount": {"value": "9999"}},        # out-of-scope, present → keep
            flags=["inn: corrected"])
        out = apply_patch(base, patch)
        self.assertEqual(out["fields"]["inn"]["value"], "7800000001")
        self.assertEqual(out["fields"]["revenue"]["value"], "250 млн ₽")
        self.assertEqual(out["fields"]["headcount"]["value"], "50")   # preserved
        self.assertEqual(out["review_flags"], ["revenue: coverage gap", "inn: corrected"])
        # base not mutated
        self.assertEqual(base["fields"]["inn"]["value"], "7700000000")

    def test_dropped_field_is_preserved(self):
        base = self._rec()
        out = apply_patch(base, RepairPatch(scope={"inn"}, fields={"inn": {"value": "7800000002"}}))
        self.assertIn("headcount", out["fields"])   # model dropped it → still there

    def test_custom_container_and_empty(self):
        base = {"data": {"a": None, "b": "keep"}}
        out = apply_patch(base, RepairPatch(scope=set(), fields={"a": "filled", "b": "IGNORED"}),
                          container="data")
        self.assertEqual(out["data"]["a"], "filled")   # gap-fill
        self.assertEqual(out["data"]["b"], "keep")     # out-of-scope, non-empty → kept


class TestControl(unittest.TestCase):
    def test_terminal_sets_and_classify(self):
        self.assertTrue(is_terminal(COMPLETE))
        self.assertFalse(is_terminal("running"))
        self.assertTrue(SUCCESS_STATES <= TERMINAL_STATES)
        self.assertEqual(classify(COMPLETE), "success")
        self.assertEqual(classify(NEEDS_REVIEW), "review")
        self.assertEqual(classify(STOPPED_NO_PROGRESS), "stopped")
        self.assertEqual(classify("running"), "running")

    def test_limits(self):
        lim = Limits(max_steps=3, max_tokens=1000)
        self.assertIsNone(lim.exceeded(steps=2, tokens=500))
        self.assertEqual(lim.exceeded(steps=3), "max_steps")
        self.assertEqual(lim.exceeded(steps=0, tokens=1000), "max_tokens")

    def test_snapshot_diff(self):
        s1 = ControllerSnapshot.of({"accepted": 2, "unresolved": ["a", "b"]})
        s2 = ControllerSnapshot.of({"unresolved": ["a", "b"], "accepted": 2})  # order-independent
        s3 = ControllerSnapshot.of({"accepted": 3, "unresolved": ["a", "b"]})
        self.assertFalse(s1.changed_from(s2))
        self.assertTrue(s3.changed_from(s1))
        self.assertTrue(s1.changed_from(None))

    def test_run_control_file_backed_resume(self):
        with tempfile.TemporaryDirectory() as d:
            c = RunControl(run_dir=Path(d))
            self.assertFalse(c.should_stop())
            c.request_stop()
            self.assertTrue(c.should_stop())
            # a fresh controller over the same dir (i.e. after a restart) sees it
            self.assertTrue(RunControl(run_dir=Path(d)).should_stop())
            c.clear()
            self.assertFalse(RunControl(run_dir=Path(d)).should_stop())

    def test_run_control_in_memory_without_dir(self):
        c = RunControl()
        c.request_pause()
        self.assertTrue(c.should_pause())
        self.assertFalse(c.should_stop())


class TestGateways(unittest.TestCase):
    def test_null_adapters_offline(self):
        m = NullModelGateway(reply='{"ok": true}')
        self.assertEqual(m.complete("s", "u").text, '{"ok": true}')
        self.assertEqual(m.calls, 1)
        r = NullRetrievalGateway()
        self.assertEqual(r.search("q"), [])
        self.assertFalse(r.fetch("http://x").ok)

    def test_protocols_runtime_checkable(self):
        self.assertIsInstance(NullModelGateway(), ModelGateway)
        self.assertIsInstance(NullRetrievalGateway(), RetrievalGateway)
        self.assertIsInstance(InMemorySourceLog(), Grounding)

    def test_source_log_grounding(self):
        log = InMemorySourceLog()
        log.fetched("https://rusprofile.ru/id/1", "ООО Ромашка ИНН 7700000000 выручка 250 млн")
        log.seen("https://example.com")
        self.assertTrue(log.has_source("https://rusprofile.ru/id/1"))
        self.assertTrue(log.has_source("https://example.com"))
        self.assertFalse(log.has_source("https://never.seen"))
        self.assertTrue(log.supports_value("7700000000"))
        self.assertFalse(log.supports_value("9999999999"))   # fabricated → unsupported


class TestSpecAndRegistry(unittest.TestCase):
    def test_spec_meta_roundtrip(self):
        spec = ResearchSpec(pack_id="funds", geo="ru_cis", output_language="Russian",
                            params={"stage": "seed"}, rule_ids=["needs-name"],
                            outputs=["fund_table"])
        again = ResearchSpec.from_meta(spec.to_meta())
        self.assertEqual(again.pack_id, "funds")
        self.assertEqual(again.geo, "ru_cis")
        self.assertEqual(again.rule_ids, ["needs-name"])
        self.assertEqual(again.outputs, ["fund_table"])

    def test_pack_registry(self):
        class Pack:
            id = "demo"
            def register_rules(self, reg): pass
            def outputs(self): return [OutputSpec("t", "xlsx")]
            def default_spec(self): return ResearchSpec(pack_id="demo")

        reg = PackRegistry()
        reg.register(Pack())
        self.assertEqual(reg.ids(), ["demo"])
        self.assertEqual(reg.get("demo").id, "demo")
        with self.assertRaises(KeyError):
            reg.get("missing")
        with self.assertRaises(ValueError):
            reg.register(Pack())          # duplicate id


class TestExamplePackEndToEnd(unittest.TestCase):
    """A tiny illustrative pack (defined here, not shipped) driving the core
    end-to-end offline: create run → register rules → validate → scoped repair →
    re-validate accepted → outputs declared. Proves the seam is credible."""

    def test_flow(self):
        reg = RuleRegistry()

        @reg.rule("required-name")
        def _name(p):
            f = p.get("fields") or {}
            v = (f.get("name") or {}).get("value")
            return [] if v else [Issue("name", REJECT, "required-name", "name is blank")]

        spec = ResearchSpec(pack_id="example", geo="ru_cis",
                            rule_ids=["required-name"], outputs=["brief"])

        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            h = store.create(pack_id=spec.pack_id, label="demo mandate",
                             meta={"spec": spec.to_meta()})
            h.event("record_saved", entity="Alpha")

            record = {"fields": {"name": {"value": ""}}, "review_flags": []}
            r1 = reg.run(record, spec.rule_ids)
            self.assertFalse(r1.accepted)
            h.event("gate", verdict=r1.verdict, codes=sorted(r1.codes()))

            # scoped repair fills exactly the flagged field
            fixed = apply_patch(record, RepairPatch(scope={"name"},
                                                    fields={"name": {"value": "Alpha Capital"}}))
            r2 = reg.run(fixed, spec.rule_ids)
            self.assertTrue(r2.accepted)
            h.update_meta(status=COMPLETE)
            h.event("gate", verdict=r2.verdict)

            # ledger is the source of truth
            events = [e["event"] for e in h.ledger.read()]
            self.assertEqual(events.count("gate"), 2)
            self.assertEqual(h.load_meta()["status"], COMPLETE)
            self.assertEqual(ResearchSpec.from_meta(h.load_meta()["spec"]).pack_id, "example")


class TestCoreIsolation(unittest.TestCase):
    """The core must not import from the company app (`src/`). Enforced
    statically so the boundary can't erode silently."""

    def test_no_src_imports(self):
        pkg = Path(rc.__file__).parent
        offenders = []
        for py in pkg.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            for ln in text.splitlines():
                s = ln.strip()
                if s.startswith("import src") or s.startswith("from src ") or s.startswith("from src."):
                    offenders.append(f"{py.name}: {s}")
        self.assertEqual(offenders, [], f"research_core must not import src: {offenders}")

    def test_no_src_module_pulled_in(self):
        # A FRESH interpreter importing only research_core must pull in no src.*
        # module (checking this process's sys.modules is invalid — sibling test
        # files in the suite import src.* first).
        import subprocess
        import sys
        repo = Path(rc.__file__).parent.parent
        code = ("import research_core, sys; "
                "bad=[m for m in sys.modules if m=='src' or m.startswith('src.')]; "
                "print('BAD' if bad else 'CLEAN', bad)")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(repo),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("CLEAN"), r.stdout + r.stderr)


# ── foundation fixes added for the Funds vertical slice ──────────────────────
class TestPackVersionPersisted(unittest.TestCase):
    """A run must identify the EXACT pack version that owns it (reproducibility
    across pack evolution)."""

    def test_version_in_meta_and_created_event(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            h = store.create(pack_id="funds", label="RU VC", pack_version="1.3.0")
            self.assertEqual(h.load_meta()["pack_version"], "1.3.0")
            created = h.ledger.last("created")
            self.assertEqual(created["pack_version"], "1.3.0")

    def test_version_defaults_and_stringified(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            self.assertEqual(store.create(pack_id="p", label="m").load_meta()["pack_version"], "0")
            h = store.create(pack_id="p", label="m2", pack_version=2)
            self.assertEqual(h.load_meta()["pack_version"], "2")   # coerced to str


class TestLedgerCrossHandleConcurrency(unittest.TestCase):
    """Two handles to the SAME run must serialise their appends. A per-instance
    lock would not — this guards the controller+UI case."""

    def test_two_ledgers_same_file_share_a_lock(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "events.jsonl"
            a, b = EventLedger(p), EventLedger(p)
            self.assertIs(a._lock, b._lock)   # same file → same lock object

            def w(lg, tag):
                for j in range(50):
                    lg.append("tick", who=tag, j=j)

            t1 = threading.Thread(target=w, args=(a, "A"))
            t2 = threading.Thread(target=w, args=(b, "B"))
            t1.start(); t2.start(); t1.join(); t2.join()
            # every line intact & parseable (no interleaved/torn writes), 100 total
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 100)
            for ln in lines:
                json.loads(ln)   # would raise on a torn line
            self.assertEqual(a.count("tick"), 100)

    def test_different_files_get_different_locks(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNot(EventLedger(Path(d) / "a.jsonl")._lock,
                             EventLedger(Path(d) / "b.jsonl")._lock)

    def test_lock_keyed_by_resolved_path(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            a = EventLedger(base / "events.jsonl")
            b = EventLedger(base / "sub" / ".." / "events.jsonl")   # same file, messy path
            self.assertIs(a._lock, b._lock)


class TestResearchPass(unittest.TestCase):
    """The higher-level browse+extract+ground seam (added because collect() owns
    provider tool orchestration and does not fit complete()+search())."""

    def test_scripted_pass_returns_text_and_grounding(self):
        rp = ScriptedResearchPass([
            ('{"name": "Alpha Capital"}',
             {"https://reg.example/alpha": "Alpha Capital AUM $1.2bn manager"}),
        ])
        self.assertIsInstance(rp, ResearchPass)
        res = rp.run_pass("sys", "research Alpha")
        self.assertIsInstance(res, ResearchPassResult)
        self.assertEqual(json.loads(res.text)["name"], "Alpha Capital")
        # grounding is populated from the scripted page → pack can verify claims
        self.assertTrue(res.grounding.has_source("https://reg.example/alpha"))
        self.assertTrue(res.grounding.supports_value("$1.2bn"))
        self.assertFalse(res.grounding.supports_value("$9bn"))   # fabricated → unsupported

    def test_script_exhaustion_is_empty_ungrounded(self):
        rp = ScriptedResearchPass([])
        res = rp.run_pass("s", "u")
        self.assertEqual(res.text, "{}")
        self.assertFalse(res.grounding.has_source("anything"))

    def test_steps_advance(self):
        rp = ScriptedResearchPass([("{\"i\": 1}", {}), ("{\"i\": 2}", {})])
        self.assertEqual(json.loads(rp.run_pass("s", "u").text)["i"], 1)
        self.assertEqual(json.loads(rp.run_pass("s", "u").text)["i"], 2)
        self.assertEqual(rp.calls, 2)


if __name__ == "__main__":
    unittest.main()
