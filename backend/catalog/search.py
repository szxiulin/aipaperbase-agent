"""Local paper-library full-text search: FTS5 + BM25 + intent guessing.

Replaces the old LIKE '%full query string%' contiguous-substring matching (no word splitting, so "4K agent" fails to match "4KAgent").

Design (see docs/模块设计/12_本地检索重构.md):
- **Unified normalize pipeline**: NFKC + casefold + camelCase splitting (4KAgent → 4K Agent)
  + letter→digit boundary splitting (super4k → super 4k). digit→letter is not split (4K stays a single token "4k").
  The index side and query side use the same pipeline to keep token surfaces identical.
- **contentless FTS5**: stores only tokens, not the original text (saves space); rowid ↔ record_id mapping in fts_map.
- **BM25 field weighting**: title^3 > abstract^2 > authors^1; paper_id/doi/arxiv_id^10 (identifier exactness first).
- **Intent guessing**: token prefix matching (FTS5 `tok*`) + academic abbreviation synonym expansion (SR→super-resolution
  etc.) + AND combination; an AND 0-result automatically degrades to an OR re-query (to avoid over-strict zero recall).
- **Zero dependency**: SQLite's stdlib ships FTS5 (enabled by default since Python 3.12).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

# ---------- normalize pipeline ----------

_CAMEL_1 = re.compile(r"(?<=[a-z])(?=[A-Z])")  # lowercase→uppercase: agenticAny -> agentic Any
_CAMEL_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")  # SuperResolution -> Super Resolution
_LETTER_TO_DIGIT = re.compile(r"(?<=[a-z])(?=[0-9])")  # super4k -> super 4k (digit→letter not split; 4K stays a single token)


def normalize_text(value: str) -> str:
    """Unified text normalization: NFKC → camelCase/alphanumeric boundary splitting (based on original case) → casefold."""
    value = unicodedata.normalize("NFKC", value or "")
    value = _CAMEL_1.sub(" ", value)
    value = _CAMEL_2.sub(" ", value)
    value = _LETTER_TO_DIGIT.sub(" ", value)
    return value.casefold()


def tokenize(value: str) -> list[str]:
    """Split on non-alphanumeric characters (called after normalize)."""
    return [t for t in re.split(r"[^a-z0-9]+", value) if t]


# ---------- intent guessing ----------

# Common English stopwords (filtered on the query side to reduce noise; not filtered on the index side to avoid losing recall)
STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "on", "in", "with", "to", "is",
    "are", "at", "by", "from", "as", "vs", "using", "based", "via", "into",
}

# Common academic abbreviations → expanded phrases (FTS5 phrase queries in quotes; keys must be tokenize(normalize(x)) output)
SYN_TOKEN_MAP: dict[str, list[str]] = {
    "sr": ["super resolution", "superresolution"],
    "llm": ["large language model"],
    "gan": ["generative adversarial network"],
    "nerf": ["neural radiance field"],
    "cnn": ["convolutional neural network"],
    "rnn": ["recurrent neural network"],
    "vae": ["variational autoencoder"],
    "lstm": ["long short term memory"],
    "dnn": ["deep neural network"],
    "mlp": ["multilayer perceptron"],
    "sota": ["state of the art"],
    "ssl": ["self supervised learning"],
    "ood": ["out of distribution"],
    "iou": ["intersection over union"],
    "moe": ["mixture of experts"],
    "slam": ["simultaneous localization and mapping"],
    "dpr": ["dense passage retrieval"],
    "qa": ["question answering"],
    "rlaif": ["reinforcement learning from ai feedback"],
}


def _group_query(tokens: list[str]) -> str:
    """Token groups (OR within a group, synonyms included); AND between groups. Each group token is prefix-matched (intent guessing)."""
    groups: list[str] = []
    for tok in tokens:
        parts = [f'"{tok}"*']
        for syn in SYN_TOKEN_MAP.get(tok, []):
            parts.append(f'"{syn}"')
        groups.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(groups)


def _group_query_or(tokens: list[str]) -> str:
    """OR fallback: OR all token groups (relaxed to any hit)."""
    return " OR ".join(f'("{tok}"*)' for tok in tokens)


def build_fts_query(query: str) -> tuple[str, list[str]]:
    """Build the intent-guessing query. Returns (fts_match, tokens); (\"\", []) when query is empty or has no valid tokens."""
    text = normalize_text(query)
    tokens = [t for t in tokenize(text) if t and t not in STOPWORDS]
    if not tokens:
        return "", []
    return _group_query(tokens), tokens


def build_fts_query_or(query: str) -> str:
    """OR fallback query (used when AND yields 0 results)."""
    text = normalize_text(query)
    tokens = [t for t in tokenize(text) if t and t not in STOPWORDS]
    return _group_query_or(tokens) if tokens else ""


# ---------- FTS5 index ----------

# contentless FTS5: column order title, abstract, authors, paper_id, doi, arxiv_id
# bm25 weights (corresponding columns): title=3, abstract=2, authors=1, paper_id=10, doi=10, arxiv_id=10
FTS_COLUMNS = ("title", "abstract", "authors", "paper_id", "doi", "arxiv_id")
BM25_WEIGHTS = "3.0, 2.0, 1.0, 10.0, 10.0, 10.0"

FTS_TABLE_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_papers USING fts5(
    title, abstract, authors, paper_id, doi, arxiv_id,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);
"""
FTS_MAP_DDL = """
CREATE TABLE IF NOT EXISTS fts_map (
    rowid INTEGER PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL
);
"""


def _exec_ddl(connection: sqlite3.Connection) -> None:
    """Execute the DDL statements (sqlite3 execute permits only one statement per call, so run them one by one)."""
    connection.execute(FTS_TABLE_DDL)
    connection.execute(FTS_MAP_DDL)


def ensure_index(connection: sqlite3.Connection) -> bool:
    """Ensure the FTS table exists; rebuild fully if missing. Returns whether it rebuilt."""
    has = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts_papers'"
    ).fetchone()
    if has:
        return False
    rebuild_index(connection)
    return True


def rebuild_index(connection: sqlite3.Connection) -> int:
    """Fully rebuild the FTS index (idempotent; called after build/import). Returns the indexed row count."""
    connection.row_factory = sqlite3.Row  # import_csv calls with a bare connection, so ensure dict-style access works
    connection.execute("DROP TABLE IF EXISTS fts_papers")
    connection.execute("DROP TABLE IF EXISTS fts_map")
    _exec_ddl(connection)
    rows = connection.execute(
        "SELECT record_id, title, abstract, authors, paper_id, doi, arxiv_id FROM paper_records"
    ).fetchall()
    count = 0
    for row in rows:
        count += 1
        index_record(connection, row["record_id"], _row=row)
        if count % 5000 == 0:
            connection.commit()
    connection.commit()
    return count


def index_record(
    connection: sqlite3.Connection,
    record_id: str,
    _row: Any = None,
) -> None:
    """Incrementally index a single record (called after new ingests like arXiv; skipped if already indexed)."""
    existing = connection.execute(
        "SELECT 1 FROM fts_map WHERE record_id = ?", (record_id,)
    ).fetchone()
    if existing:
        return
    if _row is None:
        _row = connection.execute(
            "SELECT record_id, title, abstract, authors, paper_id, doi, arxiv_id"
            " FROM paper_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
    if _row is None:
        return
    rowid = connection.execute(
        "SELECT COALESCE(MAX(rowid), 0) + 1 FROM fts_map"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO fts_map(rowid, record_id) VALUES (?, ?)", (rowid, record_id)
    )
    connection.execute(
        "INSERT INTO fts_papers(rowid, title, abstract, authors, paper_id, doi, arxiv_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            rowid,
            normalize_text(_row["title"]),
            normalize_text(_row["abstract"]),
            normalize_text(_row["authors"]),
            normalize_text(_row["paper_id"]),
            normalize_text(_row["doi"]),
            normalize_text(_row["arxiv_id"]),
        ),
    )


def remove_record(connection: sqlite3.Connection, record_id: str) -> None:
    """Remove a record from the index (contentless table uses the 'delete' command, which needs the original column values)."""
    row = connection.execute(
        "SELECT rowid FROM fts_map WHERE record_id = ?", (record_id,)
    ).fetchone()
    if not row:
        return
    pr = connection.execute(
        "SELECT title, abstract, authors, paper_id, doi, arxiv_id"
        " FROM paper_records WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    if pr is not None:
        values = tuple(normalize_text(pr[key]) for key in FTS_COLUMNS)
        connection.execute(
            "INSERT INTO fts_papers(fts_papers, rowid, title, abstract, authors,"
            " paper_id, doi, arxiv_id) VALUES('delete', ?, ?, ?, ?, ?, ?, ?)",
            (row["rowid"], *values),
        )
    connection.execute("DELETE FROM fts_map WHERE record_id = ?", (record_id,))


def match_record_ids(
    connection: sqlite3.Connection,
    fts_query: str,
    limit: int = 2000,
) -> list[str]:
    """FTS search, returning record_id list ordered by BM25 score descending (after intent guessing)."""
    rows = connection.execute(
        f"""SELECT fm.record_id
            FROM fts_map fm
            JOIN fts_papers f ON f.rowid = fm.rowid
            WHERE fts_papers MATCH ?
            ORDER BY bm25(fts_papers, {BM25_WEIGHTS}), f.rowid
            LIMIT ?""",
        (fts_query, limit),
    ).fetchall()
    return [row["record_id"] for row in rows]


def has_index(connection: sqlite3.Connection) -> bool:
    """Whether the FTS index exists (callers fall back to LIKE when it does not)."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts_papers'"
        ).fetchone()
        is not None
    )


def resolve(connection: sqlite3.Connection, query: str, limit: int = 2000) -> list[str] | None:
    """Full intent-guessing search entry: AND combination → auto OR fallback on 0 results.

    Returns record_id list ordered by BM25; [] when query has no valid tokens; None when the
    index does not exist (callers fall back to LIKE).
    """
    if not (query or "").strip():
        return []
    if not has_index(connection):
        return None
    fts_query, _ = build_fts_query(query)
    if not fts_query:
        return []
    ids = match_record_ids(connection, fts_query, limit)
    if ids:
        return ids
    fts_or = build_fts_query_or(query)
    if not fts_or:
        return []
    return match_record_ids(connection, fts_or, limit)


def order_case(ids: list[str]) -> str:
    """CASE expression restoring order by ids (used with the ids parameter list; single batch ≤1000 items)."""
    if not ids:
        return ""
    return (
        "CASE p.record_id "
        + " ".join(f"WHEN ? THEN {i}" for i in range(len(ids)))
        + " ELSE 9999 END"
    )
