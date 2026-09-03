from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PARSED_ROOT = ROOT / "data" / "parsed"
DEFAULT_DATABASE = PARSED_ROOT / "parsed.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parsed_documents (
    entity_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'parsing', 'success', 'failed')),
    source_pdf_path TEXT NOT NULL DEFAULT '',
    markdown_path TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parsed_status ON parsed_documents(status);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parsed_dir(entity_id: str) -> Path:
    return PARSED_ROOT / entity_id


def parsed_path(entity_id: str) -> Path:
    return parsed_dir(entity_id) / f"{entity_id}.md"


def _file_exists(entity_id: str) -> bool:
    """Recognize both the new directory layout data/parsed/{id}/{id}.md and the old flat file data/parsed/{id}.md."""
    return parsed_path(entity_id).exists() or (PARSED_ROOT / f"{entity_id}.md").exists()


def connect(path: Path = DEFAULT_DATABASE, *, read_only: bool = False) -> sqlite3.Connection:
    path = path.resolve()
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


def mark(
    connection: sqlite3.Connection,
    entity_id: str,
    status: str,
    *,
    source_pdf_path: str = "",
    markdown_path: str = "",
    char_count: int = 0,
    error: str = "",
) -> None:
    now = utc_now()
    with connection:
        connection.execute(
            """INSERT INTO parsed_documents
               (entity_id, status, source_pdf_path, markdown_path, char_count, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 status=excluded.status, source_pdf_path=excluded.source_pdf_path,
                 markdown_path=excluded.markdown_path, char_count=excluded.char_count,
                 error=excluded.error, updated_at=excluded.updated_at""",
            (entity_id, status, source_pdf_path, markdown_path, char_count, error, now, now),
        )


def is_parsed(connection: sqlite3.Connection, entity_id: str) -> bool:
    row = connection.execute(
        "SELECT status FROM parsed_documents WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    # Only count as parsed when the manifest says success AND the Markdown file (new dir or old flat) actually exists
    return row is not None and row["status"] == "success" and _file_exists(entity_id)


def remove(connection: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    """Delete a parse result: remove the Markdown + image directory + manifest record."""
    row = connection.execute(
        "SELECT * FROM parsed_documents WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return {"entity_id": entity_id, "removed": False}
    with connection:
        import shutil

        # Remove both the new dir layout and the old flat file (compatible with historical parse results)
        shutil.rmtree(parsed_dir(entity_id), ignore_errors=True)
        (PARSED_ROOT / f"{entity_id}.md").unlink(missing_ok=True)
        connection.execute("DELETE FROM parsed_documents WHERE entity_id = ?", (entity_id,))
    return {"entity_id": entity_id, "removed": True}


def status_map(connection: sqlite3.Connection, entity_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    result: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT entity_id, status FROM parsed_documents WHERE entity_id IN ({placeholders})", chunk
        ).fetchall()
        for row in rows:
            status = row["status"]
            if status == "success" and not _file_exists(row["entity_id"]):
                status = ""  # File was deleted; treat as not parsed
            result[row["entity_id"]] = status
    return result
