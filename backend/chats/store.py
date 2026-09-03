from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone

from backend.chats.database import initialize

STATUSES = {"active", "archived"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_conversation_id() -> str:
    return "chat_" + secrets.token_hex(6)


def new_message_id() -> str:
    return "msg_" + secrets.token_hex(6)


def ensure_schema(connection: sqlite3.Connection) -> None:
    initialize(connection)


def _conversation_dict(row: sqlite3.Row) -> dict:
    return {
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _message_dict(row: sqlite3.Row) -> dict:
    chunks = row["chunks_json"]
    tool_trace = row["tool_trace_json"]
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "chunks": json.loads(chunks) if chunks else [],
        "top_k": row["top_k"],
        "model": row["model"],
        "finish_reason": row["finish_reason"],
        "reasoning": row["reasoning"],
        "tool_trace": json.loads(tool_trace) if tool_trace else [],
        "error": row["error"],
        "created_at": row["created_at"],
    }


def _touch_conversation(connection: sqlite3.Connection, conversation_id: str, when: str) -> None:
    connection.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (when, conversation_id)
    )


def create_conversation(connection: sqlite3.Connection, title: str = "") -> dict:
    when = utc_now()
    conversation = {
        "conversation_id": new_conversation_id(),
        "title": title or "新对话",
        "status": "active",
        "created_at": when,
        "updated_at": when,
    }
    connection.execute(
        "INSERT INTO conversations (conversation_id, title, status, created_at, updated_at)"
        " VALUES (:conversation_id, :title, :status, :created_at, :updated_at)",
        conversation,
    )
    connection.commit()
    return conversation


def get_conversation(connection: sqlite3.Connection, conversation_id: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    return _conversation_dict(row) if row else None


def list_conversations(connection: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = connection.execute(
            "SELECT * FROM conversations WHERE status = ? ORDER BY updated_at DESC", (status,)
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [_conversation_dict(row) for row in rows]


def update_conversation(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
) -> dict:
    if status is not None and status not in STATUSES:
        raise ValueError(f"未知会话状态: {status}")
    existing = get_conversation(connection, conversation_id)
    if existing is None:
        raise KeyError(conversation_id)
    title = existing["title"] if title is None else (title or "新对话")
    status = status or existing["status"]
    connection.execute(
        "UPDATE conversations SET title = ?, status = ?, updated_at = ? WHERE conversation_id = ?",
        (title, status, utc_now(), conversation_id),
    )
    connection.commit()
    return get_conversation(connection, conversation_id)


def delete_conversation(connection: sqlite3.Connection, conversation_id: str) -> None:
    cursor = connection.execute(
        "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
    )
    if cursor.rowcount == 0:
        raise KeyError(conversation_id)
    connection.commit()


def add_message(
    connection: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str = "",
    *,
    chunks: list | None = None,
    top_k: int | None = None,
    model: str | None = None,
    finish_reason: str | None = None,
    reasoning: str | None = None,
    tool_trace: list | None = None,
    error: str | None = None,
) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError(f"未知消息角色: {role}")
    when = utc_now()
    message = {
        "message_id": new_message_id(),
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "chunks_json": json.dumps(chunks, ensure_ascii=False) if chunks else None,
        "top_k": top_k,
        "model": model,
        "finish_reason": finish_reason,
        "reasoning": reasoning,
        "tool_trace_json": json.dumps(tool_trace, ensure_ascii=False) if tool_trace else None,
        "error": error,
        "created_at": when,
    }
    connection.execute(
        "INSERT INTO messages (message_id, conversation_id, role, content, chunks_json,"
        " top_k, model, finish_reason, reasoning, tool_trace_json, error, created_at)"
        " VALUES (:message_id, :conversation_id, :role, :content, :chunks_json,"
        " :top_k, :model, :finish_reason, :reasoning, :tool_trace_json, :error, :created_at)",
        message,
    )
    _touch_conversation(connection, conversation_id, when)
    connection.commit()
    row = connection.execute(
        "SELECT * FROM messages WHERE message_id = ?", (message["message_id"],)
    ).fetchone()
    return _message_dict(row)


def get_message(connection: sqlite3.Connection, message_id: str) -> dict | None:
    row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    return _message_dict(row) if row else None


def list_messages(connection: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY rowid", (conversation_id,)
    ).fetchall()
    return [_message_dict(row) for row in rows]


def history_before(connection: sqlite3.Connection, conversation_id: str, before_rowid: int, limit: int = 4) -> list[dict]:
    """Return the most recent valid messages before a given message (for assembling chat history, in insertion order)."""
    rows = connection.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND rowid < ? AND content != ''"
        " ORDER BY rowid DESC LIMIT ?",
        (conversation_id, before_rowid, limit),
    ).fetchall()
    return [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(rows)
    ]


def message_rowid(connection: sqlite3.Connection, message_id: str) -> int | None:
    row = connection.execute(
        "SELECT rowid FROM messages WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row["rowid"] if row else None


def last_user_before(connection: sqlite3.Connection, conversation_id: str, before_rowid: int) -> dict | None:
    row = connection.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND rowid < ? AND role = 'user'"
        " ORDER BY rowid DESC LIMIT 1",
        (conversation_id, before_rowid),
    ).fetchone()
    return _message_dict(row) if row else None


def messages_after(connection: sqlite3.Connection, conversation_id: str, rowid: int) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND rowid > ? ORDER BY rowid",
        (conversation_id, rowid),
    ).fetchall()
    return [_message_dict(row) for row in rows]


def update_message_content(connection: sqlite3.Connection, message_id: str, content: str) -> dict:
    connection.execute(
        "UPDATE messages SET content = ?, created_at = ? WHERE message_id = ?",
        (content, utc_now(), message_id),
    )
    row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    return _message_dict(row)


def delete_messages_after(connection: sqlite3.Connection, conversation_id: str, rowid: int) -> int:
    cursor = connection.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND rowid > ?",
        (conversation_id, rowid),
    )
    return cursor.rowcount


def create_snapshot(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    label: str,
    source_message_id: str | None,
    messages: list[dict],
) -> dict:
    snapshot_id = f"snap_{secrets.token_hex(6)}"
    when = utc_now()
    connection.execute(
        "INSERT INTO message_snapshots (snapshot_id, conversation_id, label, source_message_id, messages_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (snapshot_id, conversation_id, label, source_message_id, json.dumps(messages, ensure_ascii=False), when),
    )
    return {
        "snapshot_id": snapshot_id,
        "conversation_id": conversation_id,
        "label": label,
        "source_message_id": source_message_id,
        "created_at": when,
    }


def list_snapshots(connection: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT snapshot_id, conversation_id, label, source_message_id, created_at,"
        " json_array_length(messages_json) AS message_count"
        " FROM message_snapshots WHERE conversation_id = ? ORDER BY created_at DESC",
        (conversation_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_snapshot(connection: sqlite3.Connection, snapshot_id: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM message_snapshots WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["messages"] = json.loads(result.pop("messages_json"))
    return result
