from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .database import DEFAULT_DATABASE, ROOT, connect, initialize
from .entities import build_entities
from backend.analytics.topics import build_topic_analysis
from backend.analytics.evaluation import DEFAULT_REVIEWS, DEFAULT_SAMPLE, load_topic_evaluation
from backend.catalog import search as search_mod


CATALOG_ROOT = ROOT / "data" / "catalog"
CSV_ROOTS = (CATALOG_ROOT / "conferences", CATALOG_ROOT / "journals")
EXPECTED_FIELDS = (
    "paper_id", "venue", "venue_type", "year", "track", "title", "authors",
    "abstract", "abstract_source_name", "abstract_source_url",
    "abstract_source_tier", "abstract_fetched_at",
    "doi", "arxiv_id", "paper_url", "pdf_url", "source_name", "source_url",
    "source_tier", "verification_status", "list_status", "fetched_at",
)
REQUIRED_FIELDS = (
    "paper_id", "venue", "venue_type", "year", "title", "source_name",
    "source_url", "source_tier", "verification_status", "list_status",
)
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
ARXIV_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?$", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", value)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_csv_files() -> list[Path]:
    return sorted(path for root in CSV_ROOTS for path in root.rglob("*.csv"))


def source_digest(files: list[Path]) -> tuple[str, dict[Path, str]]:
    hashes = {path: file_sha256(path) for path in files}
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(hashes[path].encode("ascii"))
    return digest.hexdigest(), hashes


def valid_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def record_id(source_file: str, paper_id: str) -> str:
    return hashlib.sha256(f"{source_file}\0{paper_id}".encode("utf-8")).hexdigest()


