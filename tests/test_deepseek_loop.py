"""DeepSeek tools loop — streaming reassembly, tool budget, runaway abort.
Run with:  python -m unittest tests.test_deepseek_loop"""
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src import model_router as mr

os.environ.setdefault("SEARCH_API_KEY", "fake-key-for-tests")


def chunk(content=None, tcs=None, finish=None):
    d = MagicMock()
    d.content = content
    d.tool_calls = tcs or []
    c = MagicMock()
    c.delta = d
    c.finish_reason = finish
    ck = MagicMock()
    ck.choices = [c]
    return ck


def tcd(index, id=None, name=None, args=None):
    fn = MagicMock()
    fn.name = name
    fn.arguments = args
    t = MagicMock()
    t.index = index
    t.id = id
    t.function = fn
    return t


def search_call(cid, query="q"):
    return [chunk(tcs=[tcd(0, id=cid, name="web_search",
                           args=json.dumps({"query": query}, ensure_ascii=False))]),
            chunk(finish="tool_calls")]


class TestDeepseekLoop(unittest.TestCase):
    def _run(self, iterations, max_calls=25, search_results=None):
        fc = MagicMock()
        fc.chat.completions.create = MagicMock(
            side_effect=[iter(i) for i in iterations])
        events, searches = [], []

        def fake_search(q, count=8):
            searches.append(q)
            return search_results or []

        with patch.object(mr, "_deepseek", return_value=fc), \
             patch.object(mr, "_DS_MAX_TOOL_CALLS", max_calls), \
             patch("src.web_tools.web_search", side_effect=fake_search):
            text, log = mr._run_deepseek_tools(
                "SYS", "USER", on_event=lambda a, d: events.append((a, d)))
        return text, log, events, searches, fc

    def test_split_tool_call_deltas_reassembled(self):
        it1 = [chunk(tcs=[tcd(0, id="c1", name="web_search")]),
               chunk(tcs=[tcd(0, args='{"query": "СКБ Ко')]),
               chunk(tcs=[tcd(0, args='нтур"}')]),
               chunk(finish="tool_calls")]
        it2 = [chunk(content='{"fields'), chunk(content='": {}}'), chunk(finish="stop")]
        text, log, events, searches, fc = self._run([it1, it2])
        self.assertEqual(text, '{"fields": {}}')
        self.assertEqual(searches, ["СКБ Контур"])
        self.assertEqual(events, [("thinking", ""), ("searching", "СКБ Контур"),
                                  ("thinking", ""), ("writing", "")])
        for call in fc.chat.completions.create.call_args_list:
            self.assertTrue(call.kwargs["stream"])
            self.assertIn("tools", call.kwargs)      # tools NEVER removed (DSML leak)

    def test_budget_exhausted_calls_not_executed_but_answer_returned(self):
        # cap=1: first search executes; the second gets a budget-exhausted
        # payload (NOT executed) and the model then finishes properly
        its = [search_call("c1", "первый"), search_call("c2", "второй"),
               [chunk(content='{"fields": {}}'), chunk(finish="stop")]]
        text, log, _events, searches, fc = self._run(its, max_calls=1)
        self.assertEqual(text, '{"fields": {}}')
        self.assertEqual(searches, ["первый"])       # second never executed
        self.assertEqual(log.tool_calls, 1)
        msgs = fc.chat.completions.create.call_args_list[2].kwargs["messages"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        self.assertIn("tool budget exhausted", tool_msgs[-1]["content"])
        self.assertTrue(any("Finish now" in m.get("content", "")
                            for m in msgs if m.get("role") == "user"))

    def test_runaway_tool_requests_abort(self):
        # model ignores the budget 4+ rounds in a row → clear RuntimeError
        its = [search_call(f"c{i}") for i in range(6)]
        with self.assertRaises(RuntimeError) as cm:
            self._run(its, max_calls=1)
        self.assertIn("kept requesting tools", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
