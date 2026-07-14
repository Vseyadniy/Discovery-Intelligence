"""Manual qualitative targets — coexistence with run-backed companies,
provenance, dedup, persistence. Run with: python -m unittest tests.test_manual_targets"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import onepager, runs


class TestManualEntryProvenance(unittest.TestCase):
    def test_synthetic_record_carries_only_user_context(self):
        e = onepager.manual_entry("Новая Ко", {"segment": "Low-code BPM",
                                               "notes": "пилот в двух банках"})
        rec = e["record"]
        self.assertTrue(rec["manual_target"])           # explicit, never verified
        self.assertEqual(set(rec["fields"]), {"segment", "user_notes"})
        for f in rec["fields"].values():
            self.assertEqual(f["source"], "user-provided")
        self.assertTrue(any("manual target" in fl for fl in rec["review_flags"]))

    def test_known_unclear_uses_only_user_context(self):
        e = onepager.manual_entry("Новая Ко", {"segment": "Low-code BPM",
                                               "notes": "пилот в двух банках"})
        schema_fields = [f["name"] for f in runs.load_schema()["fields"]]
        known, unclear = onepager.known_unclear(e["record"],
                                                schema_fields + ["user_notes"])
        known_names = {k["field"] for k in known}
        self.assertEqual(known_names, {"segment", "user_notes"})
        self.assertGreater(len(unclear), 10)            # everything else unclear


class TestTargetResolution(unittest.TestCase):
    def _g(self, *entities):
        return {"accepted": [{"entity": x, "stem": runs._slug(x),
                              "record": {"entity": x, "fields": {}}}
                             for x in entities]}

    def test_run_backed_wins_over_manual_duplicate(self):
        qmeta = {"companies": {
            "Directum": {"angle": "competitor",
                         "manual": {"segment": "ECM", "notes": ""}},
            "Новая Ко": {"angle": "partner",
                         "manual": {"segment": "BPM", "notes": ""}}}}
        targets = onepager.target_entries(self._g("Directum"), qmeta)
        self.assertNotIn("manual_target", targets["Directum"]["record"])  # record wins
        self.assertTrue(targets["Новая Ко"]["record"]["manual_target"])   # gap filled

    def test_normalized_name_dedup(self):
        qmeta = {"companies": {"directum ": {"angle": "competitor",
                                             "manual": {"segment": "x", "notes": ""}}}}
        targets = onepager.target_entries(self._g("Directum"), qmeta)
        self.assertNotIn("manual_target", targets["directum "]["record"])

    def test_run_backed_without_manual_key_unchanged(self):
        qmeta = {"companies": {"Directum": {"angle": "competitor"}}}
        targets = onepager.target_entries(self._g("Directum"), qmeta)
        self.assertEqual(list(targets), ["Directum"])


class TestPersistenceAndStandaloneFlow(unittest.TestCase):
    def test_setup_remove_and_prompt_on_manual_only_run(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(runs, "LOGS", Path(td)):
                rd = onepager.create_manual_run("пилоты BPM")
                self.assertTrue((rd / "run.json").exists())
                onepager.setup(rd, "выбрать вендора для пилота",
                               {"Новая Ко": "partner", "Вторая Ко": "competitor"},
                               manual={"Новая Ко": {"segment": "Low-code BPM",
                                                    "notes": "пилот в двух банках"},
                                       "Вторая Ко": {"segment": "BPM для госсектора",
                                                     "notes": ""}})
                meta = onepager.load_meta(rd)
                self.assertEqual(meta["companies"]["Новая Ко"]["manual"]["notes"],
                                 "пилот в двух банках")
                # both pending, both resolvable without ANY quantitative record
                kind, text = onepager.next_qual_prompt(rd, batch=2)
                self.assertEqual(kind, "qual-research")
                self.assertEqual(text.count("MANUAL TARGET"), 2)
                self.assertIn("пилот в двух банках", text)          # notes = KNOWN
                self.assertIn("user-provided", text)
                self.assertIn("invent figures", text)
                # removal persists
                onepager.remove_target(rd, "Вторая Ко")
                self.assertNotIn("Вторая Ко",
                                 onepager.load_meta(rd)["companies"])

    def test_manual_marker_in_rendered_onepager(self):
        e = onepager.manual_entry("Новая Ко", {"segment": "BPM", "notes": "n"})
        op = {"entity": "Новая Ко", "context": {}, "interview_brief": {},
              "priorities": {}}
        md = onepager.render_md(op, e["record"],
                                {"research_goal": "g", "companies": {}},
                                {"market": "m", "run_id": "r",
                                 "output_language": "Russian"})
        self.assertIn("✍ manual target", md)


if __name__ == "__main__":
    unittest.main()
