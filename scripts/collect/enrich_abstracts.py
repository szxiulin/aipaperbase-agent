#!/usr/bin/env python3
"""Enrich annual catalog CSV files with traceable paper abstracts.

Priority is deliberately conservative:
1. keep an existing abstract and its provenance;
2. ACL Anthology official XML;
3. Crossref publisher-registered metadata;
4. Hugging Face ai-conferences exact identifier/title matches;
5. optional Semantic Scholar batch fallback for records with DOI/arXiv/ACL IDs.

No fuzzy title-only match is accepted. CSV files are replaced atomically only
after the full enrichment pass succeeds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlencode

import pandas as pd
import requests
from lxml import etree, html


ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = ROOT / "data" / "catalog"
CSV_ROOTS = (CATALOG_ROOT / "conferences", CATALOG_ROOT / "journals")
REPORT_PATH = CATALOG_ROOT / "reports" / "abstract_enrichment.json"
HF_DATASET_URL = "https://huggingface.co/datasets/ai-conferences/all-papers"
REVIEWARENA_URL = "https://huggingface.co/datasets/anonymousNeurIPS2026submission4281/reviewarena"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"

LEGACY_FIELDS = [
    "paper_id", "venue", "venue_type", "year", "track", "title", "authors",
    "doi", "arxiv_id", "paper_url", "pdf_url", "source_name", "source_url",
    "source_tier", "verification_status", "list_status", "fetched_at",
]
ABSTRACT_FIELDS = [
    "abstract", "abstract_source_name", "abstract_source_url",
    "abstract_source_tier", "abstract_fetched_at",
]
FIELDS = LEGACY_FIELDS[:7] + ABSTRACT_FIELDS + LEGACY_FIELDS[7:]

JOURNAL_ISSNS = {
    "AIJ": "0004-3702",
    "TPAMI": "0162-8828",
    "IJCV": "0920-5691",
    "TOG": "0730-0301",
    "TIP": "1057-7149",
    "TKDE": "1041-4347",
    "TOIS": "1046-8188",
    "TVCG": "1077-2626",
}
OPENALEX_SOURCE_IDS = {
    "JMLR": "S118988714",
}
OFFICIAL_ABSTRACT_HOSTS = {
    "openaccess.thecvf.com": "CVF Open Access official paper page",
    "proceedings.neurips.cc": "NeurIPS Proceedings official paper page",
    "proceedings.mlsys.org": "MLSys Proceedings official paper page",
    "www.jmlr.org": "JMLR official paper page",
    "jmlr.org": "JMLR official paper page",
    "www.ijcai.org": "IJCAI Proceedings official paper page",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_abstract(value: object) -> str:
    raw = clean(value)
    if not raw:
        return ""
    if "<" in raw and ">" in raw:
        try:
            raw = clean(" ".join(html.fromstring(f"<div>{raw}</div>").itertext()))
        except (etree.ParserError, ValueError):
            raw = clean(re.sub(r"<[^>]+>", " ", raw))
    return raw


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[^\w]+", "", text)


def normalize_venue(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).casefold())


def normalize_doi(value: object) -> str:
    doi = clean(value).casefold()
    return doi.removeprefix("https://doi.org/").removeprefix("doi:")


def normalize_arxiv(value: object) -> str:
    arxiv_id = clean(value).casefold().removeprefix("arxiv:")
    return re.sub(r"v\d+$", "", arxiv_id)


def discover_csv_files() -> list[Path]:
    return sorted(path for root in CSV_ROOTS for path in root.rglob("*.csv"))


def read_catalogs(files: Iterable[Path]) -> dict[Path, list[dict[str, str]]]:
    catalogs: dict[Path, list[dict[str, str]]] = {}
    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            if header not in (LEGACY_FIELDS, FIELDS):
                raise ValueError(f"CSV 字段不受支持: {path.relative_to(ROOT)}")
            rows = []
            for raw in reader:
                row = {field: clean(raw.get(field)) for field in FIELDS}
                rows.append(row)
            catalogs[path] = rows
    return catalogs


def iter_rows(catalogs: dict[Path, list[dict[str, str]]]):
    for path, rows in catalogs.items():
        for row in rows:
            yield path, row


def apply_abstract(
    row: dict[str, str], *, abstract: object, source_name: str,
    source_url: str, source_tier: str, fetched_at: str,
) -> bool:
    text = clean_abstract(abstract)
    if row["abstract"] or not text:
        return False
    row.update({
        "abstract": text,
        "abstract_source_name": clean(source_name),
        "abstract_source_url": clean(source_url),
        "abstract_source_tier": clean(source_tier),
        "abstract_fetched_at": clean(fetched_at),
    })
    return True


class CachedSession:
    def __init__(self, cache_dir: Path, *, delay: float = 0.15):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AIPaperbase Agent-abstracts/0.1 (public research metadata)"
        })

    def get_bytes(self, url: str, suffix: str) -> bytes:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.cache_dir / f"{digest}{suffix}"
        if path.exists():
            return path.read_bytes()
        response = self.session.get(url, timeout=120)
        response.raise_for_status()
        path.write_bytes(response.content)
        time.sleep(self.delay)
        return response.content


def enrich_acl_official(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession, fetched_at: str,
) -> int:
    source_urls = sorted({
        row["source_url"] for _, row in iter_rows(catalogs)
        if row["venue"] in {"ACL", "EMNLP", "NAACL"}
        and row["source_url"].startswith("https://raw.githubusercontent.com/acl-org/")
    })
    by_paper_id: dict[str, str] = {}
    for url in source_urls:
        root = etree.fromstring(session.get_bytes(url, ".xml"))
        for paper in root.findall(".//paper"):
            url_node = paper.find("url")
            abstract_node = paper.find("abstract")
            paper_id = clean(" ".join(url_node.itertext())) if url_node is not None else ""
            abstract = clean_abstract(" ".join(abstract_node.itertext())) if abstract_node is not None else ""
            if paper_id and abstract:
                by_paper_id[paper_id] = abstract

    updated = 0
    for _, row in iter_rows(catalogs):
        abstract = by_paper_id.get(row["paper_id"], "")
        if apply_abstract(
            row, abstract=abstract, source_name="ACL Anthology official XML",
            source_url=row["source_url"], source_tier="official", fetched_at=fetched_at,
        ):
            updated += 1
    return updated


def enrich_crossref(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession, fetched_at: str,
) -> int:
    targets = sorted({
        (row["venue"], int(row["year"])) for _, row in iter_rows(catalogs)
        if row["venue"] in JOURNAL_ISSNS and not row["abstract"]
    })
    abstracts: dict[str, str] = {}
    for venue, year in targets:
        cursor = "*"
        while cursor:
            params = {
                "filter": f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article",
                "rows": "1000",
                "cursor": cursor,
                "mailto": "metadata@apexpaperrag.local",
            }
            url = f"https://api.crossref.org/journals/{JOURNAL_ISSNS[venue]}/works?{urlencode(params)}"
            payload = json.loads(session.get_bytes(url, ".json"))
            message = payload["message"]
            items = message.get("items", [])
            for item in items:
                doi = normalize_doi(item.get("DOI"))
                abstract = clean_abstract(item.get("abstract"))
                if doi and abstract:
                    abstracts[doi] = abstract
            next_cursor = clean(message.get("next-cursor"))
            if not items or len(items) < 1000 or next_cursor == cursor:
                break
            cursor = next_cursor

    updated = 0
    for _, row in iter_rows(catalogs):
        doi = normalize_doi(row["doi"])
        if not doi:
            continue
        if apply_abstract(
            row, abstract=abstracts.get(doi),
            source_name="Crossref publisher-registered metadata",
            source_url=f"https://api.crossref.org/works/{quote(doi, safe='/')}",
            source_tier="official_plus_secondary", fetched_at=fetched_at,
        ):
            updated += 1
    return updated


def unique_index(frame: pd.DataFrame, key_builder) -> dict[object, dict]:
    candidates: dict[object, list[dict]] = defaultdict(list)
    for row in frame.to_dict("records"):
        key = key_builder(row)
        if key:
            candidates[key].append(row)
    result: dict[object, dict] = {}
    for key, rows in candidates.items():
        abstracts = {clean_abstract(row.get("abstract")) for row in rows if clean_abstract(row.get("abstract"))}
        if len(abstracts) == 1:
            result[key] = next(row for row in rows if clean_abstract(row.get("abstract")))
    return result


def enrich_huggingface(
    catalogs: dict[Path, list[dict[str, str]]], parquet_path: Path, fetched_at: str,
) -> int:
    columns = [
        "paper_id", "conference", "year", "source_paper_id", "source", "title",
        "abstract", "doi", "arxiv_id",
    ]
    frame = pd.read_parquet(parquet_path, columns=columns)
    frame = frame[(frame["year"] >= 2023) & frame["abstract"].notna()].copy()
    frame["_title"] = frame["title"].map(normalize_title)
    frame = frame[frame["_title"] != ""]

    by_doi = unique_index(frame, lambda row: normalize_doi(row.get("doi")))
    by_arxiv = unique_index(frame, lambda row: normalize_arxiv(row.get("arxiv_id")))
    by_source_id = unique_index(
        frame,
        lambda row: (
            normalize_venue(row.get("conference")), int(row.get("year")),
            clean(row.get("source_paper_id")),
        ) if clean(row.get("source_paper_id")) else None,
    )
    by_title = unique_index(
        frame,
        lambda row: (normalize_venue(row.get("conference")), int(row.get("year")), row["_title"]),
    )

    updated = 0
    for _, row in iter_rows(catalogs):
        if row["abstract"]:
            continue
        venue_year = (normalize_venue(row["venue"]), int(row["year"]))
        candidates = [
            by_doi.get(normalize_doi(row["doi"])) if row["doi"] else None,
            by_arxiv.get(normalize_arxiv(row["arxiv_id"])) if row["arxiv_id"] else None,
            by_source_id.get((*venue_year, row["paper_id"])),
            by_title.get((*venue_year, normalize_title(row["title"]))),
        ]
        match = next((candidate for candidate in candidates if candidate), None)
        if not match or match["_title"] != normalize_title(row["title"]):
            continue
        source = clean(match.get("source")) or "aggregated metadata"
        if apply_abstract(
            row, abstract=match.get("abstract"),
            source_name=f"Hugging Face ai-conferences ({source})",
            source_url=HF_DATASET_URL,
            source_tier="third_party_crosschecked", fetched_at=fetched_at,
        ):
            updated += 1
    return updated


def enrich_reviewarena_tmlr(
    catalogs: dict[Path, list[dict[str, str]]], parquet_path: Path, fetched_at: str,
) -> int:
    frame = pd.read_parquet(parquet_path, columns=["forum_id", "title", "abstract"])
    candidates: dict[str, dict] = {}
    for item in frame.to_dict("records"):
        forum_id = clean(item.get("forum_id"))
        abstract = clean_abstract(item.get("abstract"))
        if forum_id and abstract:
            candidates[forum_id] = item
    updated = 0
    for _, row in iter_rows(catalogs):
        if row["venue"] != "TMLR" or row["abstract"]:
            continue
        match = candidates.get(row["paper_id"])
        if not match or normalize_title(match.get("title")) != normalize_title(row["title"]):
            continue
        if apply_abstract(
            row, abstract=match.get("abstract"), source_name="ReviewArena OpenReview snapshot (2026-04)",
            source_url=REVIEWARENA_URL, source_tier="third_party_crosschecked",
            fetched_at=fetched_at,
        ):
            updated += 1
    return updated


def openalex_abstract(inverted_index: object) -> str:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for token, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                positioned.append((position, clean(token)))
    positioned.sort()
    return clean(" ".join(token for _, token in positioned))


def enrich_openalex(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession, fetched_at: str,
) -> int:
    rows_by_doi: dict[str, list[dict[str, str]]] = defaultdict(list)
    for _, row in iter_rows(catalogs):
        doi = normalize_doi(row["doi"])
        if not row["abstract"] and doi:
            rows_by_doi[doi].append(row)

    updated = 0
    identifiers = sorted(rows_by_doi)
    for batch_index, dois in enumerate(batched(identifiers, 50), 1):
        params = {
            "filter": "doi:" + "|".join(dois),
            "select": "id,doi,title,abstract_inverted_index",
            "per-page": "50",
            "mailto": "metadata@apexpaperrag.local",
        }
        url = f"{OPENALEX_WORKS_URL}?{urlencode(params, safe='|:/')}"
        payload = json.loads(session.get_bytes(url, ".json"))
        for work in payload.get("results", []):
            doi = normalize_doi(work.get("doi"))
            abstract = openalex_abstract(work.get("abstract_inverted_index"))
            if not doi or not abstract:
                continue
            for row in rows_by_doi.get(doi, []):
                if normalize_title(work.get("title")) != normalize_title(row["title"]):
                    continue
                if apply_abstract(
                    row, abstract=abstract, source_name="OpenAlex",
                    source_url=clean(work.get("id")) or "https://openalex.org/",
                    source_tier="third_party_crosschecked", fetched_at=fetched_at,
                ):
                    updated += 1
        if batch_index % 20 == 0 or batch_index * 50 >= len(identifiers):
            print(f"OpenAlex: {min(batch_index * 50, len(identifiers))}/{len(identifiers)} DOIs", flush=True)
    return updated


def enrich_openalex_sources(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession, fetched_at: str,
) -> int:
    candidates: dict[tuple[str, int, str], dict] = {}
    for venue, source_id in OPENALEX_SOURCE_IDS.items():
        cursor = "*"
        while cursor:
            params = {
                "filter": (
                    f"primary_location.source.id:{source_id},"
                    "from_publication_date:2023-01-01,to_publication_date:2026-12-31"
                ),
                "select": "id,title,publication_year,abstract_inverted_index",
                "per-page": "200",
                "cursor": cursor,
                "mailto": "metadata@apexpaperrag.local",
            }
            url = f"{OPENALEX_WORKS_URL}?{urlencode(params, safe='|:/')}"
            payload = json.loads(session.get_bytes(url, ".json"))
            results = payload.get("results", [])
            for work in results:
                title = normalize_title(work.get("title"))
                year = work.get("publication_year")
                abstract = openalex_abstract(work.get("abstract_inverted_index"))
                if title and isinstance(year, int) and abstract:
                    candidates[(venue, year, title)] = work
            next_cursor = clean(payload.get("meta", {}).get("next_cursor"))
            if not results or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    updated = 0
    for _, row in iter_rows(catalogs):
        if row["abstract"] or row["venue"] not in OPENALEX_SOURCE_IDS:
            continue
        match = candidates.get((row["venue"], int(row["year"]), normalize_title(row["title"])))
        if not match:
            continue
        if apply_abstract(
            row, abstract=openalex_abstract(match.get("abstract_inverted_index")),
            source_name="OpenAlex", source_url=clean(match.get("id")),
            source_tier="third_party_crosschecked", fetched_at=fetched_at,
        ):
            updated += 1
    return updated


def extract_official_page_abstract(content: bytes, expected_title: str) -> str:
    tree = html.fromstring(content)
    expected = normalize_title(expected_title)
    page_titles = {
        normalize_title(" ".join(node.itertext()))
        for node in tree.xpath("//h1 | //h2 | //h3 | //h4 | //*[@id='papertitle']")
    }
    if expected not in page_titles:
        return ""

    direct = tree.xpath(
        "//*[@id='abstract'] | "
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' abstract ')]"
    )
    for node in direct:
        text = clean_abstract(" ".join(node.itertext()))
        if len(text) >= 50 and normalize_title(text) != "abstract":
            return re.sub(r"^abstract\s*", "", text, flags=re.IGNORECASE)

    headings = tree.xpath(
        "//*[self::h1 or self::h2 or self::h3 or self::h4]"
        "[translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='abstract']"
    )
    for heading in headings:
        parts = []
        sibling = heading.getnext()
        while sibling is not None and clean(getattr(sibling, "tag", "")).casefold() not in {"h1", "h2", "h3", "h4"}:
            text = clean(" ".join(sibling.itertext()))
            if text:
                parts.append(text)
            sibling = sibling.getnext()
        abstract = clean_abstract(" ".join(parts))
        if len(abstract) >= 50:
            return abstract
    return ""


def enrich_official_pages(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession,
    fetched_at: str, workers: int,
) -> int:
    from urllib.parse import urlparse

    targets: list[dict[str, str]] = []
    for _, row in iter_rows(catalogs):
        host = urlparse(row["paper_url"]).netloc.casefold()
        if not row["abstract"] and host in OFFICIAL_ABSTRACT_HOSTS:
            targets.append(row)

    def fetch(row: dict[str, str]) -> tuple[dict[str, str], str, str]:
        host = urlparse(row["paper_url"]).netloc.casefold()
        try:
            content = session.get_bytes(row["paper_url"], ".html")
            return row, extract_official_page_abstract(content, row["title"]), OFFICIAL_ABSTRACT_HOSTS[host]
        except (requests.RequestException, etree.ParserError, ValueError):
            return row, "", OFFICIAL_ABSTRACT_HOSTS[host]

    updated = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = [executor.submit(fetch, row) for row in targets]
        for future in as_completed(futures):
            row, abstract, source_name = future.result()
            if apply_abstract(
                row, abstract=abstract, source_name=source_name,
                source_url=row["paper_url"], source_tier="official", fetched_at=fetched_at,
            ):
                updated += 1
            completed += 1
            if completed % 200 == 0 or completed == len(targets):
                print(f"Official pages: {completed}/{len(targets)}", flush=True)
    return updated


def semantic_scholar_id(row: dict[str, str]) -> str:
    if row["doi"]:
        return f"DOI:{normalize_doi(row['doi'])}"
    if row["arxiv_id"]:
        return f"ARXIV:{normalize_arxiv(row['arxiv_id'])}"
    if row["venue"] in {"ACL", "EMNLP", "NAACL"} and row["paper_id"]:
        return f"ACL:{row['paper_id']}"
    return ""


def batched(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def s2_batch(
    session: CachedSession, ids: list[str], api_key: str, cache_dir: Path,
) -> list[dict | None]:
    digest = hashlib.sha256("\0".join(ids).encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"semantic-scholar-{digest}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    headers = {"x-api-key": api_key} if api_key else {}
    for attempt in range(6):
        response = session.session.post(
            S2_BATCH_URL,
            params={"fields": "title,abstract,url,externalIds"},
            json={"ids": ids}, headers=headers, timeout=120,
        )
        if response.status_code == 429 or response.status_code >= 500:
            wait = int(response.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(max(wait, 1), 60))
            continue
        response.raise_for_status()
        cache_path.write_text(response.text, encoding="utf-8")
        time.sleep(max(session.delay, 0.5))
        return response.json()
    raise RuntimeError(f"Semantic Scholar 批量接口多次失败，首个 ID: {ids[0]}")


def enrich_semantic_scholar(
    catalogs: dict[Path, list[dict[str, str]]], session: CachedSession,
    fetched_at: str, api_key: str,
) -> int:
    rows_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for _, row in iter_rows(catalogs):
        if row["abstract"]:
            continue
        identifier = semantic_scholar_id(row)
        if identifier:
            rows_by_id[identifier].append(row)

    updated = 0
    identifiers = sorted(rows_by_id)
    for batch_index, ids in enumerate(batched(identifiers, 500), 1):
        results = s2_batch(session, ids, api_key, session.cache_dir)
        if len(results) != len(ids):
            raise RuntimeError("Semantic Scholar 返回数量与请求数量不一致")
        for identifier, result in zip(ids, results):
            if not result or not result.get("abstract"):
                continue
            for row in rows_by_id[identifier]:
                if normalize_title(result.get("title")) != normalize_title(row["title"]):
                    continue
                if apply_abstract(
                    row, abstract=result["abstract"],
                    source_name="Semantic Scholar Academic Graph",
                    source_url=clean(result.get("url")) or "https://www.semanticscholar.org/",
                    source_tier="third_party_crosschecked", fetched_at=fetched_at,
                ):
                    updated += 1
        print(f"Semantic Scholar: {min(batch_index * 500, len(identifiers))}/{len(identifiers)} IDs", flush=True)
    return updated


def validate(catalogs: dict[Path, list[dict[str, str]]]) -> None:
    for path, row in iter_rows(catalogs):
        metadata = [row[field] for field in ABSTRACT_FIELDS[1:]]
        if row["abstract"] and not all(metadata):
            raise ValueError(f"摘要来源不完整: {path.relative_to(ROOT)} / {row['paper_id']}")
        if not row["abstract"] and any(metadata):
            raise ValueError(f"存在孤立摘要来源: {path.relative_to(ROOT)} / {row['paper_id']}")


def build_report(
    catalogs: dict[Path, list[dict[str, str]]], fetched_at: str,
    source_updates: dict[str, int], parquet_path: Path,
) -> dict[str, object]:
    rows = [row for _, row in iter_rows(catalogs)]
    total = len(rows)
    present = sum(bool(row["abstract"]) for row in rows)
    missing_rows = [row for row in rows if not row["abstract"]]
    tiers = Counter(row["abstract_source_tier"] or "missing" for row in rows)
    source_names = Counter(row["abstract_source_name"] or "missing" for row in rows)
    venues: dict[str, dict[str, object]] = {}
    for venue in sorted({row["venue"] for row in rows}):
        subset = [row for row in rows if row["venue"] == venue]
        count = sum(bool(row["abstract"]) for row in subset)
        venues[venue] = {
            "records": len(subset), "abstracts": count,
            "coverage_rate": round(100 * count / len(subset), 1),
        }
    dimensions: dict[str, dict[str, dict[str, object]]] = {}
    for dimension in ("year", "list_status"):
        groups: dict[str, dict[str, object]] = {}
        for value in sorted({row[dimension] for row in rows}):
            subset = [row for row in rows if row[dimension] == value]
            count = sum(bool(row["abstract"]) for row in subset)
            groups[str(value)] = {
                "records": len(subset), "abstracts": count,
                "missing": len(subset) - count,
                "coverage_rate": round(100 * count / len(subset), 1),
            }
        dimensions[dimension] = groups
    return {
        "generated_at": fetched_at,
        "hf_parquet": str(parquet_path),
        "hf_dataset_url": HF_DATASET_URL,
        "reviewarena_dataset_url": REVIEWARENA_URL,
        "records": total,
        "abstracts": present,
        "missing": total - present,
        "missing_with_semantic_scholar_id": sum(bool(semantic_scholar_id(row)) for row in missing_rows),
        "missing_without_supported_id": sum(not semantic_scholar_id(row) for row in missing_rows),
        "coverage_rate": round(100 * present / total, 1) if total else 0,
        "updates_by_pass": source_updates,
        "records_by_abstract_tier": dict(sorted(tiers.items())),
        "records_by_abstract_source": dict(source_names.most_common()),
        "venues": venues,
        "dimensions": dimensions,
    }


def write_catalogs(catalogs: dict[Path, list[dict[str, str]]]) -> None:
    staged: list[tuple[Path, Path]] = []
    for path, rows in catalogs.items():
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        staged.append((temporary, path))
    for temporary, path in staged:
        os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="为 AIPaperbase Agent 年度清单补全可追溯摘要")
    parser.add_argument("--hf-parquet", type=Path, required=True)
    parser.add_argument("--reviewarena-tmlr", type=Path, help="可选的 ReviewArena TMLR parquet 快照")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/apexpaperrag-abstract-cache"))
    parser.add_argument("--skip-acl", action="store_true", help="跳过 ACL Anthology 官方 XML")
    parser.add_argument("--skip-crossref", action="store_true", help="跳过 Crossref 期刊元数据")
    parser.add_argument("--skip-openalex", action="store_true", help="跳过 OpenAlex DOI 摘要补充")
    parser.add_argument("--official-pages", action="store_true", help="并发读取仍缺摘要的官方论文页")
    parser.add_argument("--official-page-workers", type=int, default=4)
    parser.add_argument("--semantic-scholar", action="store_true", help="对仍缺摘要的 DOI/arXiv/ACL 记录启用 S2 批量回填")
    parser.add_argument("--dry-run", action="store_true", help="只计算，不改写 CSV 和报告")
    args = parser.parse_args()

    parquet_path = args.hf_parquet.resolve()
    if not parquet_path.is_file():
        raise FileNotFoundError(parquet_path)
    files = discover_csv_files()
    catalogs = read_catalogs(files)
    fetched_at = utc_now()
    session = CachedSession(args.cache.resolve())
    updates: dict[str, int] = {}

    if not args.skip_acl:
        updates["acl_official"] = enrich_acl_official(catalogs, session, fetched_at)
    if not args.skip_crossref:
        updates["crossref"] = enrich_crossref(catalogs, session, fetched_at)
    updates["huggingface"] = enrich_huggingface(catalogs, parquet_path, fetched_at)
    if args.reviewarena_tmlr:
        reviewarena_path = args.reviewarena_tmlr.resolve()
        if not reviewarena_path.is_file():
            raise FileNotFoundError(reviewarena_path)
        updates["reviewarena_tmlr"] = enrich_reviewarena_tmlr(
            catalogs, reviewarena_path, fetched_at,
        )
    if not args.skip_openalex:
        updates["openalex"] = enrich_openalex(catalogs, session, fetched_at)
        updates["openalex_sources"] = enrich_openalex_sources(catalogs, session, fetched_at)
    if args.official_pages:
        updates["official_pages"] = enrich_official_pages(
            catalogs, session, fetched_at, args.official_page_workers,
        )
    if args.semantic_scholar:
        updates["semantic_scholar"] = enrich_semantic_scholar(
            catalogs, session, fetched_at, os.environ.get("S2_API_KEY", ""),
        )

    validate(catalogs)
    report = build_report(catalogs, fetched_at, updates, parquet_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.dry_run:
        write_catalogs(catalogs)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
