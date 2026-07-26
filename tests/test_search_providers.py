"""Tavily provider + search error taxonomy + compact telemetry + secret
redaction, and that Brave's behaviour is unchanged (402 sticky, 429 no longer
permanent). Offline — every HTTP call is mocked.
Run with: python -m unittest tests.test_search_providers"""
import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from src import web_tools as wt


def _resp(status=200, json_body=None, headers=None, raise_json=False):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    if raise_json:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_body if json_body is not None else {}
    return r


class _Base(unittest.TestCase):
    def setUp(self):
        wt.reset_quota_flag()
        wt.SEARCH_TELEMETRY_SINK = None
        self._env = patch.dict(os.environ, {"SEARCH_API_KEY": "sekret-KEY-123",
                                            "SEARCH_PROVIDER": "brave"},
                               clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(wt.reset_quota_flag)
        # keep retries instant
        self._sleep = patch.object(wt.time, "sleep", lambda *_: None)
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestTavilyProvider(_Base):
    def test_normalises_to_internal_contract(self):
        body = {"results": [{"title": "T", "url": "https://a.ru/p", "content": "c"},
                            {"title": "T2", "url": "https://b.ru", "content": "c2"}]}
        with patch.object(wt.requests, "post", return_value=_resp(json_body=body)) as post, \
             patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily"}):
            out = wt.web_search("q", count=5)
        self.assertEqual(out, [{"title": "T", "url": "https://a.ru/p", "snippet": "c"},
                               {"title": "T2", "url": "https://b.ru", "snippet": "c2"}])
        # controlled/reproducible request parameters
        body_sent = post.call_args.kwargs["json"]
        self.assertEqual(body_sent["search_depth"], "basic")
        self.assertFalse(body_sent["auto_parameters"])
        self.assertFalse(body_sent["include_answer"])
        self.assertFalse(body_sent["include_raw_content"])
        self.assertEqual(body_sent["max_results"], 5)
        # key travels in the Authorization header, NEVER the body
        self.assertNotIn("api_key", body_sent)
        self.assertIn("Authorization", post.call_args.kwargs["headers"])

    def test_provider_specific_key_preferred(self):
        with patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily",
                                     "TAVILY_API_KEY": "tvly-abc"}), \
             patch.object(wt.requests, "post", return_value=_resp(json_body={"results": []})) as post:
            wt.web_search("q")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"],
                         "Bearer tvly-abc")

    def test_empty_results_is_success_not_error(self):
        with patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily"}), \
             patch.object(wt.requests, "post", return_value=_resp(json_body={"results": []})):
            out, tel = wt.search_with_telemetry("q", provider="tavily")
        self.assertEqual(out, [])
        self.assertEqual(tel.outcome, "empty")
        self.assertEqual(tel.error_category, "")


