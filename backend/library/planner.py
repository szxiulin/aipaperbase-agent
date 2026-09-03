from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse


ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)

# Estimated average size per source used at planning time (bytes). The actual size is whatever the downloaded file measures.
ARXIV_ESTIMATED_BYTES = 1_500_000  # ~1.5 MB
OPEN_ESTIMATED_BYTES = 3_000_000   # ~3 MB

OPEN_ACCESS_HOSTS = {
    "arxiv.org",
    "openaccess.thecvf.com",
    "proceedings.neurips.cc",
    "aclanthology.org",
    "jmlr.org",
    "proceedings.mlr.press",
    "mlr.press",
    "proceedings.mlsys.org",
    "ojs.aaai.org",
    "ijcai.org",
    "www.ijcai.org",
    "ecva.net",
    "www.ecva.net",
    "openreview.net",
}

RESTRICTED_HOSTS = {
    "link.springer.com",
    "springer.com",
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "sciencedirect.com",
}


def arxiv_pdf_url(arxiv_id: str) -> str:
    arxiv_id = ARXIV_VERSION.sub("", arxiv_id.strip().casefold())
    return f"https://arxiv.org/pdf/{arxiv_id}"


def _host_of(url: str) -> str:
    if not url:
        return ""
    return (urlparse(url).netloc or "").casefold()


def _classify_url(url: str) -> str:
    host = _host_of(url)
    if not host:
        return "none"
    if host in OPEN_ACCESS_HOSTS:
        return "open"
    if host in RESTRICTED_HOSTS:
        return "restricted"
    return "unspecified"


def _chunked(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def build_plan(catalog_conn: sqlite3.Connection, entity_ids: list[str]) -> dict[str, Any]:
    """Build a download plan for a set of paper entities."""
    ids = list(dict.fromkeys(entity_ids))
    counts: dict[str, int] = defaultdict(int)
    items: list[dict[str, Any]] = []

    if not ids:
        return {
            "summary": {
                "total": 0,
                "counts": dict(counts),
                "downloadable": 0,
                "estimated_total_bytes": 0,
                "note": "空间为按来源估算的平均值，实际以下载后文件大小为准。",
            },
            "items": [],
        }

    titles: dict[str, dict[str, Any]] = {}
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in _chunked(ids, 500):
        placeholders = ",".join("?" for _ in chunk)
        for row in catalog_conn.execute(
            f"""SELECT e.entity_id, e.canonical_title AS title, p.authors, p.paper_url, p.doi
                FROM paper_entities e
                JOIN paper_records p ON p.record_id = e.canonical_record_id
                WHERE e.entity_id IN ({placeholders})""",
            chunk,
        ).fetchall():
            titles[row["entity_id"]] = dict(row)
        for row in catalog_conn.execute(
            f"""SELECT m.entity_id, p.arxiv_id, p.pdf_url, p.venue, p.year
                FROM entity_memberships m
                JOIN paper_records p ON p.record_id = m.record_id
                WHERE m.entity_id IN ({placeholders})
                ORDER BY m.entity_id, p.year, p.venue""",
            chunk,
        ).fetchall():
            members[row["entity_id"]].append(dict(row))

    for entity_id in ids:
        info = titles.get(entity_id, {})
        rows = members.get(entity_id, [])

        arxiv_ids = sorted({row["arxiv_id"] for row in rows if row["arxiv_id"]})
        open_urls = [row["pdf_url"] for row in rows if _classify_url(row["pdf_url"]) == "open"]
        restricted = any(_classify_url(row["pdf_url"]) == "restricted" for row in rows)
        unspecified = any(_classify_url(row["pdf_url"]) == "unspecified" for row in rows)

        if arxiv_ids:
            source = "arxiv"
            download_url = arxiv_pdf_url(arxiv_ids[0])
            estimated_bytes = ARXIV_ESTIMATED_BYTES
        elif open_urls:
            source = "open"
            download_url = open_urls[0]
            estimated_bytes = OPEN_ESTIMATED_BYTES
        elif restricted:
            source = "restricted"
            download_url = ""
            estimated_bytes = 0
        elif unspecified:
            source = "unspecified"
            download_url = ""
            estimated_bytes = 0
        else:
            source = "none"
            download_url = ""
            estimated_bytes = 0

        counts[source] += 1
        venues = sorted({row["venue"] for row in rows if row["venue"]})
        years = sorted({row["year"] for row in rows if row["year"]})
        items.append({
            "entity_id": entity_id,
            "title": info.get("title", ""),
            "authors": info.get("authors", ""),
            "paper_url": info.get("paper_url", ""),
            "doi": info.get("doi", ""),
            "source": source,
            "downloadable": source in {"arxiv", "open"},
            "download_url": download_url,
            "estimated_bytes": estimated_bytes,
            "arxiv_id": arxiv_ids[0] if arxiv_ids else "",
            "venues": venues,
            "years": years,
        })

    items.sort(key=lambda item: (not item["downloadable"], item["source"], item["title"]))
    estimated_total_bytes = sum(item["estimated_bytes"] for item in items)
    downloadable = sum(item["downloadable"] for item in items)
    return {
        "summary": {
            "total": len(items),
            "counts": dict(counts),
            "downloadable": downloadable,
            "estimated_total_bytes": estimated_total_bytes,
            "note": "空间为按来源估算的平均值，实际以下载后文件大小为准。",
        },
        "items": items,
    }
