"""Source-priority policy (company-level stages only) + tool-flow dedup.
Run with:  python -m unittest tests.test_source_policy"""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import gate, runs
from src import model_router as mr
from tests.test_deepseek_loop import chunk, tcd

ROOT = Path(__file__).resolve().parent.parent
MARKER = "Source priority by field type"


class TestPromptPolicy(unittest.TestCase):
    def test_collectors_carry_field_type_policy(self):
        for f in ("collector_a.md", "collector_b.md"):
            text = (ROOT / "prompts" / f).read_text(encoding="utf-8")
            self.assertIn(MARKER, text, f)
            self.assertIn("Finishing under budget", text, f)

    def test_verifier_reuses_evidence_and_matches_class_to_field(self):
        text = (ROOT / "prompts" / "verifier.md").read_text(encoding="utf-8")
        self.assertIn("source CLASS matched to the field type", text)
        self.assertIn("Consolidate only the evidence already inside A and B", text)

    def test_discovery_prompt_untouched_by_policy(self):
        meta = {"run_id": "t", "market": "рынок X", "output_language": "Russian",
                "geo": "ru_cis", "depth": "superficial"}
        depth = runs.load_depths()["superficial"]
        text = runs.build_discovery_prompt(meta, depth)
        self.assertNotIn(MARKER, text)          # discovery keeps free source choice

    def test_repair_prompts_reuse_evidence_first(self):
        # prompt mode
        rejected = [{"entity": "Т", "stem": "т", "issues": [
            {"field": "inn", "code": "inn-invalid", "severity": "reject",
             "reason": "r"}]}]
        text = gate.render_repair_prompt("m", "Russian", "logs/x/agent_runs", rejected)
        self.assertIn("REUSE EXISTING EVIDENCE FIRST", text)
        self.assertIn("fields that stay unresolved", text)


class TestToolFlowDedup(unittest.TestCase):
    def _search_iter(self, cid, query):
        return [chunk(tcs=[tcd(0, id=cid, name="web_search",
                               args=json.dumps({"query": query}, ensure_ascii=False))]),
                chunk(finish="tool_calls")]

    def _fetch_iter(self, cid, url):
        return [chunk(tcs=[tcd(0, id=cid, name="fetch_url",
                               args=json.dumps({"url": url}))]),
                chunk(finish="tool_calls")]

    def test_duplicate_query_denied_without_second_search(self):
        its = [self._search_iter("c1", "Контур выручка"),
               self._search_iter("c2", "  контур   ВЫРУЧКА "),   # same, normalized
               [chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        fc = MagicMock()
        fc.chat.completions.create = MagicMock(side_effect=[iter(i) for i in its])
        searches = []
        with patch.object(mr, "_deepseek", return_value=fc), \
             patch("src.web_tools.web_search",
                   side_effect=lambda q, c=8: searches.append(q) or []):
            _t, log = mr._run_deepseek_tools("SYS", "USER")
        self.assertEqual(len(searches), 1)                 # second never executed
        self.assertEqual(log.stats["dup_queries"], 1)
        msgs = fc.chat.completions.create.call_args_list[2].kwargs["messages"]
        self.assertTrue(any("already ran this session" in m.get("content", "")
                            for m in msgs if m.get("role") == "tool"))

    def test_refetch_served_from_cache_without_http(self):
        its = [self._fetch_iter("c1", "https://kontur.ru/x"),
               self._fetch_iter("c2", "https://kontur.ru/x"),
               [chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        fc = MagicMock()
        fc.chat.completions.create = MagicMock(side_effect=[iter(i) for i in its])
        fetches = []

        def fake_fetch(u):
            fetches.append(u)
            return {"url": u, "final_url": u, "title": "t",
                    "text": "выручка 48 млрд", "fetched_at": "x"}

        with patch.object(mr, "_deepseek", return_value=fc), \
             patch("src.web_tools.fetch_url", side_effect=fake_fetch):
            _t, log = mr._run_deepseek_tools("SYS", "USER")
        self.assertEqual(len(fetches), 1)                  # one real HTTP fetch
        self.assertEqual(log.stats["cache_hits"], 1)
        msgs = fc.chat.completions.create.call_args_list[2].kwargs["messages"]
        cached = [m for m in msgs if m.get("role") == "tool"
                  and "served from session cache" in m.get("content", "")]
        self.assertEqual(len(cached), 1)
        self.assertIn("выручка 48 млрд", cached[0]["content"])   # text still usable


if __name__ == "__main__":
    unittest.main()
