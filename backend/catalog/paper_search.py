"""Entity-level paper search for the research agent.

Differences from queries.search_papers (kept for the legacy catalog page):
- one row per unified paper entity, canonical venue/year, non-paper records excluded;
- filters use the current taxonomy (areas/directions) shared with backend.capabilities;
- when no paper contains every keyword, relax by dropping the most common keyword first instead
  of OR-ing everything (OR turned a 5-word query into 35k loosely related hits);
- each hit carries area/direction names and an abstract snippet so the model can judge relevance.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.capabilities.filters import BASE, normalize_filters, where_clause
from backend.catalog import search as search_mod

MAX_PAGE_SIZE = 25
SNIPPET_CHARS = 280
GROUP_COLUMNS = {"venue": "venue", "year": "year", "area": "area_id", "direction": "direction_id"}


def _count(connection: sqlite3.Connection, fts_query: str) -> int:
    return connection.execute("SELECT COUNT(*) FROM fts_papers WHERE fts_papers MATCH ?", (fts_query,)).fetchone()[0]


def relaxed_match(connection: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Return {"fts", "terms", "dropped"}; fts is "" when the query has no searchable English token."""
    _, tokens = search_mod.build_fts_query(query)
    if not tokens:
        return {"fts": "", "terms": [], "dropped": []}
    terms = list(dict.fromkeys(tokens))
    full = search_mod._group_query(terms)
    if _count(connection, full):
        return {"fts": full, "terms": terms, "dropped": []}
    freq = {t: _count(connection, search_mod._group_query([t])) for t in terms}
    kept = [t for t in terms if freq[t] > 0]
    dropped = [t for t in terms if freq[t] == 0]
    # Keep as many terms as possible: at each level try every "drop one term" variant and keep the
    # variant that still matches the most papers; if none matches, drop the rarest (most limiting) term.
    while len(kept) > 1:
        if _count(connection, search_mod._group_query(kept)):
            return {"fts": search_mod._group_query(kept), "terms": kept, "dropped": dropped}
        options = []
        for term in kept:
            rest = [t for t in kept if t != term]
            options.append((_count(connection, search_mod._group_query(rest)), term))
        hits, term = max(options)
        if hits == 0:
            term = min(kept, key=lambda t: freq[t])
        kept.remove(term)
        dropped.append(term)
    if kept:
        return {"fts": search_mod._group_query(kept), "terms": kept, "dropped": dropped}
    return {"fts": "", "terms": [], "dropped": dropped}


def _authors(value: str | None) -> str:
    names = [n.strip() for n in (value or "").replace(" and ", ";").split(";") if n.strip()]
    if len(names) > 3:
        return "; ".join(names[:3]) + " 等"
    return "; ".join(names)


