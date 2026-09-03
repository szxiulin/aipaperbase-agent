from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Record:
    record_id: str
    title: str
    normalized_title: str
    authors: str
    abstract: str
    doi: str
    arxiv_id: str
    paper_url: str
    pdf_url: str
    source_tier: str
    list_status: str
    venue: str
    year: int


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            value, self.parent[value] = self.parent[value], root
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _plain_title(value: str) -> str:
    value = HTML_TAG.sub(" ", html.unescape(value))
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", value)


def _authors_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", value)


def _arxiv_key(value: str) -> str:
    value = value.strip().casefold()
    value = value.removeprefix("arxiv:")
    return ARXIV_VERSION.sub("", value)


def _openreview_key(*urls: str) -> str:
    for value in urls:
        if "openreview.net" not in value:
            continue
        parsed = urlparse(value)
        identifier = parse_qs(parsed.query).get("id", [""])[0].strip()
        if identifier:
            return identifier
    return ""


def _compatible(records: list[Record]) -> bool:
    titles = {_plain_title(record.title) for record in records}
    author_sets = {_authors_key(record.authors) for record in records if record.authors.strip()}
    return len(titles) == 1 and len(author_sets) <= 1


def _entity_id(seed: str) -> str:
    return "ape_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _candidate_id(left: str, right: str, method: str) -> str:
    return "review_" + hashlib.sha256(f"{left}\0{right}\0{method}".encode("utf-8")).hexdigest()[:24]


def _canonical(records: list[Record]) -> Record:
    def score(record: Record) -> tuple[object, ...]:
        title_has_markup = int(bool(HTML_TAG.search(record.title) or "&" in record.title))
        return (
            int(record.list_status == "final"),
            int(bool(record.abstract)),
            int(bool(record.authors)),
            int(bool(record.doi)),
            int(bool(record.arxiv_id)),
            int(bool(record.paper_url)),
            int(bool(record.pdf_url)),
            int(record.source_tier == "official"),
            -title_has_markup,
            -record.year,
            record.record_id,
        )

    return max(records, key=score)


def _previous_memberships(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_memberships'"
        ).fetchone()
        if not exists:
            return {}
        return dict(connection.execute("SELECT record_id, entity_id FROM entity_memberships"))
    finally:
        connection.close()


