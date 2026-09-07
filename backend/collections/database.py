from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USER_ROOT = ROOT / "data" / "user"
DEFAULT_DATABASE = USER_ROOT / "collections" / "collections.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collections (
    collection_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'filter', 'topic', 'mixed')),
    source_snapshot TEXT NOT NULL DEFAULT '',
    catalog_release_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_members (
    collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    added_by TEXT NOT NULL CHECK (added_by IN ('manual', 'filter', 'topic')),
    note TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_members_entity ON collection_members(entity_id);

CREATE TABLE IF NOT EXISTS collection_organization_runs (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    target_collection_ids_json TEXT NOT NULL,
    rule_snapshot_json TEXT NOT NULL,
    suggestions_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'applied', 'expired', 'failed')),
    classifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_organization_runs_conversation ON collection_organization_runs(conversation_id, created_at DESC);
"""


def connect(path: Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    path = (path or DEFAULT_DATABASE).resolve()
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(collection_organization_runs)")}
    if "result_json" not in columns:
        connection.execute("ALTER TABLE collection_organization_runs ADD COLUMN result_json TEXT NOT NULL DEFAULT ''")
    connection.commit()