def search(connection: sqlite3.Connection, *, query: str = "", filters: dict | None = None,
           sort: str = "relevance", page: int = 1, page_size: int = 10,
           aggregate: list[str] | None = None) -> dict[str, Any]:
    raw_filters = dict(filters or {})
    raw_filters.pop("query", None)
    normalized = normalize_filters(raw_filters)
    where, params = where_clause(normalized)
    page = max(int(page or 1), 1)
    page_size = max(0, min(int(page_size if page_size is not None else 10), MAX_PAGE_SIZE))
    match = {"fts": "", "terms": [], "dropped": []}
    query = (query or "").strip()
    original_query = query

    select = """a.entity_id, e.canonical_title AS title, p.venue, p.year, a.area_id, d.direction_id,
                ad.name_zh AS area_name, dd.name_zh AS direction_name,
                substr(p.abstract, 1, {n}) AS snippet, p.authors""".format(n=SNIPPET_CHARS + 1)
    joins = """ LEFT JOIN area_definitions ad ON ad.area_id = a.area_id
                LEFT JOIN direction_definitions dd ON dd.direction_id = d.direction_id"""
    if query and not search_mod.has_index(connection):
        # Small fixtures / databases built without FTS: every token must appear in title or abstract.
        _, tokens = search_mod.build_fts_query(query)
        for token in tokens or [query]:
            where += " AND (e.canonical_title LIKE ? OR p.abstract LIKE ?)"
            params.extend([f"%{token}%", f"%{token}%"])
        match = {"fts": "", "terms": tokens, "dropped": []}
        query = ""
    if query:
        match = relaxed_match(connection, query)
        if not match["fts"]:
            note = ("当前关键词索引不支持中文分词，请尝试英文关键词检索。" if any("一" <= ch <= "鿿" for ch in query)
                    else "关键词在论文库中没有命中。")
            return {"count_unit": "canonical_research_entities", "total": 0, "page": page, "page_size": page_size, "items": [], "facets": {},
                    "match": {"query": original_query, "terms": [], "dropped": match["dropped"], "note": note,
                              "mode": "no_searchable_tokens" if not search_mod.build_fts_query(original_query)[1] else "no_matches",
                              "relaxed": False, "count_kind": "matches"}}
        grouped = f"""(WITH hit AS (
                SELECT fm.record_id, fts_papers.rank AS rank
                FROM fts_papers JOIN fts_map fm ON fm.rowid = fts_papers.rowid
                WHERE fts_papers MATCH ? AND fts_papers.rank MATCH ?)
            SELECT {select}, MIN(h.rank) AS rank
            FROM hit h
            JOIN entity_memberships m ON m.record_id = h.record_id
            JOIN entity_area_assignments a ON a.entity_id = m.entity_id
            LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
            JOIN paper_entities e ON e.entity_id = a.entity_id
            JOIN paper_records p ON p.record_id = e.canonical_record_id{joins}
            {where} GROUP BY a.entity_id)"""
        grouped_params: list[Any] = [match["fts"], f"bm25({search_mod.BM25_WEIGHTS})", *params]
        order = "rank, year DESC" if sort != "year_desc" else "year DESC, rank"
    else:
        grouped = f"(SELECT {select}, 0 AS rank {BASE}{joins}{where})"
        grouped_params = list(params)
        order = "year DESC, title"

    total = connection.execute(f"SELECT COUNT(*) FROM {grouped}", grouped_params).fetchone()[0]
    facets: dict[str, list[dict]] = {}
    for key in aggregate or []:
        column = GROUP_COLUMNS.get(key)
        if not column:
            continue
        rows = connection.execute(
            f"SELECT {column} AS k, COUNT(*) AS c FROM {grouped} GROUP BY {column} ORDER BY c DESC LIMIT 15",
            grouped_params).fetchall()
        facets[key] = [{"key": r["k"], "count": r["c"]} for r in rows]
    items: list[dict] = []
    if page_size:
        rows = connection.execute(f"SELECT * FROM {grouped} ORDER BY {order} LIMIT ? OFFSET ?",
                                  [*grouped_params, page_size, (page - 1) * page_size]).fetchall()
        for row in rows:
            snippet = (row["snippet"] or "").strip()
            items.append({
                "entity_id": row["entity_id"], "title": row["title"], "venue": row["venue"], "year": row["year"],
                "area": row["area_name"] or row["area_id"], "direction": row["direction_name"] or row["direction_id"] or "",
                "authors": _authors(row["authors"]),
                "snippet": snippet[:SNIPPET_CHARS] + ("…" if len(snippet) > SNIPPET_CHARS else ""),
            })
    result = {"count_unit": "canonical_research_entities", "total": total, "page": page, "page_size": page_size, "items": items, "facets": facets}
    if match["terms"] or match["dropped"]:
        result["match"] = {"terms": match["terms"], "dropped": match["dropped"],
                           "query": original_query, "relaxed": bool(match["dropped"]),
                           "count_kind": "candidates" if match["dropped"] else "matches"}
        if match["dropped"]:
            result["match"]["note"] = ("没有论文同时包含全部关键词，已放宽为只要求：" + " ".join(match["terms"])
                                       + "；被忽略：" + " ".join(match["dropped"]) + "。结果可能偏宽，请看摘要判断相关性。")
    return result
