"""Regression: entry widgets must support paste/copy/cut/select-all so an API
key can be pasted into Settings on macOS (Tk binds no Cmd+V there by default).
Headless — skipped where Tk has no display. The binding-presence check is the
deterministic guard; the behavioural checks self-skip where a headless/withdrawn
Tk cannot route synthetic key events to a focused widget.
Run with: python -m unittest tests.test_clipboard_shortcuts"""
import os
import unittest
from unittest.mock import patch


class TestClipboardShortcuts(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception:
            self.skipTest("no display for Tk")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        import app as app_mod
        with patch.dict(os.environ, {}, clear=False):
            self.app = app_mod.App(self.root)

    def test_all_shortcuts_bound_on_entry_classes(self):
        # the real regression guard: before the fix none of these existed
        for cls in ("TEntry", "Entry", "Text"):
            for mod in ("Command", "Control"):
                for key in ("v", "c", "x", "a"):
                    script = self.root.bind_class(cls, f"<{mod}-{key}>")
                    self.assertTrue(script, f"{cls} <{mod}-{key}> not bound")

    def _deliver(self, widget, seq):
        """True if a synthetic event actually reaches the widget here."""
        self.root.deiconify()
        widget.pack()
        widget.focus_force()
        self.root.update()
        hit = []
        widget.bind(seq, lambda e: hit.append(True))
        widget.event_generate(seq, when="now")
        self.root.update()
        return bool(hit)

    def test_cmd_v_triggers_paste_event(self):
        import tkinter as tk
        e = tk.Entry(self.root)
        if not self._deliver(e, "<Command-x>"):   # probe: can we route at all?
            self.skipTest("headless Tk cannot route synthetic key events")
        got = []
        e.bind("<<Paste>>", lambda ev: got.append(True))
        e.event_generate("<Command-v>", when="now")
        self.root.update()
        self.assertTrue(got, "Cmd+V did not generate <<Paste>>")

    def test_cmd_a_selects_all(self):
        import tkinter as tk
        e = tk.Entry(self.root)
        e.insert(0, "tvly-secret-key")
        if not self._deliver(e, "<Command-c>"):
            self.skipTest("headless Tk cannot route synthetic key events")
        e.event_generate("<Command-a>", when="now")
        self.root.update()
        self.assertTrue(e.selection_present())


if __name__ == "__main__":
    unittest.main()
