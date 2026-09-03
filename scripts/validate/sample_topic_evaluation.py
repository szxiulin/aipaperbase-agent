from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.catalog.database import DEFAULT_DATABASE, connect  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data" / "analyses" / "topic_evaluation_topic-tree-v2.csv"
# Main evaluation arenas: family 2 (LLM ontology) / family 3 (Agent ontology) / family 5 (low-level vision) — covering the two acceptance points: Agent score inflation drop-off and super-resolution/restoration
ROOT_TOPICS = ("f2_llm_models", "f3_agent_systems", "f5_lowlevel_vision")
QUOTAS = {"high": 30, "middle": 30, "boundary": 40}
FIELDS = (
    "sample_id", "evaluation_version", "sampled_catalog_release_id", "topic_id",
    "entity_id", "score", "score_band", "title", "authors", "abstract",
    "appearances", "evidence_json", "verdict", "error_type", "rationale",
    "reviewer_type", "reviewer_name", "evidence_level", "reviewed_at",
)


def score_band(score: float) -> str:
    if score >= 6:
        return "high"
    if score > 3:
        return "middle"
    return "boundary"


def stable_key(version: str, topic_id: str, entity_id: str) -> str:
    return hashlib.sha256(f"{version}\0{topic_id}\0{entity_id}".encode("utf-8")).hexdigest()


def choose_stratified(rows: list[dict[str, object]], quota: int, version: str, topic_id: str) -> list[dict[str, object]]:
    by_year: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["last_year"])].append(row)
    for items in by_year.values():
        items.sort(key=lambda item: stable_key(version, topic_id, str(item["entity_id"])))
    selected = []
    years = sorted(by_year)
    while len(selected) < quota and any(by_year.values()):
        for year in years:
            if by_year[year] and len(selected) < quota:
                selected.append(by_year[year].pop())
    return selected


def build_sample(database: Path, output: Path, evaluation_version: str) -> dict[str, object]:
    """Stratified sampling of role='primary' entities within each family (via topic_ancestors).

    A primary leaf belongs to exactly one family ⇒ entities are mutually exclusive across the main-arena families, so no dedup is needed.
    """
    with connect(database, read_only=True) as connection:
        release = connection.execute(
            "SELECT release_id FROM data_releases WHERE status = 'ready' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        placeholders = ",".join("?" for _ in ROOT_TOPICS)
        rows = connection.execute(
            f"""SELECT x.ancestor_topic_id AS topic_id, a.entity_id, a.score, a.evidence_json,
                      e.canonical_title AS title, e.last_year,
                      p.authors, p.abstract
               FROM entity_topic_assignments a
               JOIN topic_ancestors x ON x.topic_id = a.topic_id
               JOIN paper_entities e ON e.entity_id = a.entity_id
               JOIN paper_records p ON p.record_id = e.canonical_record_id
               WHERE a.role = 'primary' AND x.ancestor_topic_id IN ({placeholders})""",
            ROOT_TOPICS,
        ).fetchall()
        appearances: dict[str, list[str]] = defaultdict(list)
        entity_ids = {row["entity_id"] for row in rows}
        for row in connection.execute(
            """SELECT m.entity_id, p.venue, p.year
               FROM entity_memberships m JOIN paper_records p ON p.record_id = m.record_id
               ORDER BY m.entity_id, p.year, p.venue"""
        ):
            if row["entity_id"] in entity_ids:
                appearances[row["entity_id"]].append(f"{row['venue']}:{row['year']}")

    candidates: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for raw in rows:
        item = dict(raw)
        candidates[(item["topic_id"], score_band(float(item["score"])))].append(item)

    selected = []
    for topic_id in ROOT_TOPICS:
        for band, quota in QUOTAS.items():
            chosen = choose_stratified(candidates[(topic_id, band)], quota, evaluation_version, topic_id)
            if len(chosen) != quota:
                raise RuntimeError(f"{topic_id}/{band} 候选不足: {len(chosen)} < {quota}")
            for item in chosen:
                entity_id = str(item["entity_id"])
                selected.append({
                    "sample_id": "sample_" + stable_key(evaluation_version, topic_id, entity_id)[:24],
                    "evaluation_version": evaluation_version,
                    "sampled_catalog_release_id": release,
                    "topic_id": topic_id,
                    "entity_id": entity_id,
                    "score": item["score"],
                    "score_band": band,
                    "title": item["title"],
                    "authors": item["authors"],
                    "abstract": item["abstract"],
                    "appearances": ";".join(appearances[entity_id]),
                    "evidence_json": item["evidence_json"],
                    "verdict": "pending",
                    "error_type": "",
                    "rationale": "",
                    "reviewer_type": "",
                    "reviewer_name": "",
                    "evidence_level": "title_abstract",
                    "reviewed_at": "",
                })
    selected.sort(key=lambda item: (ROOT_TOPICS.index(str(item["topic_id"])), ("high", "middle", "boundary").index(str(item["score_band"])), str(item["sample_id"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(selected)
    return {
        "output": str(output),
        "evaluation_version": evaluation_version,
        "catalog_release_id": release,
        "sample_count": len(selected),
        "topic_counts": {topic_id: sum(item["topic_id"] == topic_id for item in selected) for topic_id in ROOT_TOPICS},
        "band_counts": {band: sum(item["score_band"] == band for item in selected) for band in QUOTAS},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="为主题树 v2 生成可复现的分层评估样本（primary 口径）")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evaluation-version", default="topic-audit-v2")
    args = parser.parse_args()
    print(json.dumps(build_sample(args.database, args.output, args.evaluation_version), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
