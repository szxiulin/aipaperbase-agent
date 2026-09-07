"""Read-only, shared reconciliation view of the local PDF -> parse -> vector pipeline."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from backend.rag import parse_store


def _markdown_path(row: dict[str, Any], entity_id: str) -> Path:
    configured = row.get("markdown_path") or ""
    return Path(configured) if configured else parse_store.parsed_path(entity_id)


def snapshot(
    catalog_conn: sqlite3.Connection,
    *,
    downloads_conn: sqlite3.Connection | None = None,
    parsed_conn: sqlite3.Connection | None = None,
    document_chunk_counts: dict[str, int] | None = None,
    pipeline_fingerprint: str = "",
) -> dict[str, Any]:
    """Reconcile manifests with actual files and actual vector counts, without writes.

    ``document_chunk_counts=None`` is deliberately different from ``{}``: it
    means the vector store could not be queried, so index state is unknown.
    """
    downloads = [] if downloads_conn is None else [dict(row) for row in downloads_conn.execute("SELECT * FROM downloads")]
    parsed = {} if parsed_conn is None else {
        row["entity_id"]: dict(row) for row in parsed_conn.execute("SELECT * FROM parsed_documents")
    }
    qdrant_available = document_chunk_counts is not None
    actual_counts = document_chunk_counts or {}
    download_map = {row["entity_id"]: row for row in downloads}
    ids = list(dict.fromkeys([row["entity_id"] for row in downloads] + list(parsed) + list(actual_counts)))
    titles: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        titles.update({row["entity_id"]: row["canonical_title"] for row in catalog_conn.execute(
            f"SELECT entity_id, canonical_title FROM paper_entities WHERE entity_id IN ({placeholders})", chunk)})

    items: list[dict[str, Any]] = []
    for entity_id in ids:
        downloaded, parsed_row = download_map.get(entity_id, {}), parsed.get(entity_id, {})
        pdf_registered = downloaded.get("status") in {"success", "duplicate"}
        pdf_path = Path(downloaded.get("file_path") or "")
        pdf_file_exists = bool(downloaded.get("file_path")) and pdf_path.is_file()
        parse_success = parsed_row.get("status") == "success"
        markdown = _markdown_path(parsed_row, entity_id)
        markdown_exists = parse_success and markdown.exists()
        actual_count = int(actual_counts.get(entity_id, 0)) if qdrant_available else 0
        registered_count = int(parsed_row.get("indexed_chunk_count") or 0)
        current_hash = parsed_row.get("parsed_sha256") or ""
        indexed_hash = parsed_row.get("indexed_parsed_sha256") or ""
        indexed_fingerprint = parsed_row.get("indexed_pipeline_fingerprint") or ""
        actual_hash = hashlib.sha256(markdown.read_bytes()).hexdigest() if markdown_exists else ""

        if entity_id in actual_counts and not parsed_row:
            index_status, failure = "registry_missing", "Qdrant 存在向量，但缺少解析登记。"
        elif not parse_success:
            index_status = "pdf_only" if pdf_registered else ""
            failure = parsed_row.get("error", "") or downloaded.get("error", "")
        elif not markdown_exists:
            index_status, failure = "parsed_only", "解析登记成功，但 Markdown 文件不存在。"
        elif not qdrant_available:
            index_status, failure = "qdrant_unavailable", "无法读取 Qdrant，索引状态未知。"
        elif actual_count == 0:
            index_status, failure = "index_missing", "解析结果存在，但 Qdrant 中没有该论文的向量。"
        elif not current_hash or not indexed_hash or not indexed_fingerprint or registered_count <= 0:
            index_status, failure = "registry_missing", "Qdrant 存在向量，但索引登记不完整。"
        elif (actual_hash != current_hash or indexed_hash != current_hash
              or indexed_fingerprint != pipeline_fingerprint
              or actual_count != registered_count):
            index_status, failure = "index_stale", "Markdown、索引管线或 chunk 数与登记状态不一致。"
        else:
            index_status, failure = "indexed_current", ""

        items.append({
            "entity_id": entity_id, "title": titles.get(entity_id, entity_id),
            "pdf_status": downloaded.get("status", ""), "pdf_source": downloaded.get("source", ""),
            "pdf_file_status": "present" if pdf_file_exists else ("unverified" if pdf_registered else "missing"),
            "parse_status": parsed_row.get("status", ""), "index_status": index_status,
            "chunk_count": actual_count if qdrant_available else registered_count,
            "actual_chunk_count": actual_count if qdrant_available else None,
            "indexed_chunk_count": registered_count, "markdown_exists": markdown_exists,
            "parsed_sha256": current_hash, "indexed_parsed_sha256": indexed_hash,
            "actual_parsed_sha256": actual_hash,
            "pipeline_fingerprint": indexed_fingerprint,
            "updated_at": max(downloaded.get("updated_at", ""), parsed_row.get("updated_at", "")),
            "failure": failure,
        })
    summary = {
        "registered_pdfs": sum(row["pdf_status"] in {"success", "duplicate"} for row in items),
        "pdf_files_present": sum(row["pdf_file_status"] == "present" for row in items),
        "valid_pdfs": sum(row["pdf_file_status"] == "present" for row in items),
        "parsed": sum(row["parse_status"] == "success" and row["markdown_exists"] for row in items),
        "indexed_current": sum(row["index_status"] == "indexed_current" for row in items) if qdrant_available else None,
        "index_stale": sum(row["index_status"] == "index_stale" for row in items),
        "index_missing": sum(row["index_status"] == "index_missing" for row in items),
        "registry_missing": sum(row["index_status"] == "registry_missing" for row in items),
        "qdrant_available": qdrant_available,
        "chunks": sum(actual_counts.values()) if qdrant_available else None,
        "last_ingest_at": max((row["updated_at"] for row in items if row["index_status"] == "indexed_current"), default=""),
    }
    return {"summary": summary, "items": sorted(items, key=lambda row: row["updated_at"], reverse=True)}
