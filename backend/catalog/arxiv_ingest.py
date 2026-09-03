"""arXiv paper ingestion into the catalog (venue='arXiv', peer with journals/conferences) + merge.

Background: many papers are first posted as preprints on arXiv and later accepted at journals/conferences.
To avoid duplicate entities, on ingestion records are merged with existing records by normalized_title
(the preprint-first, later-accepted case is automatically merged into the same entity, and the frontend
shows multiple appearances).

Merge rules (asymmetric, conservative):
- Idempotent: record_id = sha256("arxiv\0<arxiv_id>"); re-ingesting the same arXiv record returns exists;
- Title merge: normalized_title (NFKC+casefold+strip non-word chars) exactly matches an existing paper_records
  → that arXiv record joins the existing entity (entity_memberships.match_method='arxiv_title'),
  updating entity statistics; no new entity is created;
- No match → create a new entity (venue='arXiv', venue_type='preprint', list_status='rolling').

Data consistency:
- Each ingestion day gets its own release (arxiv-YYYYMMDD) + source_files aggregate row (arxiv:YYYYMMDD.csv),
  keeping foreign keys intact, so arXiv records are not overwritten on a full CSV rebuild (the rebuild only reads
  the CSV directory);
- After ingestion, incrementally sync the FTS index (search.index_record).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any

from backend.catalog import search as search_mod
from backend.catalog.database import ROOT


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today() -> datetime:
    return datetime.now(timezone.utc).date()


def arxiv_record_id(arxiv_id: str) -> str:
    """Idempotent arXiv record id: the same arXiv record always maps to the same record_id."""
    return hashlib.sha256(f"arxiv\0{arxiv_id.strip()}".encode("utf-8")).hexdigest()


def _entity_id(seed: str) -> str:
    return "ape_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w]+", "", value)


def release_key(day=None) -> str:
    day = day or _today()
    return f"arxiv-{day:%Y%m%d}"


def _source_file(day) -> str:
    return f"arxiv:{day:%Y%m%d}.csv"


def ensure_release(connection: sqlite3.Connection, day) -> tuple[str, str]:
    """Ensure the arXiv-sourced release + source_files aggregate rows exist; returns (release_id, source_file)."""
    rid = release_key(day)
    sf = _source_file(day)
    if not connection.execute(
        "SELECT 1 FROM data_releases WHERE release_id = ?", (rid,)
    ).fetchone():
        connection.execute(
            "INSERT INTO data_releases (release_id, created_at, source_digest,"
            " source_file_count, record_count, status) VALUES (?, ?, ?, 0, 0, 'ready')",
            (rid, _now_iso(), f"arxiv-{day:%Y%m%d}"),
        )
    if not connection.execute(
        "SELECT 1 FROM source_files WHERE source_file = ?", (sf,)
    ).fetchone():
        connection.execute(
            "INSERT INTO source_files (source_file, release_id, sha256, row_count,"
            " venue, venue_type, year, list_status) VALUES (?, ?, ?, 0, 'arXiv', 'preprint', ?, 'rolling')",
            (sf, rid, f"arxiv-{day:%Y%m%d}", day.year),
        )
    return rid, sf


def ingest_entry(
    connection: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    day=None,
) -> dict[str, Any]:
    """Ingest one arXiv metadata record (including merge). entry fields:
    arxiv_id / title / authors(list[str]) / abstract / year(int) / doi / paper_url / pdf_url.
    Returns {status: 'new'|'merged'|'exists', entity_id, record_id, matched_title?}.
    The caller is responsible for commit.
    """
    day = day or _today()
    arxiv_id = (entry.get("arxiv_id") or "").strip()
    if not arxiv_id:
        raise ValueError("arxiv_id 必填")
    rid = arxiv_record_id(arxiv_id)

    # idempotent: already ingested, return directly
    existing = connection.execute(
        "SELECT m.entity_id FROM paper_records p"
        " JOIN entity_memberships m ON m.record_id = p.record_id"
        " WHERE p.record_id = ?",
        (rid,),
    ).fetchone()
    if existing:
        return {"status": "exists", "entity_id": existing["entity_id"], "record_id": rid}

    release_id, source_file = ensure_release(connection, day)
    now = _now_iso()
    title = (entry.get("title") or "").strip()
    authors = ", ".join(entry.get("authors") or [])
    abstract = (entry.get("abstract") or "").strip()
    year = int(entry.get("year") or day.year)
    doi = (entry.get("doi") or "").strip()
    paper_url = entry.get("paper_url") or f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = entry.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    ntitle = normalize_title(title)

    # merge: normalized_title exactly matches an existing record → join the existing entity
    matched = connection.execute(
        """SELECT p.record_id, p.title, p.year, p.venue
           FROM paper_records p
           WHERE p.normalized_title = ? AND p.record_id != ?
           ORDER BY p.year DESC LIMIT 1""",
        (ntitle, rid),
    ).fetchone()
    if matched:
        entity = connection.execute(
            "SELECT entity_id, canonical_record_id FROM paper_entities"
            " WHERE canonical_record_id = ?",
            (matched["record_id"],),
        ).fetchone()
        if entity:
            connection.execute(
                """INSERT INTO paper_records (record_id, paper_id, venue, venue_type, year,
                       track, title, normalized_title, authors, abstract, abstract_source_name,
                       abstract_source_url, abstract_source_tier, abstract_fetched_at, doi,
                       arxiv_id, paper_url, pdf_url, source_name, source_url, source_tier,
                       verification_status, list_status, fetched_at, source_file, imported_at,
                       release_id)
                   VALUES (?, ?, 'arXiv', 'preprint', ?, '', ?, ?, ?, ?, 'arXiv', ?, 'official',
                       ?, ?, ?, ?, ?, 'arXiv', 'https://arxiv.org', 'official', 'verified',
                       'rolling', ?, ?, ?, ?)""",
                (
                    rid, arxiv_id, year, title, ntitle, authors, abstract,
                    paper_url, now, doi, arxiv_id, paper_url, pdf_url,
                    now, source_file, now, release_id,
                ),
            )
            connection.execute(
                """INSERT INTO entity_memberships (record_id, entity_id, match_method,
                       evidence_value, confidence, decision_status, is_canonical, created_at, release_id)
                   VALUES (?, ?, 'arxiv_title', ?, 'exact', 'auto_accepted', 0, ?, ?)""",
                (rid, entity["entity_id"], ntitle, now, release_id),
            )
            # update entity statistics
            connection.execute(
                """UPDATE paper_entities SET
                       record_count = record_count + 1,
                       venue_count = (SELECT COUNT(DISTINCT venue) FROM paper_records
                                      WHERE record_id IN (SELECT record_id FROM entity_memberships
                                                          WHERE entity_id = ?)),
                       last_year = MAX(last_year, ?),
                       entity_status = 'merged'
                   WHERE entity_id = ?""",
                (entity["entity_id"], year, entity["entity_id"]),
            )
            search_mod.ensure_index(connection)
            try:
                search_mod.index_record(connection, rid)
            except sqlite3.Error:
                pass  # FTS indexing failure does not block metadata ingestion (search falls back to LIKE)
            return {
                "status": "merged",
                "entity_id": entity["entity_id"],
                "record_id": rid,
                "matched_title": matched["title"],
                "matched_venue": matched["venue"],
            }

    # create a new entity
    entity_id = _entity_id(f"arxiv:{arxiv_id}")
    connection.execute(
        """INSERT INTO paper_records (record_id, paper_id, venue, venue_type, year,
               track, title, normalized_title, authors, abstract, abstract_source_name,
               abstract_source_url, abstract_source_tier, abstract_fetched_at, doi,
               arxiv_id, paper_url, pdf_url, source_name, source_url, source_tier,
               verification_status, list_status, fetched_at, source_file, imported_at,
               release_id)
           VALUES (?, ?, 'arXiv', 'preprint', ?, '', ?, ?, ?, ?, 'arXiv', ?, 'official',
               ?, ?, ?, ?, ?, 'arXiv', 'https://arxiv.org', 'official', 'verified',
               'rolling', ?, ?, ?, ?)""",
        (
            rid, arxiv_id, year, title, ntitle, authors, abstract,
            paper_url, now, doi, arxiv_id, paper_url, pdf_url,
            now, source_file, now, release_id,
        ),
    )
    connection.execute(
        """INSERT INTO paper_entities (entity_id, canonical_record_id, canonical_title,
               first_year, last_year, record_count, venue_count, entity_status, created_at, release_id)
           VALUES (?, ?, ?, ?, ?, 1, 1, 'single', ?, ?)""",
        (entity_id, rid, title, year, year, now, release_id),
    )
    connection.execute(
        """INSERT INTO entity_memberships (record_id, entity_id, match_method,
               evidence_value, confidence, decision_status, is_canonical, created_at, release_id)
           VALUES (?, ?, 'arxiv_id', ?, 'single', 'auto_accepted', 1, ?, ?)""",
        (rid, entity_id, arxiv_id, now, release_id),
    )
    search_mod.ensure_index(connection)
    try:
        search_mod.index_record(connection, rid)
    except sqlite3.Error:
        pass  # FTS indexing failure does not block metadata ingestion (search falls back to LIKE)
    return {"status": "new", "entity_id": entity_id, "record_id": rid}
