"""Conservative paper identity resolution used before a local ingest job.

The catalog already owns the authoritative entity graph.  This module only
resolves aliases and reports ambiguous candidates; it never mutates catalog
identity data.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any


_ARXIV_VERSION = re.compile(r"v\d+$", re.I)


def arxiv_base(value: str) -> str:
    return _ARXIV_VERSION.sub("", (value or "").strip().casefold())


def normalize_title(value: str) -> str:
    return re.sub(r"\W+", "", (value or "").casefold(), flags=re.UNICODE)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def resolve_entity_ids(catalog_conn: sqlite3.Connection, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve requested ids to canonical catalog entities without automatic fuzzy merging."""
    result: dict[str, dict[str, Any]] = {}
    aliases = _has_table(catalog_conn, "entity_aliases")
    for requested in dict.fromkeys(entity_ids):
        row = catalog_conn.execute(
            "SELECT entity_id, canonical_title FROM paper_entities WHERE entity_id = ?", (requested,)
        ).fetchone()
        if row is None and aliases:
            row = catalog_conn.execute(
                "SELECT e.entity_id, e.canonical_title FROM entity_aliases a "
                "JOIN paper_entities e ON e.entity_id=a.entity_id WHERE a.alias_entity_id=?", (requested,)
            ).fetchone()
        if row is None:
            result[requested] = {
                "requested_entity_id": requested, "canonical_entity_id": requested,
                "status": "failed", "reason_code": "unknown_entity", "message": "目录中未找到该论文实体。",
            }
        else:
            result[requested] = {
                "requested_entity_id": requested, "canonical_entity_id": row["entity_id"],
                "title": row["canonical_title"], "status": "catalog_only", "reason_code": "", "message": "",
            }
    return result


def candidate_matches(item: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only candidates that meet an exact DOI/arXiv/title+author-or-year rule."""
    doi = (item.get("doi") or "").casefold().removeprefix("https://doi.org/")
    arxiv_id = arxiv_base(item.get("arxiv_id", ""))
    title = normalize_title(item.get("title", ""))
    authors = (item.get("authors") or "").casefold()
    years = {int(y) for y in item.get("years", []) if str(y).isdigit()}
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        cdoi = (candidate.get("doi") or "").casefold().removeprefix("https://doi.org/")
        carxiv = arxiv_base(candidate.get("arxiv_id", ""))
        if doi and doi == cdoi:
            accepted.append({**candidate, "match_basis": "doi"})
            continue
        if arxiv_id and arxiv_id == carxiv:
            accepted.append({**candidate, "match_basis": "arxiv_id"})
            continue
        same_title = title and title == normalize_title(candidate.get("title", ""))
        author_match = bool(authors and any(a.casefold() in authors for a in candidate.get("authors", []) if a))
        year_match = candidate.get("year") in years
        if same_title and (author_match or year_match):
            accepted.append({**candidate, "match_basis": "title_author_or_year"})
    return accepted
