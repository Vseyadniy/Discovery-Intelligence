"""Outreach contacts: published channels allowed only inside `contacts`,
never inferred elsewhere; Excel Respondents sheet add/create; manual-only flow.
Run with: python -m unittest tests.test_respondent_contacts"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src import export_excel as xl
from src import onepager, respondents, runs

TODAY = date.today().isoformat()


def _cand(**over):
    c = {"name": "Иван Иванов", "role": "CIO", "org": "Банк N",
         "why_relevant": "внедрял BPM", "addresses": ["buying_behavior"],
         "priority": 1, "profile_url": "https://conf.ru/speakers/ivanov",
         "sources": ["https://conf.ru/speakers/ivanov"],
         "confidence": "high", "role_current": True, "verified_on": TODAY}
    c.update(over)
    return c


def _codes(doc, scope="market", entity=""):
    return {(i["code"], i["severity"])
            for i in respondents.validate_respondents(doc, set(), scope, entity)}


class TestContactChannels(unittest.TestCase):
    def test_published_channels_in_contacts_accepted(self):
        doc = {"scope": "market", "candidates": [_cand(contacts={
            "telegram": "@ivanov_cio", "email": "i.ivanov@bank-n.ru",
            "linkedin": "https://linkedin.com/in/ivanov",
            "phone": "+7 495 123-45-67"})]}
        rejects = [i for i in respondents.validate_respondents(doc, set(), "market")
                   if i["severity"] == "reject"]
        self.assertEqual(rejects, [])

    def test_contact_outside_contacts_still_rejected(self):
        # the whole point of the old net is preserved for free-text fields
        self.assertIn(("private-contact", "reject"),
                      _codes({"scope": "market",
                              "candidates": [_cand(why_relevant="пишите i@bank.ru")]}))
        self.assertIn(("private-contact", "reject"),
                      _codes({"scope": "market",
                              "candidates": [_cand(email="i@bank.ru")]}))  # top-level

    def test_malformed_channels_rejected(self):
        for ct, bad in (({"email": "not-an-email"}, "email"),
                        ({"telegram": "ivanov"}, "telegram"),   # missing @ / t.me
                        ({"linkedin": "https://vk.com/x"}, "linkedin"),
                        ({"phone": "звоните завтра"}, "phone")):
            codes = _codes({"scope": "market", "candidates": [_cand(contacts=ct)]})
            self.assertIn(("bad-contact", "reject"), codes, ct)

    def test_telegram_channel_and_handle_forms_accepted(self):
        for tg in ("@ivanov_cio", "https://t.me/ivanov_cio", "https://t.me/s/true_conf"):
            codes = _codes({"scope": "market",
                            "candidates": [_cand(contacts={"telegram": tg})]})
            self.assertNotIn(("bad-contact", "reject"), codes, tg)

    def test_channel_without_source_rejected(self):
        doc = {"scope": "market",
               "candidates": [_cand(sources=[], contacts={"telegram": "@x_cio"})]}
        # sources also required-empty, but the channel-needs-a-source rule fires
        self.assertIn(("unsourced", "reject"), _codes(doc))

    def test_no_channel_is_warn_not_reject(self):
        codes = _codes({"scope": "market", "candidates": [_cand()]})
        self.assertIn(("no-channel", "warn"), codes)
        self.assertNotIn(("no-channel", "reject"), codes)

    def test_autofix_migrates_top_level_channel_into_contacts(self):
        doc = {"scope": "market", "candidates": [_cand(telegram="@ivanov_cio")]}
        notes = respondents.autofix_doc(doc)
        c = doc["candidates"][0]
        self.assertNotIn("telegram", set(c) - {"contacts"})
        self.assertEqual(c["contacts"]["telegram"], "@ivanov_cio")
        self.assertTrue(any("contacts.telegram" in n for n in notes))


class TestExcelSheet(unittest.TestCase):
    def _rows(self):
        return [{"target": "Market", "name": "Иван", "telegram": "@x",
                 "email": "i@b.ru", "priority": 1}]

    def test_creates_workbook_with_respondents_first(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "new.xlsx"
            xl.write_respondents_sheet(out, respondents.RESP_COLUMNS, self._rows())
            from openpyxl import load_workbook
            wb = load_workbook(out)
            self.assertEqual(wb.sheetnames[0], "Respondents")
            self.assertEqual([c.value for c in wb["Respondents"][1]][:2],
                             ["target", "name"])

    def test_adds_sheet_to_existing_quant_workbook(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "deliverable.xlsx"
            from openpyxl import Workbook, load_workbook
            wb = Workbook(); wb.active.title = "Research"
            wb.active["A1"] = "brand_name"; wb.save(out)
            xl.write_respondents_sheet(out, respondents.RESP_COLUMNS, self._rows())
            wb2 = load_workbook(out)
            self.assertIn("Research", wb2.sheetnames)      # quant sheet preserved
            self.assertIn("Respondents", wb2.sheetnames)

    def test_stale_respondents_sheet_replaced_not_duplicated(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "d.xlsx"
            xl.write_respondents_sheet(out, respondents.RESP_COLUMNS, self._rows())
            xl.write_respondents_sheet(out, respondents.RESP_COLUMNS,
                                       self._rows() + self._rows())
            from openpyxl import load_workbook
            wb = load_workbook(out)
            self.assertEqual(wb.sheetnames.count("Respondents"), 1)
            self.assertEqual(wb["Respondents"].max_row, 3)   # header + 2 rows


class TestManualOnlyDeliverable(unittest.TestCase):
    def test_manual_only_builds_standalone_respondents_workbook(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(runs, "LOGS", Path(td)):
                rd = onepager.create_manual_run("пилоты BPM")
                onepager.setup(rd, "выбрать вендора", {"Новая Ко": "partner"},
                               manual={"Новая Ко": {"segment": "BPM", "notes": "n"}},
                               require_goal=False)
                respondents.resp_path(rd, "новая-ко").write_text(json.dumps(
                    {"scope": "company", "entity": "Новая Ко",
                     "candidates": [_cand(name="П П", org="Новая Ко",
                                          contacts={"telegram": "@pp_bpm"})]},
                    ensure_ascii=False), encoding="utf-8")
                respondents.resp_path(rd, respondents.MARKET_STEM).write_text(
                    json.dumps({"scope": "market", "candidates": []},
                               ensure_ascii=False), encoding="utf-8")
                out = respondents.build_contacts_xlsx(rd)
                self.assertIsNotNone(out)
                from openpyxl import load_workbook
                wb = load_workbook(out)
                self.assertEqual(wb.sheetnames, ["Respondents"])   # no quant sheet
                vals = [c.value for c in wb["Respondents"][2]]
                self.assertIn("@pp_bpm", vals)                     # channel present
                self.assertIn("Новая Ко", vals)                    # manual company


if __name__ == "__main__":
    unittest.main()
