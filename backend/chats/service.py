from __future__ import annotations

import re
import threading
from typing import Callable

from backend.chats import store
from raglib import Chunk

# Pronoun/ellipsis whitelist: if any of these appear, the follow-up needs context from earlier in the conversation
_PRONOUNS = (
    "它", "它们", "这个", "这些", "那个", "那些", "上述", "该", "此",
    "两者", "二者", "前者", "后者", "上面提到", "刚才", "里面", "其中",
)
_MIN_STANDALONE_LEN = 12
_HISTORY_LIMIT = 4
_CITATION_MARKER = re.compile(r"\[(\d+)\]")

# Serialize within the same session (locked by conversation_id); parallel across sessions
class _LockRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def get(self, conversation_id: str) -> threading.Lock:
        with self._lock:
            if conversation_id not in self._locks:
                self._locks[conversation_id] = threading.Lock()
            return self._locks[conversation_id]


_conv_locks = _LockRegistry()


def _cited_evidence(answer: str, ledger: list[dict]) -> tuple[list[dict], str]:
    """Select final-answer citations and make out-of-range markers visible.

    This validates only the [n] range; it deliberately does not claim that every
    generated fact is entailed by its cited evidence.
    """
    cited: list[int] = []
    invalid: list[int] = []
    for marker in _CITATION_MARKER.findall(answer or ""):
        index = int(marker)
        if 1 <= index <= len(ledger):
            if index not in cited:
                cited.append(index)
        elif index not in invalid:
            invalid.append(index)
    evidence = [{**ledger[index - 1], "citation_index": index} for index in cited]
    warning = ""
    if invalid:
        markers = "、".join(f"[{index}]" for index in invalid)
        warning = (
            f"\n\n> ⚠️ **引用校验警告**：回答包含越界引用 {markers}；"
            f"本轮证据账本共有 {len(ledger)} 条，未为这些引用展示证据卡。"
        )
    return evidence, warning


def build_search_query(current: str, history: list[dict]) -> str:
    """Build the retrieval query: standalone questions are used as-is; follow-ups containing
    pronouns or that are too short are concatenated with the previous user question.

    Honest note: this is a heuristic (v1). When v2 upgrades to LLM condense, only the internals
    of this function change; the signature and callers stay the same.
    """
    current = (current or "").strip()
    if not history:
        return current
    needs_context = len(current) < _MIN_STANDALONE_LEN or any(p in current for p in _PRONOUNS)
    if not needs_context:
        return current
    last_user = next(
        (m["content"] for m in reversed(history) if m["role"] == "user"), ""
    ).strip()
    if not last_user or last_user == current:
        return current
    return f"{last_user} {current}"


def _history_of(conn, conversation_id: str, before_rowid: int) -> list[dict]:
    return store.history_before(conn, conversation_id, before_rowid, _HISTORY_LIMIT)


def _default_query_fn() -> Callable[[str, int, list[dict]], dict]:
    from backend.rag import service as rag_service

    return rag_service.chat_query


def _default_generate_fn() -> Callable[[str, list[Chunk], list[dict]], dict]:
    from backend.rag import service as rag_service

    return rag_service.regenerate_answer


def _chunks_to_raglib(chunks: list[dict]) -> list[Chunk]:
    return [
        Chunk(
            id=item.get("chunk_id", f"{item.get('entity_id', '')}:{index}"),
            document_id=item.get("entity_id", ""),
            text=item.get("text", ""),
            metadata={
                "entity_id": item.get("entity_id", ""),
                "title": item.get("title", ""),
                "section": item.get("section", ""),
            },
        )
        for index, item in enumerate(chunks)
    ]