class TestErrorTaxonomy(_Base):
    def test_brave_402_sticky_quota(self):
        with patch.object(wt.requests, "get", return_value=_resp(402)):
            out, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.outcome, "error")
        self.assertEqual(tel.error_category, "quota")
        self.assertTrue(wt.QUOTA_EXHAUSTED)             # sticky, as before
        self.assertTrue(tel.quota_state)

    def test_429_is_transient_not_sticky(self):
        # persistent 429 → rate_limit after bounded retries, NEVER quota
        with patch.object(wt.requests, "get",
                          return_value=_resp(429, headers={"Retry-After": "0"})):
            out, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.error_category, "rate_limit")
        self.assertEqual(tel.http_status, 429)
        self.assertEqual(tel.retries, wt.SEARCH_RETRY_MAX)
        self.assertFalse(wt.QUOTA_EXHAUSTED)            # the key fix
        self.assertFalse(tel.quota_state)

    def test_429_then_success_retries(self):
        seq = [_resp(429, headers={"Retry-After": "0"}),
               _resp(json_body={"web": {"results": [{"title": "t", "url": "https://a.ru", "description": "d"}]}})]
        with patch.object(wt.requests, "get", side_effect=seq):
            out, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.outcome, "ok")
        self.assertEqual(tel.retries, 1)
        self.assertEqual(len(out), 1)

    def test_auth_error(self):
        with patch.object(wt.requests, "get", return_value=_resp(401)):
            out, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.error_category, "auth")
        self.assertEqual(tel.http_status, 401)
        self.assertFalse(wt.QUOTA_EXHAUSTED)

    def test_timeout_and_network(self):
        with patch.object(wt.requests, "get", side_effect=requests.Timeout()):
            _, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.error_category, "timeout")
        with patch.object(wt.requests, "get", side_effect=requests.ConnectionError()):
            _, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.error_category, "network")

    def test_malformed_response(self):
        with patch.object(wt.requests, "get", return_value=_resp(raise_json=True)):
            _, tel = wt.search_with_telemetry("q", provider="brave")
        self.assertEqual(tel.error_category, "malformed")

    def test_missing_key_is_config_error(self):
        with patch.dict(os.environ, {"SEARCH_API_KEY": "", "SEARCH_PROVIDER": "tavily"},
                        clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            _, tel = wt.search_with_telemetry("q", provider="tavily")
        self.assertEqual(tel.error_category, "config")

    def test_unknown_provider_is_config_error(self):
        _, tel = wt.search_with_telemetry("q", provider="duckduck")
        self.assertEqual(tel.error_category, "config")


class TestBraveUnchanged(_Base):
    def test_brave_success_shape_and_default_provider(self):
        body = {"web": {"results": [{"title": "t", "url": "https://x.ru/p",
                                     "description": "d"}]}}
        with patch.object(wt.requests, "get", return_value=_resp(json_body=body)) as get:
            out = wt.web_search("q")                    # SEARCH_PROVIDER=brave
        self.assertEqual(out, [{"title": "t", "url": "https://x.ru/p", "snippet": "d"}])
        self.assertIn("X-Subscription-Token", get.call_args.kwargs["headers"])

    def test_sticky_flag_short_circuits_web_search(self):
        wt.QUOTA_EXHAUSTED = True
        with patch.object(wt.requests, "get") as get:
            with self.assertRaises(wt.SearchQuotaExhausted):
                wt.web_search("q")
            get.assert_not_called()


class TestTelemetryAndRedaction(_Base):
    def test_public_dict_has_required_fields_no_secrets(self):
        body = {"web": {"results": []}}
        with patch.object(wt.requests, "get", return_value=_resp(json_body=body)):
            _, tel = wt.search_with_telemetry("q", provider="brave")
        d = tel.public_dict()
        for k in ("provider", "outcome", "latency_ms", "results",
                  "error_category", "http_status", "retry_after_s", "retries",
                  "retry_delay_s", "quota_state"):
            self.assertIn(k, d)
        self.assertNotIn("error_exc", d)
        self.assertNotIn("sekret-KEY-123", str(d))

    def test_redact_masks_configured_and_shaped_keys(self):
        self.assertNotIn("sekret-KEY-123", wt.redact("token=sekret-KEY-123 fail"))
        self.assertIn("‹redacted-key›", wt.redact("k=sekret-KEY-123"))
        self.assertIn("‹redacted-key›", wt.redact("bad tvly-ABC123xyz here"))
        self.assertIn("‹redacted›",
                      wt.redact("Authorization: Bearer verysecrettoken"))

    def test_web_search_emits_telemetry_to_sink(self):
        seen = []
        wt.SEARCH_TELEMETRY_SINK = seen.append
        self.addCleanup(lambda: setattr(wt, "SEARCH_TELEMETRY_SINK", None))
        body = {"web": {"results": [{"title": "t", "url": "https://x.ru", "description": "d"}]}}
        with patch.object(wt.requests, "get", return_value=_resp(json_body=body)):
            wt.web_search("q")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].provider, "brave")
        self.assertEqual(seen[0].outcome, "ok")

    def test_broken_sink_never_breaks_search(self):
        wt.SEARCH_TELEMETRY_SINK = lambda _t: (_ for _ in ()).throw(RuntimeError("x"))
        self.addCleanup(lambda: setattr(wt, "SEARCH_TELEMETRY_SINK", None))
        body = {"web": {"results": []}}
        with patch.object(wt.requests, "get", return_value=_resp(json_body=body)):
            self.assertEqual(wt.web_search("q"), [])    # no raise


class TestProductionExceptionContract(_Base):
    def test_web_search_reraises_quota(self):
        with patch.object(wt.requests, "get", return_value=_resp(402)):
            with self.assertRaises(wt.SearchQuotaExhausted):
                wt.web_search("q")

    def test_web_search_reraises_rate_limit_as_rate_limited(self):
        with patch.object(wt.requests, "get",
                          return_value=_resp(429, headers={"Retry-After": "0"})):
            with self.assertRaises(wt.SearchRateLimited):
                wt.web_search("q")
        self.assertFalse(wt.QUOTA_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
