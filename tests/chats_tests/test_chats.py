from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.chats import service, store
from backend.chats.database import connect, initialize


def _fresh_conn():
    tmp = tempfile.TemporaryDirectory()
    conn = connect(Path(tmp.name) / "chats.sqlite")
    initialize(conn)
    return conn, tmp


def _fake_query_fn(answers: dict[str, dict] | None = None, fail: bool = False):
    calls: list[tuple[str, int, list[dict]]] = []

    def query_fn(search_query: str, top_k: int, history: list[dict]) -> dict:
        calls.append((search_query, top_k, list(history)))
        if fail:
            raise RuntimeError("无法连接 Qdrant：服务未启动")
        payload = (answers or {}).get(search_query) or {
            "answer": "回答 [1]。",
            "chunks": [{
                "chunk_id": "ape_a:0", "entity_id": "ape_a", "title": "论文A",
                "section": "## Abstract", "text": "片段文本", "score": 0.91,
            }],
            "model": "fake-model",
            "finish_reason": "stop",
            "reasoning": "先想再答",
        }
        return payload

    query_fn.calls = calls  # type: ignore[attr-defined]
    return query_fn


class ConversationStoreTests(unittest.TestCase):
    def test_create_list_update_delete(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn, "我的对话")
            self.assertEqual(store.list_conversations(conn, "active")[0]["title"], "我的对话")

            renamed = store.update_conversation(conn, conv["conversation_id"], title="改名")
            self.assertEqual(renamed["title"], "改名")

            store.update_conversation(conn, conv["conversation_id"], status="archived")
            self.assertEqual(store.list_conversations(conn, "active"), [])
            self.assertEqual(len(store.list_conversations(conn, "archived")), 1)

            store.delete_conversation(conn, conv["conversation_id"])
            self.assertEqual(store.list_conversations(conn), [])
            with self.assertRaises(KeyError):
                store.delete_conversation(conn, conv["conversation_id"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_messages_order_and_cascade(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            store.add_message(conn, cid, "user", content="第一问")
            store.add_message(conn, cid, "assistant", content="第一答",
                              chunks=[{"chunk_id": "x:0"}], top_k=5, model="m")
            store.add_message(conn, cid, "assistant", error="失败")
            messages = store.list_messages(conn, cid)
            self.assertEqual([m["role"] for m in messages], ["user", "assistant", "assistant"])
            self.assertEqual(messages[1]["chunks"][0]["chunk_id"], "x:0")
            self.assertEqual(messages[2]["error"], "失败")

            store.delete_conversation(conn, cid)
            self.assertEqual(store.list_messages(conn, cid), [])
        finally:
            conn.close()
            tmp.cleanup()

    def test_ingest_task_result_survives_refresh(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            store.create_ingest_task(conn, "task-1", conv["conversation_id"], ["e1"])
            store.finish_ingest_task(conn, "task-1", status="error",
                                     result={"requested": 1, "items": [{"reason_code": "download_timeout"}]},
                                     error="下载阶段超时")
            saved = store.get_ingest_task(conn, "task-1")
            self.assertEqual(saved["status"], "error")
            self.assertEqual(saved["result"]["items"][0]["reason_code"], "download_timeout")
            self.assertEqual(saved["error"], "下载阶段超时")
        finally:
            conn.close()
            tmp.cleanup()


class BuildSearchQueryTests(unittest.TestCase):
    def test_standalone_question_passthrough(self):
        history = [{"role": "user", "content": "事件相机超分有哪些方法"}]
        current = "EvTSR 在训练时使用了哪些损失函数进行监督"
        self.assertEqual(service.build_search_query(current, history), current)

    def test_pronoun_followup_combines_last_question(self):
        history = [{"role": "user", "content": "事件相机做文字超分有哪些方法"}, {"role": "assistant", "content": "EvTSR…"}]
        current = "它用了什么数据集？"
        self.assertEqual(
            service.build_search_query(current, history),
            "事件相机做文字超分有哪些方法 它用了什么数据集？",
        )

    def test_short_followup_combines(self):
        history = [{"role": "user", "content": "DiffBIR 的两阶段流程是什么"}]
        self.assertEqual(service.build_search_query("训练细节呢", history), "DiffBIR 的两阶段流程是什么 训练细节呢")

    def test_no_history_passthrough(self):
        self.assertEqual(service.build_search_query("它呢", []), "它呢")


class AskFlowTests(unittest.TestCase):
    def test_ask_success_and_auto_title(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn()
            msg = service.ask(conn, conversation_id=cid, query="事件相机超分有哪些方法？", top_k=5, query_fn=query_fn)
            self.assertEqual(msg["role"], "assistant")
            self.assertEqual(msg["chunks"][0]["entity_id"], "ape_a")
            self.assertEqual(msg["model"], "fake-model")
            self.assertEqual(msg["finish_reason"], "stop")
            self.assertEqual(msg["reasoning"], "先想再答")
            self.assertEqual(store.get_conversation(conn, cid)["title"], "事件相机超分有哪些方法？"[:20])
            self.assertEqual(store.list_messages(conn, cid)[0]["role"], "user")
        finally:
            conn.close()
            tmp.cleanup()

    def test_ask_truncated_answer_persists_finish_reason(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn(answers={"截断问题": {
                "answer": "回答到一半就", "chunks": [{"chunk_id": "ape_a:0", "entity_id": "ape_a",
                    "title": "A", "section": "", "text": "t", "score": 0.9}],
                "model": "fake-model", "finish_reason": "length", "reasoning": "",
            }})
            msg = service.ask(conn, conversation_id=cid, query="截断问题", query_fn=query_fn)
            self.assertEqual(msg["finish_reason"], "length")
            self.assertEqual(msg["content"], "回答到一半就")
        finally:
            conn.close()
            tmp.cleanup()

    def test_ask_followup_uses_history_for_generation(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn()
            service.ask(conn, conversation_id=cid, query="事件相机做文字超分有哪些方法", query_fn=query_fn)
            service.ask(conn, conversation_id=cid, query="它用了什么数据集？", query_fn=query_fn)
            second_call = query_fn.calls[1]
            search_query, _, history = second_call
            self.assertIn("事件相机做文字超分有哪些方法", search_query)
            self.assertIn("它用了什么数据集？", search_query)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0], {"role": "user", "content": "事件相机做文字超分有哪些方法"})
            self.assertEqual(history[1]["role"], "assistant")
        finally:
            conn.close()
            tmp.cleanup()

    def test_ask_zero_hits_answers_honestly(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn(answers={"库里斯拉夫是谁": {"answer": "", "chunks": [], "model": "m"}})
            msg = service.ask(conn, conversation_id=cid, query="库里斯拉夫是谁", query_fn=query_fn)
            self.assertEqual(msg["content"], "没有检索到相关内容。")
            self.assertEqual(msg["chunks"], [])
        finally:
            conn.close()
            tmp.cleanup()

    def test_ask_failure_persists_error_message(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn(fail=True)
            with self.assertRaises(RuntimeError):
                service.ask(conn, conversation_id=cid, query="随便问", query_fn=query_fn)
            messages = store.list_messages(conn, cid)
            self.assertEqual(messages[0]["role"], "user")
            self.assertIn("无法连接 Qdrant", messages[1]["error"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_ask_agent_mode(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]

            class FakeRun:
                answer = "搜到了 [1]。"
                finish_reason = "stop"
                reasoning = "先搜元数据"
                model = "fake-agent"
                ledger = [{"entity_id": "ape_a", "title": "A", "source": "catalog",
                           "text": "t", "section": "CVPR 2025"}]
                trace = [{"round": 1, "tool": "search_catalog", "args": {"query": "A"},
                          "ok": True, "summary": "命中 1 条", "n_results": 1, "duration_ms": 10}]

            def agent_fn(query, history, top_k):
                self.assertEqual(top_k, 5)
                return FakeRun()

            msg = service.ask(conn, conversation_id=cid, query="找 A 相关论文", mode="agent", agent_fn=agent_fn)
            self.assertEqual(msg["content"], "搜到了 [1]。")
            self.assertEqual(msg["chunks"][0]["source"], "catalog")
            self.assertEqual(len(msg["tool_trace"]), 1)
            self.assertEqual(msg["finish_reason"], "stop")
            self.assertEqual(msg["reasoning"], "先搜元数据")
            # readable back after persistence
            messages = store.list_messages(conn, cid)
            self.assertEqual(messages[1]["tool_trace"][0]["tool"], "search_catalog")
        finally:
            conn.close()
            tmp.cleanup()

    def test_agent_persists_only_answer_cited_evidence_and_warns_on_invalid_citation(self):
        """The ledger is an audit trail; cards must match the answer's valid [n] markers."""
        conn, tmp = _fresh_conn()
        try:
            cid = store.create_conversation(conn)["conversation_id"]

            class FakeRun:
                answer = "第二条证据支持结论 [2]；另见不存在的 [9]。"
                finish_reason = "stop"
                reasoning = ""
                model = "fake-agent"
                ledger = [
                    {"entity_id": "ape_a", "source": "catalog", "text": "第一条"},
                    {"entity_id": "ape_b", "source": "catalog", "text": "第二条"},
                    {"entity_id": "ape_c", "source": "catalog", "text": "第三条"},
                ]
                trace = []

            message = service.ask(
                conn, conversation_id=cid, query="测试引用", mode="agent",
                agent_fn=lambda *_: FakeRun(),
            )
            self.assertEqual([item["entity_id"] for item in message["chunks"]], ["ape_b"])
            self.assertIn("引用校验警告", message["content"])
            self.assertIn("[9]", message["content"])
        finally:
            conn.close()
            tmp.cleanup()

    def test_regenerate_reuses_chunks_without_retrieval(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            query_fn = _fake_query_fn()
            first = service.ask(conn, conversation_id=cid, query="问题一", query_fn=query_fn)

            seen: list[tuple[str, list, list]] = []

            def generate_fn(query, chunks, history):
                seen.append((query, chunks, history))
                return {"answer": "重新生成的回答 [1]。", "finish_reason": "stop", "reasoning": "", "model": "fake-model"}

            second = service.regenerate(conn, conversation_id=cid, message_id=first["message_id"], generate_fn=generate_fn)
            self.assertEqual(second["content"], "重新生成的回答 [1]。")
            self.assertEqual(second["finish_reason"], "stop")
            self.assertEqual(seen[0][0], "问题一")
            self.assertEqual(seen[0][1][0].id, "ape_a:0")
            self.assertEqual(query_fn.calls, [query_fn.calls[0]])
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

    def test_rewrite_truncates_and_snapshots(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            store.add_message(conn, cid, "user", content="原始问题A")
            store.add_message(conn, cid, "assistant", content="回答A")
            target = store.add_message(conn, cid, "user", content="要改的问题B")
            tail = store.add_message(conn, cid, "assistant", content="回答B")
            result = service.rewrite(conn, conversation_id=cid, message_id=target["message_id"], content="改后的问题B")
            self.assertEqual(result["truncated"], 1)  # only truncates answer B
            self.assertIsNotNone(result["snapshot"])
            # snapshot = old question B + answer B
            snap = store.get_snapshot(conn, result["snapshot"]["snapshot_id"])
            self.assertEqual([m["content"] for m in snap["messages"]], ["要改的问题B", "回答B"])
            # the conversation is now question A / answer A / rewritten question B; answer B is deleted
            msgs = store.list_messages(conn, cid)
            self.assertEqual([m["content"] for m in msgs], ["原始问题A", "回答A", "改后的问题B"])
            self.assertNotIn(tail["message_id"], [m["message_id"] for m in msgs])
            # snapshot list
            snaps = service.list_snapshots(conn, conversation_id=cid)
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["message_count"], 2)
        finally:
            conn.close()
            tmp.cleanup()

    def test_rewrite_last_message_no_snapshot(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            target = store.add_message(conn, cid, "user", content="唯一问题")
            result = service.rewrite(conn, conversation_id=cid, message_id=target["message_id"], content="改过")
            self.assertEqual(result["truncated"], 0)
            self.assertIsNone(result["snapshot"])
            self.assertEqual(store.list_messages(conn, cid)[0]["content"], "改过")
        finally:
            conn.close()
            tmp.cleanup()

    def test_rewrite_validation(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn)
            cid = conv["conversation_id"]
            assistant = store.add_message(conn, cid, "assistant", content="回答")
            with self.assertRaises(ValueError):
                service.rewrite(conn, conversation_id=cid, message_id=assistant["message_id"], content="x")
            with self.assertRaises(ValueError):
                service.rewrite(conn, conversation_id=cid, message_id=assistant["message_id"], content="  ")
            with self.assertRaises(KeyError):
                service.rewrite(conn, conversation_id=cid, message_id="nope", content="x")
        finally:
            conn.close()
            tmp.cleanup()

    def test_restore_snapshot_creates_copy(self):
        conn, tmp = _fresh_conn()
        try:
            conv = store.create_conversation(conn, title="原始标题")
            cid = conv["conversation_id"]
            store.add_message(conn, cid, "user", content="问题A")
            store.add_message(conn, cid, "assistant", content="回答A")
            target = store.add_message(conn, cid, "user", content="问题B")
            result = service.rewrite(conn, conversation_id=cid, message_id=target["message_id"], content="问题B改")
            snap = result["snapshot"]
            copy = service.restore_snapshot(conn, conversation_id=cid, snapshot_id=snap["snapshot_id"])
            self.assertNotEqual(copy["conversation_id"], cid)
            self.assertEqual(copy["title"], "原始标题")
            msgs = store.list_messages(conn, copy["conversation_id"])
            self.assertEqual([m["content"] for m in msgs], ["原始问题A", "回答A", "要改的问题B", "回答B"])
            # the original conversation is kept and truncated
            orig = store.list_messages(conn, cid)
            self.assertEqual([m["content"] for m in orig], ["原始问题A", "回答A", "问题B改"])
        finally:
            conn.close()
            tmp.cleanup()