def _default_agent_fn(conversation_id: str = "", is_cancelled=None, scope=None) -> Callable[[str, list[dict], int], "AgentRun"]:
    from backend.agent import run_agent
    from backend.catalog.database import DEFAULT_DATABASE, connect as catalog_connect
    from backend.collections import database as collections_db
    from backend.library import store as download_store
    from backend.rag import parse_store, service as rag_service

    def agent_fn(query: str, history: list[dict], top_k: int):
        catalog_conn = catalog_connect(DEFAULT_DATABASE, read_only=True)
        collections_conn = None
        organization_draft_conn = None
        downloads_conn = None
        parsed_conn = None
        def draft_factory():
            connection = collections_db.connect()
            collections_db.initialize(connection)
            return connection

        if collections_db.DEFAULT_DATABASE.exists():
            collections_conn = collections_db.connect(read_only=True)
            # Keep the agent's collection view read-only.  A separate connection
            # is passed only to the draft tool; that tool can INSERT a draft but
            # exposes no collection-member mutation operation.
            organization_draft_conn = draft_factory()
        if download_store.DEFAULT_DATABASE.exists():
            downloads_conn = download_store.connect(read_only=True)
        if parse_store.DEFAULT_DATABASE.exists():
            parsed_conn = parse_store.connect(read_only=True)
        direct_assignment = re.fullmatch(r"(?:请)?把本地(?:全文)?库(?:所有|全部)论文加入[到]?\s*(.+?)[。！!]?", query.strip())
        document_chunk_counts, active_pipeline_fingerprint = (None, "") if direct_assignment else rag_service.index_reconcile_snapshot()
        try:
            # Keep authoritative local manifests in the tool context. This is read-only;
            # it avoids inferring full-text state from catalog metadata.
            ctx = {
                "catalog_conn": catalog_conn,
                "collections_conn": collections_conn,
                "organization_draft_conn": organization_draft_conn,
                "organization_draft_factory": draft_factory,
                "downloads_conn": downloads_conn,
                "parsed_conn": parsed_conn,
                "document_chunk_counts": document_chunk_counts,
                "pipeline_fingerprint": active_pipeline_fingerprint,
                "conversation_id": conversation_id,
                "query": query,
                "allowed_entity_ids": scope.get("entity_ids") if scope is not None else None,
                "pipeline": None,
            }
            if scope and scope.get("collection_id") and collections_conn is not None:
                from backend.research.store import list_records
                ctx["research_progress"] = [r["data"] for r in list_records(collections_conn)
                    if r["kind"] == "progress" and r["data"].get("collection_id") == scope["collection_id"]]
            if direct_assignment:
                from backend.agent.runner import AgentRun
                from backend.agent.tools.base import ToolRegistry
                from backend.agent.tools.organization import organization_tools
                args = {"target_name": direct_assignment.group(1).strip(), "allow_all": True}
                result = ToolRegistry(organization_tools()).dispatch("propose_collection_membership", args, ctx)
                return AgentRun(answer=("已生成指定加入草稿，请核对论文名单并确认。" if result.ok and result.data.get("suggestions") else
                                        "本地全文库没有可加入的论文。" if result.ok else result.data["error"]),
                    finish_reason="local_assignment", reasoning="", ledger=[], model="local",
                    trace=[{"tool": "propose_collection_membership", "args": args, "ok": result.ok,
                            "organization_run_id": result.data.get("organization_run_id", "")}])
            return run_agent(query=query, history=history, top_k=top_k,
                             ctx_builder=lambda: ctx, is_cancelled=is_cancelled)
        finally:
            catalog_conn.close()
            if collections_conn is not None:
                collections_conn.close()
            draft_conn = ctx.get("organization_draft_conn")
            if draft_conn is not None:
                draft_conn.close()
            if downloads_conn is not None:
                downloads_conn.close()
            if parsed_conn is not None:
                parsed_conn.close()

    return agent_fn


