from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from typing import Any, Callable

from backend.collections import service


def _filename_safe(value: str) -> str:
    safe = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "-._") else "_" for ch in value
    )
    return safe or "collection"


def _bibtex_entry_type(venue_type: str) -> str:
    return {"conference": "inproceedings", "journal": "article"}.get(venue_type, "misc")


def _bibtex_key(entity_id: str, title: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "", title or "")[:20] or "paper"
    return f"{base}{entity_id[-6:]}"


def _bibtex_authors(authors: str) -> str:
    if not authors:
        return ""
    return " and ".join(part.strip() for part in authors.split(";") if part.strip())


def _bibtex_escape(value: str) -> str:
    return (value or "").replace("&", r"\&").replace("_", r"\_")


def _appearances_text(row: dict[str, Any]) -> str:
    return "; ".join(f"{item['venue']} {item['year']}" for item in row.get("appearances", []))


def _export_csv(meta: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bytes, str, str]:
    buffer = io.StringIO()
    fieldnames = [
        "entity_id", "title", "authors", "venue", "venue_type", "first_year", "last_year",
        "venue_count", "doi", "arxiv_id", "paper_url", "appearances",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "entity_id": row["entity_id"],
            "title": row.get("title", ""),
            "authors": row.get("authors", ""),
            "venue": row.get("venue", ""),
            "venue_type": row.get("venue_type", ""),
            "first_year": row.get("first_year", ""),
            "last_year": row.get("last_year", ""),
            "venue_count": row.get("venue_count", ""),
            "doi": row.get("doi", ""),
            "arxiv_id": row.get("arxiv_id", ""),
            "paper_url": row.get("paper_url", ""),
            "appearances": _appearances_text(row),
        })
    content = "﻿" + buffer.getvalue()
    return content.encode("utf-8"), "text/csv; charset=utf-8", f"{_filename_safe(meta['name'])}.csv"


def _export_json(meta: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bytes, str, str]:
    payload = {
        "collection": meta["name"],
        "description": meta.get("description", ""),
        "source_type": meta.get("source_type", ""),
        "catalog_release_id": meta.get("catalog_release_id", ""),
        "exported_at": service.utc_now(),
        "papers": rows,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return content.encode("utf-8"), "application/json; charset=utf-8", f"{_filename_safe(meta['name'])}.json"


def _export_bibtex(meta: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bytes, str, str]:
    lines = [f"% {meta['name']} — exported from AIPaperbase Agent"]
    for row in rows:
        entry_type = _bibtex_entry_type(row.get("venue_type", ""))
        key = _bibtex_key(row["entity_id"], row.get("title", ""))
        fields: list[tuple[str, str]] = []
        title = _bibtex_escape(row.get("title", ""))
        if title:
            fields.append(("title", title))
        authors = _bibtex_authors(row.get("authors", ""))
        if authors:
            fields.append(("author", _bibtex_escape(authors)))
        year = row.get("last_year") or row.get("first_year")
        if year:
            fields.append(("year", str(year)))
        if row.get("doi"):
            fields.append(("doi", row["doi"]))
        if row.get("arxiv_id"):
            fields.append(("eprint", row["arxiv_id"]))
        if row.get("paper_url"):
            fields.append(("url", row["paper_url"]))
        lines.append(f"@{entry_type}{{{key},")
        for field, value in fields:
            lines.append(f"  {field} = {{{value}}},")
        lines.append("}")
        lines.append("")
    content = "\n".join(lines)
    return content.encode("utf-8"), "application/x-bibtex; charset=utf-8", f"{_filename_safe(meta['name'])}.bib"


def _export_markdown(meta: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bytes, str, str]:
    lines = [
        f"# {meta['name']}",
        "",
        f"来源类型：{meta.get('source_type', '')}；数据版本：{meta.get('catalog_release_id', '')}",
        "",
    ]
    for row in rows:
        title = row.get("title", "(无标题)")
        if row.get("paper_url"):
            line = f"- [{title}]({row['paper_url']})"
        else:
            line = f"- {title}"
        parts = []
        if row.get("authors"):
            parts.append(row["authors"])
        appearances = _appearances_text(row)
        if appearances:
            parts.append(appearances)
        if row.get("doi"):
            parts.append(f"DOI: {row['doi']}")
        if parts:
            line += f" — {' · '.join(parts)}"
        lines.append(line)
    content = "\n".join(lines) + "\n"
    return content.encode("utf-8"), "text/markdown; charset=utf-8", f"{_filename_safe(meta['name'])}.md"


EXPORTERS: dict[str, Callable[[dict[str, Any], list[dict[str, Any]]], tuple[bytes, str, str]]] = {
    "csv": _export_csv,
    "json": _export_json,
    "bibtex": _export_bibtex,
    "markdown": _export_markdown,
}


def export_collection(
    user_conn: sqlite3.Connection,
    catalog_conn: sqlite3.Connection,
    collection_id: str,
    format: str,
) -> tuple[bytes, str, str]:
    if format not in EXPORTERS:
        raise ValueError(f"不支持的导出格式: {format}")
    meta = service.collection_summary(user_conn, collection_id)
    rows = service.export_rows(user_conn, catalog_conn, collection_id)
    return EXPORTERS[format](meta, rows)
