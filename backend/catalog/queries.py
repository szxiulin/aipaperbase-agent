from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from backend.catalog import search as search_mod


def _dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    release = connection.execute(
        "SELECT * FROM data_releases WHERE status = 'ready' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    counts = connection.execute(
        """SELECT
               COUNT(*) AS record_count,
               COUNT(DISTINCT venue) AS venue_count,
               COUNT(DISTINCT CASE WHEN venue_type = 'conference' THEN venue END) AS conference_count,
               COUNT(DISTINCT CASE WHEN venue_type = 'journal' THEN venue END) AS journal_count,
               COUNT(DISTINCT source_file) AS edition_count,
               SUM(list_status = 'final') AS final_count,
               SUM(list_status = 'rolling') AS rolling_count,
               SUM(abstract <> '') AS abstract_count,
               MIN(year) AS min_year,
               MAX(year) AS max_year
           FROM paper_records"""
    ).fetchone()
    entity_counts = connection.execute(
        """SELECT COUNT(*) AS entity_count,
                  SUM(entity_status = 'merged') AS merged_entity_count,
                  SUM(CASE WHEN entity_status = 'merged' THEN record_count ELSE 0 END) AS linked_record_count
           FROM paper_entities"""
    ).fetchone()
    issue_counts = connection.execute(
        "SELECT severity, COUNT(*) AS count FROM quality_issues GROUP BY severity"
    ).fetchall()
    return {
        "release": dict(release) if release else None,
        **dict(counts),
        **dict(entity_counts),
        "issue_counts": {row["severity"]: row["count"] for row in issue_counts},
    }


def venues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return _dicts(connection.execute(
        """SELECT venue, venue_type,
                  COUNT(*) AS record_count,
                  COUNT(DISTINCT year) AS year_count,
                  MIN(year) AS min_year,
                  MAX(year) AS max_year,
                  SUM(list_status = 'final') AS final_count,
                  SUM(list_status = 'rolling') AS rolling_count,
                  ROUND(100.0 * SUM(doi <> '') / COUNT(*), 1) AS doi_rate,
                  ROUND(100.0 * SUM(arxiv_id <> '') / COUNT(*), 1) AS arxiv_rate,
                  ROUND(100.0 * SUM(abstract <> '') / COUNT(*), 1) AS abstract_rate,
                  ROUND(100.0 * SUM(pdf_url <> '') / COUNT(*), 1) AS pdf_rate
           FROM paper_records
           GROUP BY venue, venue_type
           ORDER BY record_count DESC, venue"""
    ).fetchall())


