"""Retrieval/answer-path fixes: arXiv query building, structured trimming, streaming, tool selection, live events."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from backend.agent import runner as runner_mod
from backend.agent.llm import _collect_stream
from backend.agent.runner import _trim, run_agent, select_tools
from backend.agent.tools import deepseek_web
from backend.agent.tools.research.arxiv import build_search_query


class ArxivQueryTests(unittest.TestCase):
    def test_terms_are_anded_not_one_phrase(self) -> None:
        self.assertEqual(build_search_query("training free super resolution"),
                         "all:training AND all:free AND all:super AND all:resolution")

    def test_hyphens_and_quotes_become_phrases(self) -> None:
        self.assertEqual(build_search_query('"image restoration" training-free agent'),
                         'all:"image restoration" AND all:"training free" AND all:agent')

    def test_stopwords_dropped(self) -> None:
        self.assertEqual(build_search_query("agents for the web"), "all:agents AND all:web")


class TrimTests(unittest.TestCase):
    def test_keeps_structure_and_reports_omitted(self) -> None:
        data = {"total": 900, "items": [{"title": f"T{i}", "abstract": "x" * 3000} for i in range(80)]}
        trimmed = _trim(data, limit=6000)
        self.assertLessEqual(len(json.dumps(trimmed)), 6000)
        self.assertEqual(trimmed["total"], 900)
        self.assertEqual(trimmed["items"][0]["title"], "T0")
        self.assertGreater(trimmed["items_omitted"], 0)

    def test_small_result_untouched(self) -> None:
        data = {"items": [{"title": "a"}]}
        self.assertIs(_trim(data), data)


class StreamTests(unittest.TestCase):
    def test_collect_stream_assembles_tool_calls(self) -> None:
        lines = [
            {"model": "m", "choices": [{"delta": {"reasoning_content": "r"}}]},
            {"choices": [{"delta": {"content": "hi"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "search_", "arguments": "{\"q"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "papers", "arguments": "\":1}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        ]
        response = mock.Mock()
        response.iter_lines.return_value = [f"data: {json.dumps(x)}" for x in lines] + ["data: [DONE]"]
        seen = []
        data = _collect_stream(response, lambda kind, text: seen.append((kind, text)))
        message = data["choices"][0]["message"]
        self.assertEqual(message["tool_calls"][0]["function"], {"name": "search_papers", "arguments": "{\"q\":1}"})
        self.assertEqual(message["content"], "hi")
        self.assertEqual(seen, [("reasoning", "r"), ("answer", "hi")])
        self.assertEqual(data["usage"]["prompt_tokens"], 3)


class ToolSelectionTests(unittest.TestCase):
    def names(self, web: bool, available: bool, scoped=None) -> set[str]:
        with mock.patch.object(deepseek_web, "available", return_value=available):
            return {t.name for t in select_tools({"allowed_entity_ids": scoped}, web)}

    def test_web_tools_hidden_unless_switched_on(self) -> None:
        names = self.names(False, True)
        self.assertNotIn("web_search", names)
        self.assertNotIn("browser_search", names)
        self.assertIn("search_papers", names)

    def test_deepseek_search_preferred_over_browser(self) -> None:
        names = self.names(True, True)
        self.assertIn("web_search", names)
        self.assertNotIn("browser_search", names)

    def test_browser_fallback_without_key(self) -> None:
        names = self.names(True, False)
        self.assertIn("browser_search", names)
        self.assertNotIn("web_search", names)

    def test_scoped_conversation_keeps_scope_tools_only(self) -> None:
        self.assertNotIn("search_papers", self.names(True, True, scoped=["ape_x"]))


class _Cfg:
    class generator:  # noqa: N801 - mimics raglib config shape
        model = "fake"
        base_url = "http://x"
        api_key = "k"
        thinking = "disabled"
        reasoning_effort = "low"
        max_tokens = 100


class LiveEventTests(unittest.TestCase):
    def test_events_report_tool_steps_and_answer(self) -> None:
        calls = [
            {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "function": {"name": "list_facets", "arguments": json.dumps({"type": "years"})}}]},
                "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]},
        ]

        def fake_llm(client, **kwargs):
            data = calls.pop(0)
            if kwargs.get("on_delta") and data["choices"][0]["message"]["content"]:
                kwargs["on_delta"]("answer", data["choices"][0]["message"]["content"])
            return data

        conn = mock.Mock()
        conn.execute.return_value.fetchall.return_value = [{"year": 2025}]
        events = []
        with mock.patch.object(runner_mod, "llm_chat", fake_llm):
            run = run_agent(query="q", ctx_builder=lambda: {"catalog_conn": conn}, config=_Cfg(),
                            on_event=lambda live: events.append(live))
        self.assertEqual(run.answer, "done")
        self.assertTrue(any(e["steps"] and e["steps"][0]["status"] == "running" for e in events))
        self.assertEqual(events[-1]["steps"][0]["status"], "ok")
        self.assertEqual(events[-1]["answer"], "done")


class DeepSeekWebTests(unittest.TestCase):
    def test_search_maps_results_and_provenance(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = {"results": [{"title": "T", "url": "https://a.b/c", "summary": "S", "snippet": "s",
                                                   "site_name": "A", "date": "2026-10-01T00:00:00Z"}]}
        with mock.patch.object(deepseek_web.settings, "web_search_key", return_value="sk-test"), \
                mock.patch.object(deepseek_web.httpx, "post", return_value=response) as post:
            result = deepseek_web._web_search({"query": "q"}, query="llm agent", site="github.com", count=3)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["items"][0]["url"], "https://a.b/c")
        self.assertEqual(result.provenance[0]["source"], "web")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["query"], "llm agent site:github.com")
        self.assertEqual(body["count"], 3)

    def test_missing_key_is_reported(self) -> None:
        with mock.patch.object(deepseek_web.settings, "web_search_key", return_value=""):
            result = deepseek_web._web_search({}, query="x")
        self.assertFalse(result.ok)
        self.assertIn("未配置", result.data["error"])

    def test_payment_error_not_retried(self) -> None:
        response = mock.Mock(status_code=402)
        with mock.patch.object(deepseek_web.settings, "web_search_key", return_value="sk-test"), \
                mock.patch.object(deepseek_web.httpx, "post", return_value=response) as post:
            result = deepseek_web._web_fetch({}, url="https://example.com")
        self.assertFalse(result.ok)
        self.assertIn("余额不足", result.data["error"])
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