def build_entities(
    connection: sqlite3.Connection,
    release_id: str,
    created_at: str,
    *,
    previous_database: Path | None = None,
) -> dict[str, int]:
    rows = connection.execute(
        """SELECT record_id, title, normalized_title, authors, abstract, doi, arxiv_id,
                  paper_url, pdf_url, source_tier, list_status, venue, year
           FROM paper_records ORDER BY record_id"""
    ).fetchall()
    records = [Record(**dict(row)) for row in rows]
    by_id = {record.record_id: record for record in records}
    union = UnionFind(list(by_id))
    evidence_edges: dict[tuple[str, str], tuple[str, str]] = {}
    candidates: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    def merge_group(items: list[Record], method: str, value: str) -> None:
        anchor = items[0].record_id
        for item in items[1:]:
            union.union(anchor, item.record_id)
            pair = tuple(sorted((anchor, item.record_id)))
            evidence_edges[pair] = (method, value)

    doi_groups: dict[str, list[Record]] = defaultdict(list)
    arxiv_groups: dict[str, list[Record]] = defaultdict(list)
    openreview_groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.doi:
            doi_groups[record.doi.casefold()].append(record)
        if record.arxiv_id:
            arxiv_groups[_arxiv_key(record.arxiv_id)].append(record)
        openreview = _openreview_key(record.paper_url, record.pdf_url)
        if openreview:
            openreview_groups[openreview].append(record)

    for value, items in doi_groups.items():
        if len(items) > 1:
            merge_group(items, "doi", value)

    def merge_compatible_identifier_groups(groups: dict[str, list[Record]], method: str) -> None:
        for value, items in groups.items():
            if len(items) <= 1:
                continue
            if _compatible(items):
                merge_group(items, method, value)
                continue
            for index, left in enumerate(items):
                for right in items[index + 1:]:
                    pair = tuple(sorted((left.record_id, right.record_id)))
                    candidates[(pair[0], pair[1], f"{method}_conflict")] = (
                        value,
                        "同一标识符对应的题名或作者不一致，禁止自动归并",
                        "conflict",
                    )

    merge_compatible_identifier_groups(arxiv_groups, "arxiv")
    merge_compatible_identifier_groups(openreview_groups, "openreview")

    title_author_groups: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        title_key = _plain_title(record.title)
        authors_key = _authors_key(record.authors)
        if len(title_key) >= 24 and len(authors_key) >= 6:
            title_author_groups[(title_key, authors_key)].append(record)
    for (title_key, _), items in title_author_groups.items():
        if not 1 < len(items) <= 10:
            continue
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                if union.find(left.record_id) == union.find(right.record_id):
                    continue
                pair = tuple(sorted((left.record_id, right.record_id)))
                candidates[(pair[0], pair[1], "exact_title_authors")] = (
                    title_key,
                    "题名与作者完全一致，但可能是期刊扩展版，需人工判断",
                    "high",
                )

    components: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        components[union.find(record.record_id)].append(record)

    previous = _previous_memberships(previous_database)
    claimed_previous: set[str] = set()
    entity_rows = []
    membership_rows = []
    alias_rows = []
    component_entity: dict[str, str] = {}
    ordered_components = sorted(components.values(), key=lambda items: (-len(items), items[0].record_id))
    for items in ordered_components:
        old_counts = Counter(previous[item.record_id] for item in items if item.record_id in previous)
        reusable = [item for item in old_counts.most_common() if item[0] not in claimed_previous]
        shared_dois = sorted({item.doi.casefold() for item in items if item.doi})
        seed = f"doi:{shared_dois[0]}" if shared_dois else f"record:{min(item.record_id for item in items)}"
        entity_id = reusable[0][0] if reusable else _entity_id(seed)
        if entity_id in claimed_previous:
            entity_id = _entity_id(f"{seed}:{min(item.record_id for item in items)}")
        claimed_previous.add(entity_id)
        for old_id in old_counts:
            if old_id != entity_id and old_id not in claimed_previous:
                alias_rows.append((old_id, entity_id, "incremental_component_merge", created_at, release_id))
                claimed_previous.add(old_id)

        canonical = _canonical(items)
        component_entity[union.find(items[0].record_id)] = entity_id
        entity_rows.append((
            entity_id, canonical.record_id, canonical.title,
            min(item.year for item in items), max(item.year for item in items),
            len(items), len({item.venue for item in items}),
            "merged" if len(items) > 1 else "single", created_at, release_id,
        ))
        shared_arxiv = sorted({_arxiv_key(item.arxiv_id) for item in items if item.arxiv_id})
        shared_openreview = sorted({_openreview_key(item.paper_url, item.pdf_url) for item in items if _openreview_key(item.paper_url, item.pdf_url)})
        if len(items) == 1:
            method, value, confidence = "singleton", "", "single"
        elif shared_dois:
            method, value, confidence = "doi", shared_dois[0], "exact"
        elif shared_arxiv:
            method, value, confidence = "arxiv", shared_arxiv[0], "high"
        else:
            method, value, confidence = "openreview", shared_openreview[0], "high"
        for item in items:
            membership_rows.append((
                item.record_id, entity_id, method, value, confidence,
                "auto_accepted", int(item.record_id == canonical.record_id), created_at, release_id,
            ))

    candidate_rows = []
    for (left, right, method), (value, reason, confidence) in sorted(candidates.items()):
        if component_entity[union.find(left)] == component_entity[union.find(right)]:
            continue
        candidate_rows.append((
            _candidate_id(left, right, method), left, right, method, value,
            reason, confidence, "pending", created_at, release_id,
        ))

    connection.executemany("INSERT INTO paper_entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", entity_rows)
    connection.executemany("INSERT INTO entity_memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", membership_rows)
    connection.executemany("INSERT INTO entity_review_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", candidate_rows)
    connection.executemany("INSERT INTO entity_aliases VALUES (?, ?, ?, ?, ?)", alias_rows)
    return {
        "entities": len(entity_rows),
        "merged_entities": sum(row[7] == "merged" for row in entity_rows),
        "linked_records": sum(row[5] for row in entity_rows if row[7] == "merged"),
        "review_candidates": len(candidate_rows),
        "aliases": len(alias_rows),
    }
