from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PAPERS_ROOT = ROOT / "data" / "papers"
DEFAULT_DATABASE = PAPERS_ROOT / "downloads.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS downloads (
    entity_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'downloading', 'success', 'failed', 'duplicate')),
    source TEXT NOT NULL DEFAULT '',
    download_url TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    duplicate_of TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_downloads_sha256 ON downloads(sha256);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def paper_path(entity_id: str) -> Path:
    return PAPERS_ROOT / f"{entity_id}.pdf"


def connect(path: Path = DEFAULT_DATABASE, *, read_only: bool = False) -> sqlite3.Connection:
    path = path.resolve()
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
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
    source: str = "",
    download_url: str = "",
    sha256: str = "",
    file_path: str = "",
    bytes: int = 0,
    duplicate_of: str = "",
    error: str = "",
) -> None:
    now = utc_now()
    with connection:
        connection.execute(
            """INSERT INTO downloads
               (entity_id, status, source, download_url, sha256, file_path, bytes,
                duplicate_of, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 status=excluded.status, source=excluded.source, download_url=excluded.download_url,
                 sha256=excluded.sha256, file_path=excluded.file_path, bytes=excluded.bytes,
                 duplicate_of=excluded.duplicate_of, error=excluded.error, updated_at=excluded.updated_at""",
            (entity_id, status, source, download_url, sha256, file_path, bytes,
             duplicate_of, error, now, now),
        )


def is_success(connection: sqlite3.Connection, entity_id: str) -> bool:
    row = connection.execute(
        "SELECT status, duplicate_of FROM downloads WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return False
    status = row["status"]
    if status == "success":
        # only truly downloaded when the PDF still exists (a manually deleted file means not downloaded)
        return paper_path(entity_id).exists()
    if status == "duplicate":
        # the entity shares content with duplicate_of. Count it as downloaded when either its own
        # linked file exists (downloader now creates one) or the source it duplicates still has its file.
        if paper_path(entity_id).exists():
            return True
        src = connection.execute(
            "SELECT file_path FROM downloads WHERE entity_id = ?", (row["duplicate_of"],)
        ).fetchone()
        return bool(src and src["file_path"] and Path(src["file_path"]).exists())
    return False


def find_by_sha256(connection: sqlite3.Connection, sha256: str) -> str:
    if not sha256:
        return ""
    row = connection.execute(
        "SELECT entity_id FROM downloads WHERE sha256 = ? LIMIT 1", (sha256,)
    ).fetchone()
    return row["entity_id"] if row else ""


def status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS n FROM downloads GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def list_all(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM downloads ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def status_map(connection: sqlite3.Connection, entity_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    result: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT entity_id, status FROM downloads WHERE entity_id IN ({placeholders})", chunk
        ).fetchall()
        for row in rows:
            status = row["status"]
            if status == "success" and not paper_path(row["entity_id"]).exists():
                status = ""  # PDF was deleted; treat as not downloaded
            result[row["entity_id"]] = status
    return result


def remove(connection: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    """Delete a download: remove its PDF file and manifest row, plus any duplicates pointing to it."""
    row = connection.execute(
        "SELECT * FROM downloads WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return {"entity_id": entity_id, "removed": False}
    with connection:
        if row["status"] == "success":
            paper_path(entity_id).unlink(missing_ok=True)
            dup_ids = [
                r["entity_id"]
                for r in connection.execute(
                    "SELECT entity_id FROM downloads WHERE duplicate_of = ?", (entity_id,)
                )
            ]
            for dup_id in dup_ids:
                paper_path(dup_id).unlink(missing_ok=True)
            connection.execute(
                "DELETE FROM downloads WHERE duplicate_of = ?", (entity_id,)
            )
        connection.execute("DELETE FROM downloads WHERE entity_id = ?", (entity_id,))
    return {"entity_id": entity_id, "removed": True}