def add_issue(
    connection: sqlite3.Connection,
    release_id: str,
    severity: str,
    issue_type: str,
    source_file: str,
    record: str,
    field: str,
    message: str,
) -> None:
    connection.execute(
        """INSERT INTO quality_issues
           (release_id, severity, issue_type, source_file, record_id, field_name, message)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (release_id, severity, issue_type, source_file, record, field, message),
    )


def validate_row(
    connection: sqlite3.Connection,
    release_id: str,
    source_file: str,
    rid: str,
    row: dict[str, str],
) -> None:
    for field in REQUIRED_FIELDS:
        if not row.get(field, "").strip():
            add_issue(connection, release_id, "error", "missing_required", source_file, rid, field, "必填字段为空")
    if row.get("venue_type") not in {"conference", "journal"}:
        add_issue(connection, release_id, "error", "invalid_value", source_file, rid, "venue_type", "venue_type 必须为 conference 或 journal")
    if row.get("list_status") not in {"final", "rolling"}:
        add_issue(connection, release_id, "error", "invalid_value", source_file, rid, "list_status", "list_status 必须为 final 或 rolling")
    try:
        year = int(row.get("year", ""))
        if year < 2023 or year > datetime.now().year + 1:
            raise ValueError
    except ValueError:
        add_issue(connection, release_id, "error", "invalid_year", source_file, rid, "year", "年份不在允许范围内")
    doi = row.get("doi", "").strip()
    if doi and not DOI_PATTERN.match(doi):
        add_issue(connection, release_id, "warning", "invalid_format", source_file, rid, "doi", "DOI 格式异常")
    arxiv_id = row.get("arxiv_id", "").strip()
    if arxiv_id and not ARXIV_PATTERN.match(arxiv_id):
        add_issue(connection, release_id, "warning", "invalid_format", source_file, rid, "arxiv_id", "arXiv ID 格式异常")
    for field in ("paper_url", "pdf_url", "source_url"):
        if not valid_url(row.get(field, "").strip()):
            add_issue(connection, release_id, "warning", "invalid_url", source_file, rid, field, "URL 格式异常")
    abstract = row.get("abstract", "").strip()
    abstract_metadata = (
        row.get("abstract_source_name", "").strip(),
        row.get("abstract_source_url", "").strip(),
        row.get("abstract_source_tier", "").strip(),
        row.get("abstract_fetched_at", "").strip(),
    )
    if abstract and not all(abstract_metadata):
        add_issue(connection, release_id, "error", "incomplete_abstract_provenance", source_file, rid, "abstract", "有摘要时必须同时保存完整来源信息")
    if not abstract and any(abstract_metadata):
        add_issue(connection, release_id, "error", "orphan_abstract_provenance", source_file, rid, "abstract", "无摘要时不应保存摘要来源信息")
    if row.get("abstract_source_url") and not valid_url(row["abstract_source_url"]):
        add_issue(connection, release_id, "warning", "invalid_url", source_file, rid, "abstract_source_url", "摘要来源 URL 格式异常")


def import_file(
    connection: sqlite3.Connection,
    path: Path,
    release_id: str,
    imported_at: str,
    sha256: str,
) -> int:
    relative = path.relative_to(ROOT).as_posix()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EXPECTED_FIELDS:
            raise ValueError(f"CSV 字段不一致: {relative}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV 不应为空: {relative}")

    first = rows[0]
    venue = first["venue"].strip()
    venue_type = first["venue_type"].strip()
    year = int(first["year"])
    status = first["list_status"].strip()
    connection.execute(
        """INSERT INTO source_files
           (source_file, release_id, sha256, row_count, venue, venue_type, year, list_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (relative, release_id, sha256, len(rows), venue, venue_type, year, status),
    )

    values = []
    for raw in rows:
        row = {field: (raw.get(field) or "").strip() for field in EXPECTED_FIELDS}
        rid = record_id(relative, row["paper_id"])
        validate_row(connection, release_id, relative, rid, row)
        if row["venue"] != venue or row["venue_type"] != venue_type or int(row["year"]) != year:
            add_issue(connection, release_id, "error", "mixed_source_file", relative, rid, "venue/year", "同一 CSV 中 venue、类型或年份不一致")
        if row["list_status"] != status:
            add_issue(connection, release_id, "error", "mixed_source_file", relative, rid, "list_status", "同一 CSV 中清单状态不一致")
        values.append((
            rid, row["paper_id"], row["venue"], row["venue_type"], int(row["year"]),
            row["track"], row["title"], normalize_title(row["title"]), row["authors"],
            row["abstract"], row["abstract_source_name"], row["abstract_source_url"],
            row["abstract_source_tier"], row["abstract_fetched_at"],
            row["doi"].lower(), row["arxiv_id"], row["paper_url"], row["pdf_url"],
            row["source_name"], row["source_url"], row["source_tier"],
            row["verification_status"], row["list_status"], row["fetched_at"],
            relative, imported_at, release_id,
        ))
    connection.executemany(
        """INSERT INTO paper_records VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )
    return len(rows)


def add_set_level_issues(connection: sqlite3.Connection, release_id: str) -> None:
    duplicates = connection.execute(
        """SELECT venue, year, paper_id, COUNT(*) AS n
           FROM paper_records GROUP BY venue, year, paper_id HAVING n > 1"""
    ).fetchall()
    for item in duplicates:
        add_issue(connection, release_id, "error", "duplicate_venue_paper", "", "", "paper_id", f"{item['venue']} {item['year']} 的 {item['paper_id']} 重复 {item['n']} 次")

    cross_venue = connection.execute(
        """SELECT doi, COUNT(DISTINCT venue) AS venue_count, COUNT(*) AS record_count
           FROM paper_records WHERE doi <> '' GROUP BY doi HAVING venue_count > 1"""
    ).fetchall()
    for item in cross_venue:
        add_issue(connection, release_id, "info", "cross_venue_doi", "", "", "doi", f"{item['doi']} 出现在 {item['venue_count']} 个 venue、{item['record_count']} 条记录中")


def build_database(target: Path = DEFAULT_DATABASE) -> dict[str, object]:
    files = discover_csv_files()
    if not files:
        raise RuntimeError("没有发现年度 CSV")
    digest, hashes = source_digest(files)
    release_id = f"catalog-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}"
    imported_at = utc_now()
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary_dir = Path(tempfile.mkdtemp(prefix="apexpaper-catalog-", dir=target.parent))
    temporary_database = temporary_dir / "catalog.sqlite"
    try:
        connection = connect(temporary_database)
        initialize(connection)
        connection.execute(
            "INSERT INTO data_releases VALUES (?, ?, ?, ?, ?, 'building')",
            (release_id, imported_at, digest, len(files), 0),
        )
        total = 0
        with connection:
            for path in files:
                total += import_file(connection, path, release_id, imported_at, hashes[path])
            add_set_level_issues(connection, release_id)
            entity_stats = build_entities(
                connection,
                release_id,
                imported_at,
                previous_database=target if target.exists() else None,
            )
            topic_stats = build_topic_analysis(connection, release_id, imported_at)
            evaluation_stats = {}
            if DEFAULT_SAMPLE.exists() and DEFAULT_REVIEWS.exists():
                # The sample/review files pin entity & topic ids from an earlier release; after a data
                # change those ids may no longer resolve. Degrading here keeps a rebuild from failing
                # wholesale on a stale evaluation artifact (the audit set can be re-sampled later).
                try:
                    evaluation_stats = load_topic_evaluation(connection)
                except Exception as exc:  # noqa: BLE001
                    print(f"[import] 跳过主题评估导入（样本引用了旧实体/旧主题）: {exc}")
            connection.execute(
                "UPDATE data_releases SET record_count = ?, status = 'ready' WHERE release_id = ?",
                (total, release_id),
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        errors = connection.execute("SELECT COUNT(*) FROM quality_issues WHERE severity = 'error'").fetchone()[0]
        issues = connection.execute("SELECT COUNT(*) FROM quality_issues").fetchone()[0]
        connection.close()
        if integrity != "ok" or errors:
            raise RuntimeError(f"构建未通过: integrity={integrity}, errors={errors}")

        # Build the FTS5 full-text index (built on the temporary database before the swap, so it stays atomic with the library)
        fts_connection = sqlite3.connect(temporary_database)
        try:
            fts_count = search_mod.rebuild_index(fts_connection)
        finally:
            fts_connection.close()

        if target.exists():
            backup = target.with_name(f"catalog.{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite")
            shutil.move(target, backup)
        os.replace(temporary_database, target)
        shutil.rmtree(temporary_dir, ignore_errors=True)  # success: drop this build's scratch dir
        return {
            "database": str(target),
            "release_id": release_id,
            "source_files": len(files),
            "records": total,
            "quality_issues": issues,
            "blocking_errors": errors,
            "integrity": integrity,
            **entity_stats,
            **topic_stats,
            **{f"evaluation_{key}": value for key, value in evaluation_stats.items()},
        }
    finally:
        # Keep the build directory for auditing on failure; on success the database is atomically moved out and the directory is empty.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="从年度 CSV 构建 AIPaperbase Agent 本地元数据库")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    result = build_database(args.database)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
