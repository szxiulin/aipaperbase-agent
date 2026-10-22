"""Catalog-facing capabilities: search_papers / get_paper / sample_papers."""
from __future__ import annotations

import sqlite3
from backend.catalog import identity
from typing import Any

from backend.capabilities.filters import BASE, normalize_filters, where_clause


def _rows(connection: sqlite3.Connection, sql: str, params: list[Any]) -> list[dict]:
    return [dict(r) for r in connection.execute(sql, params).fetchall()]


def search_papers(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    sort = args.get("sort") or "year_desc"
    if sort not in ("year_desc", "title"):
        raise ValueError("sort must be year_desc or title")
    page = max(int(args.get("page") or 1), 1)
    page_size = min(max(int(args.get("page_size") or 10), 1), 50)
    where, params = where_clause(filters)
    total = connection.execute(f"SELECT COUNT(*) {BASE}{where}", params).fetchone()[0]
    order = "p.year DESC, e.canonical_title" if sort == "year_desc" else "e.canonical_title"
    items = _rows(connection,
                  f"""SELECT e.entity_id, e.canonical_title AS title, p.venue, p.year, p.authors,
                             p.abstract, p.doi, p.arxiv_id, p.paper_url,
                             a.area_id, d.direction_id
                      {BASE}{where} ORDER BY {order} LIMIT ? OFFSET ?""",
                  [*params, page_size, (page - 1) * page_size])
    for item in items:
        item["has_abstract"] = bool((item.get("abstract") or "").strip())
        item.pop("abstract", None)
    return {"total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size, "items": items}


def sample_papers(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    limit = min(max(int(args.get("limit") or 5), 1), 20)
    result = search_papers(connection, {"filters": filters, "sort": "year_desc", "page": 1, "page_size": limit})
    result["note"] = "most recent papers under the filter; not a random sample"
    return result


def get_paper(connection: sqlite3.Connection, args: dict) -> dict:
    entity_id = identity.canonical(str(args.get("entity_id") or "").strip())
    if not entity_id:
        raise ValueError("entity_id is required")
    row = connection.execute(
        """SELECT e.entity_id, e.canonical_title AS title, e.first_year, e.last_year, e.record_count,
                  p.venue, p.year, p.authors, p.abstract, p.doi, p.arxiv_id, p.paper_url, p.pdf_url,
                  p.abstract_source_name, p.abstract_source_url
           FROM paper_entities e JOIN paper_records p ON p.record_id = e.canonical_record_id
           WHERE e.entity_id = ?""", (entity_id,)).fetchone()
    if not row:
        raise ValueError("entity not found")
    paper = dict(row)
    labels = connection.execute(
        """SELECT a.area_id, ad.name_zh AS area_name, a.area_secondary, a.confidence AS area_confidence,
                  a.model, a.prompt_version, a.is_non_paper, d.direction_id, dd.name_zh AS direction_name,
                  d.assigned_by, d.needs_review
           FROM entity_area_assignments a
           LEFT JOIN area_definitions ad ON ad.area_id = a.area_id
           LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
           LEFT JOIN direction_definitions dd ON dd.direction_id = d.direction_id
           WHERE a.entity_id = ?""", (entity_id,)).fetchone()
    paper["labels"] = dict(labels) if labels else None
    paper["appearances"] = _rows(connection,
                                 """SELECT p.paper_id, p.venue, p.year, p.list_status, p.paper_url, p.source_name
                                    FROM entity_memberships m JOIN paper_records p ON p.record_id = m.record_id
                                    WHERE m.entity_id = ? ORDER BY p.year, p.venue""", (entity_id,))
    from backend.catalog.publication import review
    for item in paper["appearances"]:
        item["publication_review"] = review(item["venue"], item["year"], item["paper_id"])
    return paper