def ask(
    conn,
    *,
    conversation_id: str,
    query: str,
    top_k: int = 5,
    query_fn: Callable[[str, int, list[dict]], dict] | None = None,
    agent_fn: Callable[[str, list[dict], int], "AgentRun"] | None = None,
    mode: str = "pipeline",
    scope: dict | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict:
    """One round of Q&A: persist the user message → retrieval/tool loop → persist the assistant message.

    mode="pipeline": fixed pipeline (retrieval-rewrite heuristic + single generation), query_fn injectable;
    mode="agent": agent tool loop (DeepSeek function calling), agent_fn injectable.
    Failure semantics: if retrieval/generation raises, an assistant message with a non-empty error is
    appended and the exception is re-raised; the already-persisted conversation history stays intact,
    so the frontend can retry the failed round.
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("问题不能为空")
    conversation = store.get_conversation(conn, conversation_id)
    if conversation is None:
        raise KeyError(conversation_id)
    if mode not in ("pipeline", "agent"):
        raise ValueError(f"未知问答模式: {mode}")

    with _conv_locks.get(conversation_id):
        user_message = store.add_message(conn, conversation_id, "user", content=query)
        if conversation["title"] in ("", "新对话"):
            store.update_conversation(conn, conversation_id, title=query[:20])
        history = _history_of(conn, conversation_id, store.message_rowid(conn, user_message["message_id"]))

        if mode == "agent":
            try:
                run = (agent_fn or _default_agent_fn(conversation_id, is_cancelled, scope))(query, history, top_k)
            except Exception as exc:
                store.add_message(conn, conversation_id, "assistant", error=str(exc))
                raise
            ledger = run.ledger
            cited_ledger, citation_warning = _cited_evidence(run.answer or "", ledger)
            content = (run.answer or "") + citation_warning
            if cited_ledger:
                assistant_message = store.add_message(
                    conn, conversation_id, "assistant",
                    content=content, chunks=cited_ledger, top_k=top_k,
                    model=run.model, finish_reason=run.finish_reason,
                    reasoning=run.reasoning, tool_trace=run.trace,
                )
            else:
                assistant_message = store.add_message(
                    conn, conversation_id, "assistant",
                    content=content or "未检索到相关内容。",
                    finish_reason=run.finish_reason, reasoning=run.reasoning,
                    tool_trace=run.trace,
                )
            return assistant_message

        query_fn = query_fn or _default_query_fn()
        search_query = build_search_query(query, history)
        try:
            result = query_fn(search_query, top_k, history)
        except Exception as exc:
            store.add_message(conn, conversation_id, "assistant", error=str(exc))
            raise
        chunks = result.get("chunks") or []
        cited_chunks, citation_warning = _cited_evidence(result.get("answer") or "", chunks)
        content = (result.get("answer") or "") + citation_warning
        if cited_chunks:
            assistant_message = store.add_message(
                conn,
                conversation_id,
                "assistant",
                content=content,
                chunks=cited_chunks,
                top_k=top_k,
                model=result.get("model"),
                finish_reason=result.get("finish_reason"),
                reasoning=result.get("reasoning"),
            )
        else:
            assistant_message = store.add_message(
                conn,
                conversation_id,
                "assistant",
                content=content or "没有检索到相关内容。",
                top_k=top_k,
                model=result.get("model"),
                finish_reason=result.get("finish_reason"),
                reasoning=result.get("reasoning"),
            )
    return assistant_message


def regenerate(
    conn,
    *,
    conversation_id: str,
    message_id: str,
    generate_fn: Callable[[str, list[Chunk], list[dict]], dict] | None = None,
) -> dict:
    """Regenerate the answer: reuse the round's stored evidence chunks to re-invoke the generator (no re-retrieval), persisting the result as a new message."""
    conversation = store.get_conversation(conn, conversation_id)
    if conversation is None:
        raise KeyError(conversation_id)
    message = store.get_message(conn, message_id)
    if message is None or message["conversation_id"] != conversation_id:
        raise KeyError(message_id)
    if message["role"] != "assistant":
        raise ValueError("只能对回答重新生成")
    chunks = message.get("chunks") or []
    if not chunks:
        raise ValueError("该消息没有已存证据，请重新提问")

    generate_fn = generate_fn or _default_generate_fn()
    rowid = store.message_rowid(conn, message_id)
    last_user = store.last_user_before(conn, conversation_id, rowid)
    if last_user is None:
        raise ValueError("找不到该回答对应的提问")
    history = _history_of(conn, conversation_id, rowid)
    with _conv_locks.get(conversation_id):
        result = generate_fn(last_user["content"], _chunks_to_raglib(chunks), history)
        cited_chunks, citation_warning = _cited_evidence(result.get("answer") or "", chunks)
        new_message = store.add_message(
            conn,
            conversation_id,
            "assistant",
            content=(result.get("answer") or "") + citation_warning,
            chunks=cited_chunks,
            top_k=message.get("top_k"),
            model=result.get("model"),
            finish_reason=result.get("finish_reason"),
            reasoning=result.get("reasoning"),
        )
    return new_message


def rewrite(conn, *, conversation_id: str, message_id: str, content: str) -> dict:
    """Edit-and-replay (truncate + snapshot archive):
    Update the given user message's content, snapshot all later messages, then truncate (physically delete) them.
    Returns {message, truncated, snapshot}. Re-sending is done by the caller through ask.
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("修改后内容不能为空")
    message = store.get_message(conn, message_id)
    if message is None or message["conversation_id"] != conversation_id:
        raise KeyError(message_id)
    if message["role"] != "user":
        raise ValueError("只能编辑用户消息")

    rowid = store.message_rowid(conn, message_id)
    with _conv_locks.get(conversation_id):
        old_message = store.get_message(conn, message_id)
        tail = store.messages_after(conn, conversation_id, rowid)
        snapshot = None
        if tail:
            # Snapshot = the full conversation before the edit (the old question + all messages after it)
            snapshot_messages = [old_message, *tail]
            snapshot = store.create_snapshot(
                conn, conversation_id,
                label=f"编辑前 · {len(snapshot_messages)} 条消息",
                source_message_id=message_id,
                messages=snapshot_messages,
            )
        updated = store.update_message_content(conn, message_id, content)
        truncated = store.delete_messages_after(conn, conversation_id, rowid)
        conn.commit()
    return {"message": updated, "truncated": truncated, "snapshot": snapshot}


def list_snapshots(conn, *, conversation_id: str) -> list[dict]:
    return store.list_snapshots(conn, conversation_id)


def get_snapshot(conn, *, conversation_id: str, snapshot_id: str) -> dict:
    snapshot = store.get_snapshot(conn, snapshot_id)
    if snapshot is None or snapshot["conversation_id"] != conversation_id:
        raise KeyError(snapshot_id)
    return snapshot


def restore_snapshot(conn, *, conversation_id: str, snapshot_id: str) -> dict:
    """Restore a snapshot as a new conversation copy (the original is kept; the copy can continue the conversation)."""
    snapshot = store.get_snapshot(conn, snapshot_id)
    if snapshot is None or snapshot["conversation_id"] != conversation_id:
        raise KeyError(snapshot_id)
    conversation = store.get_conversation(conn, conversation_id)
    with _conv_locks.get(conversation_id):
        copy = store.create_conversation(conn, title=(conversation or {}).get("title", "") or "历史版本副本")
        for message in snapshot["messages"]:
            store.add_message(
                conn, copy["conversation_id"], message["role"],
                content=message.get("content") or "",
                chunks=message.get("chunks"),
                top_k=message.get("top_k"),
                model=message.get("model"),
                finish_reason=message.get("finish_reason"),
                reasoning=message.get("reasoning"),
                tool_trace=message.get("tool_trace"),
                error=message.get("error"),
            )
        conn.commit()
    return copy
