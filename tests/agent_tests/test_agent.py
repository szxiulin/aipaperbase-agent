from __future__ import annotations

import json
import sqlite3
import tempfile
import urllib.parse
import unittest
from pathlib import Path

import backend.agent.runner as runner_mod
from backend.agent.tools.catalog import catalog_tools
from backend.agent.tools.fulltext import fulltext_tools
from backend.agent.runner import run_agent
from backend.agent.tools.base import ToolDef, ToolResult, ToolRegistry
from backend.catalog import queries


def _catalog_conn() -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    conn = sqlite3.connect(Path(tmp.name) / "catalog.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE paper_records (record_id TEXT PRIMARY KEY, paper_id TEXT, venue TEXT,
            venue_type TEXT, year INTEGER, title TEXT, authors TEXT, abstract TEXT, doi TEXT);
        CREATE TABLE paper_entities (entity_id TEXT PRIMARY KEY, canonical_title TEXT,
            canonical_record_id TEXT, first_year INTEGER, last_year INTEGER, record_count INTEGER);
        CREATE TABLE entity_memberships (record_id TEXT, entity_id TEXT);
        CREATE TABLE topic_definitions (topic_id TEXT PRIMARY KEY, name TEXT);
        -- v2 口径：assignment 只落叶节点并带 role，按主题名过滤经 topic_ancestors 上卷
        CREATE TABLE entity_topic_assignments (entity_id TEXT, topic_id TEXT, role TEXT);
        CREATE TABLE topic_ancestors (topic_id TEXT, ancestor_topic_id TEXT, depth INTEGER);
        INSERT INTO paper_records VALUES
            ('r1','p1','CVPR', 'conference', 2025, 'A Diffusion Baseline for SR', 'Zhang', 'We propose...', '10.1/x'),
            ('r2','p2','AAAI', 'conference', 2026, 'Event-Guided SR', 'Li', 'Events enhance...', '10.2/x'),
            ('r3','p3','CVPR', 'conference', 2025, 'Zero-shot SR Framework', 'Wang', 'ZS framework...', '10.3/x');
        INSERT INTO paper_entities VALUES
            ('ape_a','A Diffusion Baseline for SR','r1',2025,2025,1),
            ('ape_b','Event-Guided SR','r2',2026,2026,1),
            ('ape_c','Zero-shot SR Framework','r3',2025,2025,1);
        INSERT INTO entity_memberships VALUES ('r1','ape_a'),('r2','ape_b'),('r3','ape_c');
        INSERT INTO topic_definitions VALUES ('t1','超分辨率'),('t2','图像恢复');
        INSERT INTO entity_topic_assignments VALUES
            ('ape_a','t1','primary'),('ape_b','t1','primary'),('ape_c','t2','primary');
        -- topic_ancestors 含自身 depth=0（叶名过滤经 self 行命中；与真库 build_topic_analysis 语义一致）
        INSERT INTO topic_ancestors VALUES ('t1','t1',0),('t2','t2',0);
    """)
    conn.commit()
    return conn, tmp


class FakeEmbedder:
    model = "fake"
    dim = 4

    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3, 0.4]] * len(texts)

    def embed_query(self, text):
        return [0.1, 0.2, 0.3, 0.4]


class ToolRegistryTests(unittest.TestCase):
    def test_dispatch_ok_and_unknown(self):
        def handler(ctx, x=1):
            return ToolResult(ok=True, data={"x": x}, summary="ok")
        registry = ToolRegistry([ToolDef(
            name="ping", description="", parameters={}, handler=handler)])
        self.assertTrue(registry.dispatch("ping", {"x": 2}, {}).ok)
        bad = registry.dispatch("nope", {}, {})
        self.assertFalse(bad.ok)
        self.assertIn("未知工具", bad.data["error"])
        bad_args = registry.dispatch("ping", [1, 2], {})
        self.assertFalse(bad_args.ok)

    def test_dispatch_tool_exception_injected(self):
        def handler(ctx):
            raise RuntimeError("boom")
        from backend.agent.tools.base import ToolDef
        registry = ToolRegistry([ToolDef(name="boom", description="", parameters={}, handler=handler)])
        result = registry.dispatch("boom", {}, {})
        self.assertFalse(result.ok)
        self.assertIn("boom", result.data["error"])


class SearchCatalogTests(unittest.TestCase):
    def test_filters_sort_and_aggregate(self):
        conn, tmp = _catalog_conn()
        try:
            r = queries.search_papers(conn, query="SR", sort="relevance", page_size=10)
            self.assertEqual(r["total"], 3)
            r2 = queries.search_papers(conn, venues=["CVPR"], years=[2025], page_size=10)
            self.assertEqual(r2["total"], 2)
            r3 = queries.search_papers(conn, topics=["超分辨率"], page_size=10)
            self.assertEqual(r3["total"], 2)
            self.assertEqual(r3["items"][0]["entity_id"], "ape_b")  # AAAI 2026 comes first with year DESC
            # List mode: page_size=0 + aggregate
            r4 = queries.search_papers(conn, topics=["超分辨率"], page_size=0, aggregate=["venue", "year"])
            self.assertEqual(r4["total"], 2)
            self.assertEqual(r4["items"], [])
            self.assertEqual(r4["facets"]["venue"], {"AAAI": 1, "CVPR": 1})
            # relevance: title matches should be ranked first
            r5 = queries.search_papers(conn, query="Event-Guided", sort="relevance", page_size=5)
            self.assertEqual(r5["items"][0]["entity_id"], "ape_b")
        finally:
            conn.close()
            tmp.cleanup()

    def test_tool_produces_provenance_and_facets(self):
        conn, tmp = _catalog_conn()
        try:
            tool = catalog_tools()[0]
            result = tool.handler({"catalog_conn": conn}, query="Event", page_size=5)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["total"], 1)
            self.assertEqual(result.provenance[0]["source"], "catalog")
            self.assertIn("section", result.provenance[0])
            # list mode goes through the handler
            stats = tool.handler({"catalog_conn": conn}, filters={"topics": ["超分辨率"]}, page_size=0, aggregate=["year"])
            self.assertEqual(stats.data["total"], 2)
            self.assertIn("year", stats.data["facets"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_list_facets(self):
        conn, tmp = _catalog_conn()
        try:
            tools = {t.name: t for t in catalog_tools()}
            venues = tools["list_facets"].handler({"catalog_conn": conn}, type="venues")
            self.assertEqual(venues.data["items"], ["AAAI", "CVPR"])
            topics = tools["list_facets"].handler({"catalog_conn": conn}, type="topics")
            self.assertIn("超分辨率", topics.data["items"])
        finally:
            conn.close()
            tmp.cleanup()


class FulltextToolsTests(unittest.TestCase):
    """Section map + evidence retrieval for fulltext tools (based on InMemoryStore + FakeEmbedder)."""

    def _pipeline_with_paper(self):
        from raglib import InMemoryStore, MarkdownSplitter, Pipeline
        from raglib.documents import Document
        from backend.agent.tools.fulltext import fulltext_tools
        embedder = FakeEmbedder()
        store = InMemoryStore()
        pipeline = Pipeline(MarkdownSplitter(), embedder, store, generator=None)
        # Ingest a full paper (multiple sections)
        doc = Document(
            id="ape_demo",
            text=(
                "# Abstract\n\n这是摘要段落说明本文研究事件引导超分。\n\n"
                "## Method\n\n我们提出了 EGH + CMF 两阶段融合方法。\n\n"
                "## Method\n\n在此基础上加入文本特征编码器。\n\n"
                "## Experiment\n\n在 TextZoom 数据集上 PSNR 提升 1.2dB。"
            ),
            metadata={"title": "Demo SR Paper", "entity_id": "ape_demo"},
        )
        pipeline.ingest([doc])
        ctx = {"catalog_conn": None, "collections_conn": None, "pipeline": pipeline}
        tools = {t.name: t for t in fulltext_tools()}
        return ctx, tools

    def test_get_evidence_returns_section_map(self):
        ctx, tools = self._pipeline_with_paper()
        result = tools["get_evidence"].handler(ctx, entity_id="ape_demo")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["entity_id"], "ape_demo")
        sections = result.data["sections"]
        # Abstract + Method + Experiment (MarkdownSplitter behavior)
        section_names = {s["section"] for s in sections}
        self.assertTrue(any("Abstract" in n for n in section_names))
        self.assertTrue(any("Method" in n for n in section_names))
        # Each section has a first paragraph + chunk_count
        for section in sections:
            self.assertIn("text", section)
            self.assertIn("chunk_count", section)
            self.assertGreaterEqual(section["chunk_count"], 1)

    def test_get_evidence_not_ingested_hints_to_ingest(self):
        from raglib import InMemoryStore, MarkdownSplitter, Pipeline
        from backend.agent.tools.fulltext import fulltext_tools
        pipeline = Pipeline(MarkdownSplitter(), FakeEmbedder(), InMemoryStore(), generator=None)
        ctx = {"catalog_conn": None, "collections_conn": None, "pipeline": pipeline}
        get_evidence = {t.name: t for t in fulltext_tools()}["get_evidence"]
        result = get_evidence.handler(ctx, entity_id="not_ingested")
        self.assertFalse(result.ok)
        self.assertIn("未入库", result.data.get("error", ""))
        self.assertIn("ingest", result.data.get("hint", ""))

    def test_search_evidence_limits_results(self):
        ctx, tools = self._pipeline_with_paper()
        result = tools["search_evidence"].handler(ctx, query="EGH CMF", top_k=2)
        self.assertTrue(result.ok)
        self.assertLessEqual(result.data["total"], 2)
        for item in result.data["items"]:
            self.assertIn("chunk_id", item)
            self.assertIn("score", item)

    def test_empty_collection_scope_does_not_search_entire_store(self):
        ctx, tools = self._pipeline_with_paper()
        collections = sqlite3.connect(":memory:")
        collections.row_factory = sqlite3.Row
        collections.execute("CREATE TABLE collection_members (collection_id TEXT, entity_id TEXT)")
        ctx["collections_conn"] = collections
        try:
            result = tools["search_evidence"].handler(
                ctx, query="EGH CMF", collection_id="empty", top_k=2
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.data["items"], [])
            self.assertIn("限定论文内", result.summary)
        finally:
            collections.close()


class LoopTests(unittest.TestCase):
    def _ctx(self):
        conn, tmp = _catalog_conn()
        from raglib import InMemoryStore, Pipeline, MarkdownSplitter
        from raglib.embed import Embedder  # noqa: F401
        store = InMemoryStore()
        pipeline = Pipeline(MarkdownSplitter(), FakeEmbedder(), store, generator=None)
        return {"catalog_conn": conn, "collections_conn": None, "pipeline": pipeline}, (conn, tmp)

    def _scripted(self, responses):
        state = {"calls": []}

        def fake_llm(client, **kw):
            state["calls"].append(kw["messages"])
            return responses.pop(0)

        return fake_llm, state

    def _message(self, **overrides):
        msg = {"role": "assistant", "content": "", "reasoning_content": "", "tool_calls": None}
        msg.update(overrides)
        return msg

    def _resp(self, message, finish_reason="stop"):
        return {"choices": [{"message": message, "finish_reason": finish_reason}], "model": "fake"}

    def test_agent_single_tool_round(self):
        ctx, (conn, tmp) = self._ctx()
        try:
            call = {
                "id": "call_1",
                "function": {"name": "search_papers", "arguments": json.dumps({"query": "Event"})},
            }
            responses = [
                self._resp(self._message(tool_calls=[call], reasoning_content="先搜元数据"), "tool_calls"),
                self._resp(self._message(content="找到了 **Event-Guided SR** [1]。"), "stop"),
            ]
            fake_llm, state = self._scripted(responses)
            orig = runner_mod.llm_chat
            runner_mod.llm_chat = fake_llm
            try:
                run = run_agent(query="有哪些事件相关论文？", ctx_builder=lambda: ctx, config=_FakeConfig())
            finally:
                runner_mod.llm_chat = orig
            self.assertEqual(run.answer, "找到了 **Event-Guided SR** [1]。")
            self.assertEqual(run.finish_reason, "stop")
            self.assertEqual(len(run.ledger), 1)
            self.assertEqual(run.ledger[0]["source"], "catalog")
            self.assertEqual(len(run.trace), 3)  # round1 + tool + round2
            # Return rule: assistant messages must include reasoning_content
            assistant_msgs = [m for m in state["calls"][1] if m.get("tool_calls")]
            self.assertTrue(all("reasoning_content" in m for m in assistant_msgs))
            # tool messages are present
            self.assertTrue(any(m.get("role") == "tool" for m in state["calls"][1]))
            tool_message = next(m for m in state["calls"][1] if m.get("role") == "tool")
            self.assertEqual(json.loads(tool_message["content"])["items"][0]["citation_index"], 1)
        finally:
            conn.close()
            tmp.cleanup()

    def test_repeated_evidence_keeps_one_stable_citation_number(self):
        evidence = {"source": "catalog", "entity_id": "e1", "title": "Paper", "text": "Fact"}
        ledger = []
        first = runner_mod._record_evidence(ledger, [evidence], {"items": [{"title": "Paper"}]})
        second = runner_mod._record_evidence(ledger, [dict(evidence)], {"items": [{"title": "Paper"}]})
        self.assertEqual(len(ledger), 1)
        self.assertEqual(first["items"][0]["citation_index"], 1)
        self.assertEqual(second["items"][0]["citation_index"], 1)

    def test_agent_max_rounds_falls_back_to_synthesis(self):
        ctx, (conn, tmp) = self._ctx()
        try:
            call = {"id": "call_1", "function": {"name": "list_collections", "arguments": "{}"}}
            responses = [self._resp(self._message(tool_calls=[call]), "tool_calls")] * 2
            responses.append(self._resp(self._message(content="综合回答 [1]。"), "stop"))
            fake_llm, _ = self._scripted(responses)
            orig = runner_mod.llm_chat
            runner_mod.llm_chat = fake_llm
            try:
                run = run_agent(query="q", max_rounds=2, ctx_builder=lambda: ctx, config=_FakeConfig())
            finally:
                runner_mod.llm_chat = orig
            self.assertEqual(run.finish_reason, "agent_final_synthesis")
            self.assertEqual(run.answer, "综合回答 [1]。")
            self.assertEqual(len(run.trace), 4)  # 2 round + 2 tool
        finally:
            conn.close()
            tmp.cleanup()

    def test_final_synthesis_never_exposes_raw_dsml(self):
        ctx, (conn, tmp) = self._ctx()
        try:
            call = {"id": "call_1", "function": {"name": "list_collections", "arguments": "{}"}}
            responses = [self._resp(self._message(tool_calls=[call]), "tool_calls")] * 2
            responses.append(self._resp(self._message(content='<｜｜DSML｜｜tool_calls>'), "stop"))
            fake_llm, _ = self._scripted(responses)
            orig = runner_mod.llm_chat
            runner_mod.llm_chat = fake_llm
            try:
                run = run_agent(query="q", max_rounds=2, ctx_builder=lambda: ctx, config=_FakeConfig())
            finally:
                runner_mod.llm_chat = orig
            self.assertEqual(run.finish_reason, "agent_final_synthesis")
            self.assertIn("未解析的工具调用协议", run.answer)
            self.assertNotIn("DSML", run.answer)
        finally:
            conn.close()
            tmp.cleanup()

    def test_agent_cancelled_between_rounds(self):
        ctx, (conn, tmp) = self._ctx()
        try:
            call = {"id": "call_1", "function": {"name": "list_collections", "arguments": "{}"}}
            responses = [
                self._resp(self._message(tool_calls=[call]), "tool_calls"),
                self._resp(self._message(content="正常回答"), "stop"),
            ]
            fake_llm, _ = self._scripted(responses)
            orig = runner_mod.llm_chat
            runner_mod.llm_chat = fake_llm
            cancelled = {"value": False}
            try:
                run = run_agent(query="q", max_rounds=5, ctx_builder=lambda: ctx,
                                config=_FakeConfig(), is_cancelled=lambda: cancelled["value"])
                cancelled["value"] = True
                run2 = run_agent(query="q2", max_rounds=5, ctx_builder=lambda: ctx,
                                 config=_FakeConfig(), is_cancelled=lambda: cancelled["value"])
            finally:
                runner_mod.llm_chat = orig
            self.assertEqual(run.finish_reason, "stop")  # completed normally before cancellation
            self.assertEqual(run2.finish_reason, "agent_cancelled")
            self.assertEqual(run2.answer, "")
        finally:
            conn.close()
            tmp.cleanup()

    def test_history_strips_reasoning(self):
        ctx, (conn, tmp) = self._ctx()
        try:
            responses = [self._resp(self._message(content="好的"))]
            fake_llm, state = self._scripted(responses)
            orig = runner_mod.llm_chat
            runner_mod.llm_chat = fake_llm
            try:
                run_agent(
                    query="追问",
                    history=[{"role": "assistant", "content": "上一答", "reasoning_content": "应该被剥掉"}],
                    ctx_builder=lambda: ctx, config=_FakeConfig(),
                )
            finally:
                runner_mod.llm_chat = orig
            first_call = state["calls"][0]
            history_msgs = [m for m in first_call if m.get("role") == "assistant"]
            self.assertEqual(len(history_msgs), 1)
            self.assertNotIn("reasoning_content", history_msgs[0])
            self.assertEqual(first_call[0]["role"], "system")
            self.assertEqual(first_call[-1]["content"], "追问")
        finally:
            conn.close()
            tmp.cleanup()


class _FakeConfig:
    class generator:
        base_url = "https://fake"
        api_key = "k"
        model = "fake-model"
        thinking = "disabled"
        reasoning_effort = "low"
        max_tokens = 100


if __name__ == "__main__":
    unittest.main()


class ExecToolsTests(unittest.TestCase):
    def test_ingest_papers_returns_plan_only(self):
        from backend.agent.tools.exec import exec_tools
        tools = {t.name: t for t in exec_tools()}
        conn, tmp = _catalog_conn()
        try:
            result = tools["ingest_papers"].handler({"catalog_conn": conn}, entity_ids=["ape_a", "ape_b"])
            self.assertTrue(result.ok)
            plan = result.data["plan"]
            self.assertEqual(plan["total"], 2)
            self.assertNotIn("steps", plan)  # local plan has not established executable stages yet
            self.assertTrue(plan["confirm_required"])
            titles = {p["entity_id"]: p["title"] for p in plan["papers"]}
            self.assertEqual(titles["ape_a"], "A Diffusion Baseline for SR")
            # Contract: never actually execute (no side effects, just return the plan)
            self.assertIn("confirm_required", result.data["plan"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_ingest_papers_empty_rejected(self):
        from backend.agent.tools.exec import exec_tools
        tools = {t.name: t for t in exec_tools()}
        result = tools["ingest_papers"].handler({"catalog_conn": None}, entity_ids=[])
        self.assertFalse(result.ok)


class WebToolsTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import socket

        self.enterContext(patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        ]))

    def test_url_validation_blocks_internal(self):
        from backend.agent.tools.web import _valid_http_url
        blocked = ["http://localhost:8765/x", "file:///etc/passwd", "http://127.0.0.1/x",
                   "http://10.0.0.5/x", "http://192.168.1.1/x", "ftp://a.com/x", "http://a"]
        allowed = ["https://arxiv.org/abs/2501.00001", "http://example.com/a?b=1"]
        for url in blocked:
            self.assertFalse(_valid_http_url(url), url)
        for url in allowed:
            self.assertTrue(_valid_http_url(url), url)

    def test_jump_url_parsing(self):
        from backend.agent.tools.web import _extract_real_url
        import base64
        real = "https://arxiv.org/abs/2501.00001"
        b64 = base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
        self.assertEqual(_extract_real_url("https://lite.duckduckgo.com/lite/?uddg=" + urllib.parse.quote(real)), real)
        self.assertEqual(_extract_real_url("https://www.bing.com/ck/a?!&&p=x&u=" + b64), real)
        self.assertEqual(_extract_real_url("https://example.com/direct"), "https://example.com/direct")

    def test_budget_limits_calls(self):
        from backend.agent.tools.web import web_tools
        tools = {t.name: t for t in web_tools()}
        ctx = {"web_budget": {"used": 3, "max": 3}}
        r = tools["browser_fetch_page"].handler(ctx, url="https://example.com")
        self.assertFalse(r.ok)
        self.assertIn("上限", r.data["error"])
