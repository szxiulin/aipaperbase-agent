from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USER_ROOT = ROOT / "data" / "user"
DEFAULT_DATABASE = USER_ROOT / "chats" / "chats.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL DEFAULT '',
    chunks_json     TEXT,
    top_k           INTEGER,
    model           TEXT,
    finish_reason   TEXT,
    reasoning       TEXT,
    tool_trace_json TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS message_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    label             TEXT NOT NULL DEFAULT '',
    source_message_id TEXT,
    messages_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_tasks (
    task_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    requested_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ingest_tasks_conversation ON ingest_tasks(conversation_id, started_at);
"""

# Incremental column migration for older databases (SQLite does not support ADD COLUMN IF NOT EXISTS)
_MIGRATIONS = {
    "finish_reason": "ALTER TABLE messages ADD COLUMN finish_reason TEXT",
    "reasoning": "ALTER TABLE messages ADD COLUMN reasoning TEXT",
    "tool_trace_json": "ALTER TABLE messages ADD COLUMN tool_trace_json TEXT",
}


def connect(path: Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path or DEFAULT_DATABASE).resolve()
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _existing_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    existing = _existing_columns(connection, "messages")
    for column, ddl in _MIGRATIONS.items():
        if column not in existing:
            connection.execute(ddl)
    connection.commit()