def years(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return _dicts(connection.execute(
        """SELECT year, COUNT(*) AS record_count,
                  COUNT(DISTINCT venue) AS venue_count,
                  SUM(list_status = 'final') AS final_count,
                  SUM(list_status = 'rolling') AS rolling_count
           FROM paper_records GROUP BY year ORDER BY year"""
    ).fetchall())


def quality(connection: sqlite3.Connection) -> dict[str, Any]:
    fields = {
        "doi": "doi",
        "arxiv_id": "arxiv_id",
        "paper_url": "paper_url",
        "pdf_url": "pdf_url",
        "authors": "authors",
        "abstract": "abstract",
    }
    total = connection.execute("SELECT COUNT(*) FROM paper_records").fetchone()[0]
    completeness = []
    for label, column in fields.items():
        present = connection.execute(f"SELECT COUNT(*) FROM paper_records WHERE {column} <> ''").fetchone()[0]
        completeness.append({
            "field": label,
            "present": present,
            "missing": total - present,
            "rate": round(100 * present / total, 1) if total else 0,
        })
    issue_groups = _dicts(connection.execute(
        """SELECT severity, issue_type, COUNT(*) AS count
           FROM quality_issues GROUP BY severity, issue_type
           ORDER BY CASE severity WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, count DESC"""
    ).fetchall())
    source_tiers = _dicts(connection.execute(
        """SELECT source_tier, COUNT(*) AS count,
                  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM paper_records), 1) AS rate
           FROM paper_records GROUP BY source_tier ORDER BY count DESC"""
    ).fetchall())
    abstract_source_tiers = _dicts(connection.execute(
        """SELECT abstract_source_tier AS source_tier, COUNT(*) AS count,
                  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM paper_records WHERE abstract <> ''), 1) AS rate
           FROM paper_records WHERE abstract <> ''
           GROUP BY abstract_source_tier ORDER BY count DESC"""
    ).fetchall())
    cross_venue = connection.execute(
        """SELECT COUNT(*) FROM (
               SELECT doi FROM paper_records WHERE doi <> ''
               GROUP BY doi HAVING COUNT(DISTINCT venue) > 1
           )"""
    ).fetchone()[0]
    return {
        "record_count": total,
        "completeness": completeness,
        "issue_groups": issue_groups,
        "source_tiers": source_tiers,
        "abstract_source_tiers": abstract_source_tiers,
        "cross_venue_doi_groups": cross_venue,
    }


def entity_quality(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = connection.execute(
        """SELECT COUNT(*) AS entity_count,
                  SUM(entity_status = 'merged') AS merged_entity_count,
                  SUM(CASE WHEN entity_status = 'merged' THEN record_count ELSE 0 END) AS linked_record_count,
                  MAX(record_count) AS max_record_count
           FROM paper_entities"""
    ).fetchone()
    pending = connection.execute(
        "SELECT COUNT(*) FROM entity_review_candidates WHERE review_status = 'pending'"
    ).fetchone()[0]
    methods = _dicts(connection.execute(
        """SELECT match_method,
                  COUNT(DISTINCT entity_id) AS entity_count,
                  COUNT(*) AS record_count
           FROM entity_memberships
           GROUP BY match_method
           ORDER BY record_count DESC"""
    ).fetchall())
    candidate_groups = _dicts(connection.execute(
        """SELECT candidate_method, confidence, review_status, COUNT(*) AS count
           FROM entity_review_candidates
           GROUP BY candidate_method, confidence, review_status
           ORDER BY count DESC"""
    ).fetchall())
    return {
        **dict(counts),
        "pending_review_count": pending,
        "match_methods": methods,
        "candidate_groups": candidate_groups,
    }


def entities(
    connection: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 25,
    entity_status: str = "merged",
    search: str = "",
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    clauses: list[str] = []
    params: list[Any] = []
    if entity_status in {"single", "merged"}:
        clauses.append("e.entity_status = ?")
        params.append(entity_status)
    if search:
        clauses.append("(e.canonical_title LIKE ? OR e.entity_id LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total = connection.execute(f"SELECT COUNT(*) FROM paper_entities e{where}", params).fetchone()[0]
    entity_rows = _dicts(connection.execute(
        f"""SELECT e.entity_id, e.canonical_title, e.first_year, e.last_year,
                   e.record_count, e.venue_count, e.entity_status,
                   p.authors, p.abstract, p.paper_url, p.doi, p.arxiv_id
            FROM paper_entities e
            JOIN paper_records p ON p.record_id = e.canonical_record_id
            {where}
            ORDER BY e.record_count DESC, e.last_year DESC, e.canonical_title
            LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall())
    ids = [item["entity_id"] for item in entity_rows]
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        member_rows = connection.execute(
            f"""SELECT m.entity_id, m.match_method, m.evidence_value, m.confidence,
                       m.is_canonical, p.record_id, p.venue, p.venue_type, p.year,
                       p.title, p.paper_url, p.doi, p.arxiv_id, p.list_status,
                       p.source_file
                FROM entity_memberships m
                JOIN paper_records p ON p.record_id = m.record_id
                WHERE m.entity_id IN ({placeholders})
                ORDER BY m.entity_id, m.is_canonical DESC, p.year, p.venue""",
            ids,
        ).fetchall()
        for row in member_rows:
            item = dict(row)
            members[item.pop("entity_id")].append(item)
    for item in entity_rows:
        item["members"] = members[item["entity_id"]]
    return {
        "items": entity_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


def entity_candidates(
    connection: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 25,
    review_status: str = "pending",
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    clauses = []
    params: list[Any] = []
    if review_status in {"pending", "accepted", "rejected"}:
        clauses.append("c.review_status = ?")
        params.append(review_status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total = connection.execute(f"SELECT COUNT(*) FROM entity_review_candidates c{where}", params).fetchone()[0]
    rows = connection.execute(
        f"""SELECT c.candidate_id, c.candidate_method, c.evidence_value, c.reason,
                   c.confidence, c.review_status,
                   l.record_id AS left_record_id, l.title AS left_title,
                   l.venue AS left_venue, l.year AS left_year, l.doi AS left_doi,
                   l.arxiv_id AS left_arxiv_id,
                   r.record_id AS right_record_id, r.title AS right_title,
                   r.venue AS right_venue, r.year AS right_year, r.doi AS right_doi,
                   r.arxiv_id AS right_arxiv_id
            FROM entity_review_candidates c
            JOIN paper_records l ON l.record_id = c.left_record_id
            JOIN paper_records r ON r.record_id = c.right_record_id
            {where}
            ORDER BY CASE c.confidence WHEN 'conflict' THEN 1 ELSE 2 END,
                     c.candidate_method, l.title
            LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    return {
        "items": _dicts(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


def _paper_filters(
    venue: str = "",
    year: int | None = None,
    venue_type: str = "",
    list_status: str = "",
    has_abstract: str = "",
    search: str = "",
    fts_query: str | None = None,
) -> tuple[str, str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    fts_join = ""
    if venue:
        clauses.append("p.venue = ?")
        params.append(venue)
    if year is not None:
        clauses.append("p.year = ?")
        params.append(year)
    if venue_type in {"conference", "journal", "preprint"}:
        clauses.append("p.venue_type = ?")
        params.append(venue_type)
    if list_status in {"final", "rolling"}:
        clauses.append("p.list_status = ?")
        params.append(list_status)
    if has_abstract == "yes":
        clauses.append("p.abstract <> ''")
    elif has_abstract == "no":
        clauses.append("p.abstract = ''")
    if fts_query is not None:
        # Keep the hit set inside SQLite.  Expanding it into IN / CASE parameters
        # makes otherwise valid large FTS searches slow and can exceed bind limits.
        if fts_query:
            fts_join = (
                " JOIN fts_map fm ON fm.record_id = p.record_id"
                " JOIN fts_papers f ON f.rowid = fm.rowid"
            )
            clauses.append("fts_papers MATCH ?")
            params.append(fts_query)
        else:
            clauses.append("1 = 0")
    elif search:
        # LIKE fallback when no FTS index (normally uses FTS, so this is not hit)
        clauses.append("(p.title LIKE ? OR p.authors LIKE ? OR p.abstract LIKE ? OR p.paper_id LIKE ? OR p.doi LIKE ? OR m.entity_id LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term, term, term, term])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return fts_join, where, params


def papers_entity_ids(
    connection: sqlite3.Connection,
    *,
    venue: str = "",
    year: int | None = None,
    venue_type: str = "",
    list_status: str = "",
    has_abstract: str = "",
    search: str = "",
) -> dict[str, Any]:
    """All distinct entity ids matching the same filters as papers(), unpaginated."""
    fts_query = search_mod.resolve_fts_query(connection, search) if search else None
    fts_join, where, params = _paper_filters(venue, year, venue_type, list_status, has_abstract, search, fts_query)
    rows = connection.execute(
        f"""SELECT DISTINCT m.entity_id
            FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id
            {fts_join}
            {where}
            ORDER BY m.entity_id""",
        params,
    ).fetchall()
    entity_ids = [row["entity_id"] for row in rows]
    return {"entity_ids": entity_ids, "total": len(entity_ids)}


def search_records(
    connection: sqlite3.Connection,
    *,
    search: str = "",
    venue: str = "",
    year: int | None = None,
    topic: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Structured search (used by the agent's search_catalog tool): filter by keyword/venue/year/topic,
    returning the top `limit` paper summaries deduplicated by entity. Parameterized SQL to prevent injection.
    """
    clauses: list[str] = []
    where_params: list[Any] = []
    fts_join = ""
    if venue:
        clauses.append("p.venue = ?")
        where_params.append(venue)
    if year is not None:
        clauses.append("p.year = ?")
        where_params.append(year)
    if search:
        fts_query = search_mod.resolve_fts_query(connection, search)
        if fts_query is None:
            # no FTS index: LIKE fallback
            clauses.append("(p.title LIKE ? OR p.authors LIKE ? OR p.abstract LIKE ? OR p.paper_id LIKE ? OR p.doi LIKE ?)")
            term = f"%{search}%"
            where_params.extend([term, term, term, term, term])
        elif fts_query:
            fts_join = (
                " JOIN fts_map fm ON fm.record_id = p.record_id"
                " JOIN fts_papers f ON f.rowid = fm.rowid"
            )
            clauses.append("fts_papers MATCH ?")
            where_params.append(fts_query)
        else:
            clauses.append("1 = 0")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    topic_join = ""
    if topic:
        # v2: assignments store only leaf-level primary; filtering by topic name needs roll-up via topic_ancestors (family/direction/leaf all valid).
        topic_join = (
            " JOIN entity_topic_assignments ta ON ta.entity_id = m.entity_id AND ta.role = 'primary'"
            " JOIN topic_ancestors tanc ON tanc.topic_id = ta.topic_id"
            " JOIN topic_definitions td ON td.topic_id = tanc.ancestor_topic_id AND td.name = ?"
        )
        topic_params = [topic]
    else:
        topic_params = []
    params = [*topic_params, *where_params]
    total = connection.execute(
        f"""SELECT COUNT(DISTINCT m.entity_id) FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id{topic_join}{fts_join}{where}""",
        params,
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT m.entity_id, e.canonical_title AS title, p.venue, p.venue_type, p.year,
                   p.authors, substr(p.abstract, 1, 300) AS abstract_snippet
            FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id{topic_join}{fts_join}
            JOIN paper_entities e ON e.entity_id = m.entity_id{where}
            ORDER BY p.year DESC, p.venue
            LIMIT ?""",
        [*params, max(1, min(limit, 50))],
    ).fetchall()
    return {
        "total": total,
        "items": [dict(row) for row in rows],
    }


def search_papers(
    connection: sqlite3.Connection,
    *,
    query: str = "",
    venues: list[str] | None = None,
    years: list[int] | None = None,
    topics: list[str] | None = None,
    venue_type: str = "",
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 10,
    aggregate: list[str] | None = None,
) -> dict[str, Any]:
    """Unified catalog search (used by the agent's search_papers tool).

    - filters: multi-value IN filters on venues/years/topics + venue_type;
    - sort: relevance (title>abstract>authors hit weighting, approximate semantic sort) / year_desc;
    - page_size=0: returns only total + facets, no items (for "how many / distribution" questions);
    - aggregate: GROUP BY counts on the result set (venue/year/topic), returning facets.
    Parameterized SQL to prevent injection.
    """
    clauses: list[str] = []
    params: list[Any] = []
    fts_join = ""
    if venues:
        clauses.append(f"p.venue IN ({','.join('?' * len(venues))})")
        params.extend(venues)
    if years:
        clauses.append(f"p.year IN ({','.join('?' * len(years))})")
        params.extend(years)
    if venue_type in ("conference", "journal", "preprint"):
        clauses.append("p.venue_type = ?")
        params.append(venue_type)
    if query:
        if search_mod.has_index(connection):
            fts_query, _ = search_mod.build_fts_query(query)
            if fts_query:
                # AND-combination 0 results → OR fallback (intent-guessing safety net, prevents over-strict zero recall)
                if not search_mod.match_record_ids(connection, fts_query, limit=1):
                    fts_query = search_mod.build_fts_query_or(query)
                if fts_query:
                    # FTS JOIN + BM25 ordering (intent-guessing token prefix + synonym expansion)
                    fts_join = (
                        " JOIN fts_map fm ON fm.record_id = p.record_id"
                        " JOIN fts_papers f ON f.rowid = fm.rowid"
                    )
                    clauses.append("fts_papers MATCH ?")
                    params.append(fts_query)
        if not fts_join:
            # no FTS index / all-stopwords case: LIKE fallback
            clauses.append(
                "(p.title LIKE ? OR p.authors LIKE ? OR p.abstract LIKE ? OR p.paper_id LIKE ? OR p.doi LIKE ?)"
            )
            term = f"%{query}%"
            params.extend([term, term, term, term, term])
    topic_join = ""
    if topics:
        topic_join = (
            " JOIN entity_topic_assignments ta ON ta.entity_id = m.entity_id AND ta.role = 'primary'"
            " JOIN topic_ancestors tanc ON tanc.topic_id = ta.topic_id"
            " JOIN topic_definitions td ON td.topic_id = tanc.ancestor_topic_id"
        )
        clauses.append(f"td.name IN ({','.join('?' * len(topics))})")
        params.extend(topics)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    base = (
        f"FROM paper_records p JOIN entity_memberships m ON m.record_id = p.record_id"
        f"{topic_join}{fts_join}"
    )

    total = connection.execute(
        f"SELECT COUNT(DISTINCT m.entity_id) {base}{where}", params
    ).fetchone()[0]

    facets: dict[str, dict[str, int]] = {}
    for key in (aggregate or []):
        if key not in ("venue", "year", "topic"):
            continue
        if key == "topic":
            # v2: roll up primary leaves to family/direction/leaf, aggregate by node name (same basis as the topic filter).
            rows = connection.execute(
                f"""SELECT td.name AS k, COUNT(DISTINCT m.entity_id) AS c
                    FROM paper_records p
                    JOIN entity_memberships m ON m.record_id = p.record_id
                    {fts_join}
                    JOIN entity_topic_assignments ta ON ta.entity_id = m.entity_id AND ta.role = 'primary'
                    JOIN topic_ancestors tanc ON tanc.topic_id = ta.topic_id
                    JOIN topic_definitions td ON td.topic_id = tanc.ancestor_topic_id
                    {where} GROUP BY td.name ORDER BY c DESC""",
                params,
            ).fetchall()
        else:
            column = {"venue": "p.venue", "year": "p.year"}[key]
            rows = connection.execute(
                f"SELECT {column} AS k, COUNT(DISTINCT m.entity_id) AS c {base}{where}"
                f" GROUP BY {column} ORDER BY c DESC",
                params,
            ).fetchall()
        facets[key] = {row["k"]: row["c"] for row in rows}

    items: list[dict[str, Any]] = []
    if page_size > 0:
        entity_select = (
            "SELECT m.entity_id, e.canonical_title AS title, p.venue, p.year"
            f" {base} JOIN paper_entities e ON e.entity_id = m.entity_id{where}"
        )
        if fts_join:
            # FTS path: BM25 score ordering (intent-guessing hits first)
            order = f"bm25(fts_papers, {search_mod.BM25_WEIGHTS}), p.year DESC"
            rows = connection.execute(
                f"{entity_select} ORDER BY {order} LIMIT ? OFFSET ?",
                [*params, page_size, (max(page, 1) - 1) * page_size],
            ).fetchall()
        elif sort == "relevance" and query:
            term = f"%{query}%"
            order = (
                "(CASE WHEN p.title LIKE ? THEN 3 WHEN p.abstract LIKE ? THEN 2"
                " WHEN p.authors LIKE ? THEN 1 ELSE 0 END) DESC, p.year DESC, p.venue"
            )
            rows = connection.execute(
                f"{entity_select} ORDER BY {order} LIMIT ? OFFSET ?",
                [*params, term, term, term, page_size, (max(page, 1) - 1) * page_size],
            ).fetchall()
        else:
            rows = connection.execute(
                f"{entity_select} ORDER BY p.year DESC, p.venue LIMIT ? OFFSET ?",
                [*params, page_size, (max(page, 1) - 1) * page_size],
            ).fetchall()
        items = [dict(row) for row in rows]

    return {
        "total": total,
        "page": max(page, 1),
        "page_size": max(page_size, 0),
        "items": items,
        "facets": facets,
    }


def papers(
    connection: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 25,
    venue: str = "",
    year: int | None = None,
    venue_type: str = "",
    list_status: str = "",
    has_abstract: str = "",
    search: str = "",
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    fts_query = search_mod.resolve_fts_query(connection, search) if search else None
    fts_join, where, params = _paper_filters(venue, year, venue_type, list_status, has_abstract, search, fts_query)
    total = connection.execute(
        f"""SELECT COUNT(*) FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id{fts_join}{where}""",
        params,
    ).fetchone()[0]
    order = "p.year DESC, p.venue, p.title"
    if fts_query:
        order = f"bm25(fts_papers, {search_mod.BM25_WEIGHTS}), f.rowid, p.year DESC, p.title"
    rows = connection.execute(
        f"""SELECT p.record_id, p.paper_id, p.venue, p.venue_type, p.year, p.track,
                   p.title, p.authors, p.abstract, p.abstract_source_name,
                   p.abstract_source_url, p.abstract_source_tier, p.abstract_fetched_at,
                   p.doi, p.arxiv_id, p.paper_url, p.pdf_url, p.source_tier,
                   p.verification_status, p.list_status, p.source_file,
                   m.entity_id, e.record_count AS entity_record_count,
                   m.match_method AS entity_match_method
            FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id
            JOIN paper_entities e ON e.entity_id = m.entity_id
            {fts_join}
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    return {
        "items": _dicts(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }
