"""On-demand topic recall: keyword + structured filters over the catalog.

This is the free half of define_topic: it returns candidates, their distribution and a transparent
method note. Semantic recall and LLM verification are separate steps (verification costs API calls).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.capabilities.filters import normalize_filters, where_clause
from backend.catalog import search as search_mod

VERIFIED_TOPICS = Path(__file__).resolve().parents[2] / "data/topics/verified_topics.json"


def _verified(query: str) -> dict | None:
    try:
        data = json.loads(VERIFIED_TOPICS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    key = " ".join(query.lower().split())
    for topic in data.get("topics", []):
        if " ".join(str(topic.get("query", "")).lower().split()) == key:
            return {"status": "verified", "precision_percent": topic["precision_percent"],
                    "ci95": topic["ci95"], "sample_size": topic["sample_size"],
                    "candidate_count": topic["candidate_count"], "reviewed_at": topic["reviewed_at"],
                    "reviewer": "llm-assisted (deepseek-v4-flash) + author-visible report",
                    "definition": topic.get("definition", "")}
    return None


def _base(query: str, filters: dict, connection: sqlite3.Connection):
    """Return (from_sql, params, method) for the candidate query."""
    where, params = where_clause(filters)
    expr = search_mod.resolve_fts_query(connection, query)
    # Prefer an exact phrase (plus a plural variant) when it still yields enough records;
    # token-AND recall is broad and brings in many off-topic papers for phrase-like topics.
    method = "fts5-and"
    if expr:
        tokens = search_mod.tokenize(search_mod.normalize_text(query))
        if len(tokens) >= 2:
            phrase = '"' + " ".join(tokens) + '"'
            variant = '"' + " ".join(tokens[:-1] + [tokens[-1] + "s"]) + '"'
            candidate = f"({phrase} OR {variant})"
            count = connection.execute(
                "SELECT COUNT(DISTINCT fm.record_id) FROM fts_papers JOIN fts_map fm ON fm.rowid = fts_papers.rowid "
                "WHERE fts_papers MATCH ?", (candidate,)).fetchone()[0]
            if count >= 10:
                expr = candidate
                method = "fts5-phrase"
    if expr is not None and expr:
        hit = ("WITH hit AS (SELECT fm.record_id, fts_papers.rank AS rank FROM fts_papers "
               "JOIN fts_map fm ON fm.rowid = fts_papers.rowid WHERE fts_papers MATCH ?) ")
        from_sql = (hit + """SELECT a.entity_id, MIN(h.rank) AS rank, e.canonical_title AS title,
                              p.venue, p.year, a.area_id, d.direction_id
                       FROM hit h
                       JOIN entity_memberships m ON m.record_id = h.record_id
                       JOIN entity_area_assignments a ON a.entity_id = m.entity_id
                       LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
                       JOIN paper_entities e ON e.entity_id = a.entity_id
                       JOIN paper_records p ON p.record_id = e.canonical_record_id""")
        return from_sql + where, [expr, *params], method
    term = f"%{query}%"
    like_where = where + " AND (e.canonical_title LIKE ? OR p.abstract LIKE ? OR p.authors LIKE ?)"
    from_sql = """SELECT a.entity_id, 0 AS rank, e.canonical_title AS title, p.venue, p.year,
                         a.area_id, d.direction_id
                  FROM entity_area_assignments a
                  LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
                  JOIN paper_entities e ON e.entity_id = a.entity_id
                  JOIN paper_records p ON p.record_id = e.canonical_record_id"""
    return from_sql + like_where, [*params, term, term, term], "like-fallback"


def _rows(connection: sqlite3.Connection, sql: str, params: list[Any]) -> list[dict]:
    return [dict(r) for r in connection.execute(sql, params).fetchall()]


def candidate_ids(connection: sqlite3.Connection, query: str, filters: dict | None = None,
                  limit: int = 5000) -> dict:
    filters = normalize_filters(filters)
    if not query.strip():
        raise ValueError("query is required")
    base, params, method = _base(query.strip(), filters, connection)
    sql = f"SELECT entity_id FROM ({base} GROUP BY a.entity_id) ORDER BY rank LIMIT ?"
    rows = connection.execute(sql, [*params, limit]).fetchall()
    return {"entity_ids": [r[0] for r in rows], "method": method, "filters": filters}


def recall_topic(connection: sqlite3.Connection, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    filters = normalize_filters(args.get("filters"))
    limit = min(max(int(args.get("limit") or 50), 1), 200)
    base, params, method = _base(query, filters, connection)
    grouped = f"({base} GROUP BY a.entity_id)"
    total = connection.execute(f"SELECT COUNT(*) FROM {grouped}", params).fetchone()[0]
    items = _rows(connection, f"SELECT * FROM {grouped} ORDER BY rank LIMIT ?", [*params, limit])
    stats = {}
    for field, col in (("year", "year"), ("venue", "venue"), ("area", "area_id")):
        stats[f"by_{field}"] = _rows(
            connection,
            f"SELECT {col} AS key, COUNT(*) AS count FROM {grouped} GROUP BY {col} "
            f"ORDER BY count DESC, key LIMIT 20", params)
    verification = _verified(query) or {
        "status": "not_verified",
        "note": "候选只经过关键词/结构化召回；精度需 sample + LLM 逐篇判断，未验证前不要把候选数当命中数。"}
    return {"query": query, "filters": filters, "candidate_count": total, "items": items,
            "stats": stats, "method": method, "verification": verification}
