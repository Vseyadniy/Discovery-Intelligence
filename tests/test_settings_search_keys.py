"""Settings search-key configuration: separate Brave / Tavily masked fields,
Brave preserved in the legacy shared SEARCH_API_KEY, Tavily saved as
TAVILY_API_KEY, SEARCH_PROVIDER as the explicit selector, no API request on
save/switch, and no automatic fallback.
Run with: python -m unittest tests.test_settings_search_keys"""
import os
import unittest
from unittest.mock import patch

from src import api_runner
from src import model_router as mr
from src import web_tools as wt


class TestApplyEnvNoApiNoFallback(unittest.TestCase):
    def setUp(self):
        self.addCleanup(wt.reset_quota_flag)

    def test_save_sets_env_and_resolves_per_provider_no_http(self):
        boom_http = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no HTTP on save"))
        boom_model = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no model call on save"))
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(wt.requests, "get", side_effect=boom_http), \
             patch.object(wt.requests, "post", side_effect=boom_http), \
             patch.object(mr, "collect", side_effect=boom_model):
            api_runner.apply_env({
                "SEARCH_API_KEY": "brave-shared-key",
                "TAVILY_API_KEY": "tvly-secret",
                "SEARCH_PROVIDER": "tavily",
            })
            # provider resolves as selected; each provider gets its own key
            self.assertEqual(wt.resolve_provider(), "tavily")
            self.assertEqual(wt._provider_key("tavily"), "tvly-secret")
            self.assertEqual(wt._provider_key("brave"), "brave-shared-key")

    def test_switch_provider_resets_sticky_quota_without_http(self):
        wt.QUOTA_EXHAUSTED = True
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(wt.requests, "get",
                          side_effect=AssertionError("no HTTP")), \
             patch.object(wt.requests, "post",
                          side_effect=AssertionError("no HTTP")):
            api_runner.apply_env({"SEARCH_PROVIDER": "tavily",
                                  "TAVILY_API_KEY": "tvly-x"})
        self.assertFalse(wt.QUOTA_EXHAUSTED)     # cleared for the new selection

    def test_tavily_specific_key_beats_shared_and_no_cross_use(self):
        with patch.dict(os.environ, {"SEARCH_API_KEY": "brave-only",
                                     "TAVILY_API_KEY": "tvly-own",
                                     "SEARCH_PROVIDER": "tavily"}, clear=False):
            self.assertEqual(wt._provider_key("tavily"), "tvly-own")
            self.assertEqual(wt._provider_key("brave"), "brave-only")

    def test_legacy_shared_key_still_works_for_tavily_when_no_specific(self):
        env = {"SEARCH_API_KEY": "legacy-shared", "SEARCH_PROVIDER": "tavily"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            self.assertEqual(wt._provider_key("tavily"), "legacy-shared")


class TestSettingsUiWiring(unittest.TestCase):
    """Full round-trip through the desktop Settings handler (headless Tk)."""

    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception:
            self.skipTest("no display for Tk")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.addCleanup(wt.reset_quota_flag)

    def test_separate_fields_exist_and_prefill(self):
        with patch.dict(os.environ, {"SEARCH_API_KEY": "brave-pre",
                                     "TAVILY_API_KEY": "tvly-pre",
                                     "SEARCH_PROVIDER": "tavily"}, clear=False):
            import app as app_mod
            a = app_mod.App(self.root)
            self.assertTrue(hasattr(a, "key_search"))     # Brave / shared
            self.assertTrue(hasattr(a, "key_tavily"))     # Tavily-specific
            self.assertEqual(a.key_search.get(), "brave-pre")
            self.assertEqual(a.key_tavily.get(), "tvly-pre")
            self.assertEqual(a.search_provider.get(), "tavily")

    def test_save_keys_persists_both_and_makes_no_request(self):
        import app as app_mod
        captured = {}
        with patch.dict(os.environ, {"SEARCH_API_KEY": "brave-pre"}, clear=False):
            a = app_mod.App(self.root)
            a.key_search.set("brave-pre")          # preserved Brave key
            a.key_tavily.set("tvly-new")
            a.search_provider.set("tavily")
            with patch.object(app_mod, "save_env",
                              side_effect=lambda v: captured.update(v)), \
                 patch.object(wt.requests, "get",
                              side_effect=AssertionError("no HTTP on save")), \
                 patch.object(wt.requests, "post",
                              side_effect=AssertionError("no HTTP on save")), \
                 patch.object(mr, "collect",
                              side_effect=AssertionError("no model on save")):
                a.on_save_keys()
        self.assertEqual(captured.get("SEARCH_API_KEY"), "brave-pre")  # Brave kept
        self.assertEqual(captured.get("TAVILY_API_KEY"), "tvly-new")
        self.assertEqual(captured.get("SEARCH_PROVIDER"), "tavily")


if __name__ == "__main__":
    unittest.main()
