from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.catalog.database import ROOT


DEFAULT_SAMPLE = ROOT / "data" / "analyses" / "topic_evaluation_topic-tree-v2.csv"
DEFAULT_REVIEWS = ROOT / "config" / "topic_evaluation_reviews_v2.json"
VALID_BANDS = {"high", "middle", "boundary"}
VALID_VERDICTS = {"pending", "relevant", "mention_only", "wrong_signal", "uncertain"}

EVALUATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_evaluation_sets (
    evaluation_version TEXT PRIMARY KEY,
    classifier_version TEXT NOT NULL,
    sampled_catalog_release_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    reviewed_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_evaluation_samples (
    sample_id TEXT PRIMARY KEY,
    evaluation_version TEXT NOT NULL REFERENCES topic_evaluation_sets(evaluation_version),
    topic_id TEXT NOT NULL REFERENCES topic_definitions(topic_id),
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    score REAL NOT NULL,
    score_band TEXT NOT NULL CHECK (score_band IN ('high', 'middle', 'boundary')),
    title_snapshot TEXT NOT NULL,
    authors_snapshot TEXT NOT NULL,
    abstract_snapshot TEXT NOT NULL,
    appearances_snapshot TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pending', 'relevant', 'mention_only', 'wrong_signal', 'uncertain')),
    error_type TEXT NOT NULL,
    rationale TEXT NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    sampled_catalog_release_id TEXT NOT NULL,
    UNIQUE (evaluation_version, topic_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_eval_samples_topic
    ON topic_evaluation_samples(evaluation_version, topic_id, score_band);
CREATE INDEX IF NOT EXISTS idx_topic_eval_samples_verdict
    ON topic_evaluation_samples(evaluation_version, verdict, error_type);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_evaluation_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(EVALUATION_SCHEMA)


def _read_inputs(sample_path: Path, reviews_path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with sample_path.open("r", encoding="utf-8-sig", newline="") as handle:
        samples = list(csv.DictReader(handle))
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    if not samples:
        raise ValueError("主题评估样本为空")
    versions = {row["evaluation_version"] for row in samples}
    releases = {row["sampled_catalog_release_id"] for row in samples}
    sample_ids = [row["sample_id"] for row in samples]
    if len(versions) != 1 or len(releases) != 1:
        raise ValueError("评估样本必须只对应一个评估版本和一个目录版本")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("评估样本 ID 重复")
    if reviews.get("evaluation_version") not in versions:
        raise ValueError("审阅文件与样本评估版本不一致")
    unknown_reviews = set(reviews.get("reviews", {})) - set(sample_ids)
    if unknown_reviews:
        raise ValueError(f"审阅文件包含未知样本: {sorted(unknown_reviews)[:3]}")
    for row in samples:
        if row["score_band"] not in VALID_BANDS:
            raise ValueError(f"未知分数层: {row['score_band']}")
        json.loads(row["evidence_json"])
    for sample_id, review in reviews.get("reviews", {}).items():
        if review.get("verdict") not in VALID_VERDICTS - {"pending"}:
            raise ValueError(f"{sample_id} 的 verdict 非法")
        if not review.get("rationale", "").strip():
            raise ValueError(f"{sample_id} 缺少审阅理由")
    return samples, reviews


def load_topic_evaluation(
    connection: sqlite3.Connection,
    sample_path: Path = DEFAULT_SAMPLE,
    reviews_path: Path = DEFAULT_REVIEWS,
) -> dict[str, Any]:
    samples, review_bundle = _read_inputs(sample_path, reviews_path)
    ensure_evaluation_schema(connection)
    evaluation_version = samples[0]["evaluation_version"]
    sampled_release = samples[0]["sampled_catalog_release_id"]
    topic_ids = {row["topic_id"] for row in samples}
    entity_ids = {row["entity_id"] for row in samples}

    placeholders = ",".join("?" for _ in topic_ids)
    known_topics = {
        row[0] for row in connection.execute(
            f"SELECT topic_id FROM topic_definitions WHERE topic_id IN ({placeholders})", tuple(topic_ids)
        )
    }
    if known_topics != topic_ids:
        raise ValueError(f"样本包含未知主题: {sorted(topic_ids - known_topics)}")
    placeholders = ",".join("?" for _ in entity_ids)
    known_entities = {
        row[0] for row in connection.execute(
            f"SELECT entity_id FROM paper_entities WHERE entity_id IN ({placeholders})", tuple(entity_ids)
        )
    }
    if known_entities != entity_ids:
        raise ValueError(f"当前目录缺少 {len(entity_ids - known_entities)} 个样本实体")

    classifier = connection.execute(
        """SELECT config_version FROM analysis_runs
           WHERE analysis_type = 'topic_classification' AND status = 'ready'
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not classifier:
        raise ValueError("当前数据库没有可评估的主题分类运行")

    reviews = review_bundle.get("reviews", {})
    reviewer_type = str(review_bundle.get("reviewer_type", ""))
    reviewer_name = str(review_bundle.get("reviewer_name", ""))
    evidence_level = str(review_bundle.get("evidence_level", "title_abstract"))
    reviewed_at = str(review_bundle.get("reviewed_at", ""))
    connection.execute(
        """INSERT INTO topic_evaluation_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(evaluation_version) DO UPDATE SET
             classifier_version=excluded.classifier_version,
             sampled_catalog_release_id=excluded.sampled_catalog_release_id,
             sample_count=excluded.sample_count,
             reviewer_type=excluded.reviewer_type,
             reviewer_name=excluded.reviewer_name,
             evidence_level=excluded.evidence_level,
             reviewed_count=excluded.reviewed_count""",
        (
            evaluation_version, classifier[0], sampled_release, len(samples), reviewer_type,
            reviewer_name, evidence_level, len(reviews), utc_now(),
        ),
    )
    values = []
    for row in samples:
        review = reviews.get(row["sample_id"], {})
        verdict = review.get("verdict", "pending")
        values.append((
            row["sample_id"], evaluation_version, row["topic_id"], row["entity_id"],
            float(row["score"]), row["score_band"], row["title"], row["authors"],
            row["abstract"], row["appearances"], row["evidence_json"], verdict,
            review.get("error_type", ""), review.get("rationale", ""),
            reviewer_type if verdict != "pending" else "",
            reviewer_name if verdict != "pending" else "",
            evidence_level, reviewed_at if verdict != "pending" else "", sampled_release,
        ))
    connection.executemany(
        """INSERT INTO topic_evaluation_samples VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(sample_id) DO UPDATE SET
             verdict=excluded.verdict, error_type=excluded.error_type,
             rationale=excluded.rationale, reviewer_type=excluded.reviewer_type,
             reviewer_name=excluded.reviewer_name, evidence_level=excluded.evidence_level,
             reviewed_at=excluded.reviewed_at""",
        values,
    )
    return {
        "evaluation_version": evaluation_version,
        "sample_count": len(samples),
        "reviewed_count": len(reviews),
        "reviewer_type": reviewer_type,
        "classifier_version": classifier[0],
    }
