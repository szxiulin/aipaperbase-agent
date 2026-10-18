"""Import the LLM area/direction labels into the catalog build (v0.2 data layer).

The labels are data, not configuration: areas-v1.5.csv, directions-v1.1.csv, the non-paper list and
the gold set are versioned in data/labels/, and config/areas.json + config/directions.json describe
the taxonomy. This module validates the pair before writing anything, then inserts definitions,
assignments and evaluation samples in one transaction.

Invariants:
  - every entity has exactly one area; exactly the non-paper entities have no direction;
  - every direction belongs to the area stored on the paper;
  - a mismatch raises before any row is written.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from backend.catalog.database import ROOT

DEFAULT_AREAS_LABELS = ROOT / "data/labels/areas-v1.5.csv"
DEFAULT_DIRECTIONS_LABELS = ROOT / "data/labels/directions-v1.1.csv"
DEFAULT_NON_PAPER = ROOT / "data/labels/non_paper_records.csv"
DEFAULT_GOLDEN = ROOT / "data/labels/golden_v1.csv"
DEFAULT_METRICS = ROOT / "data/labels/golden_v1.metrics.json"
DEFAULT_AREAS_CONFIG = ROOT / "config/areas.json"
DEFAULT_DIRECTIONS_CONFIG = ROOT / "config/directions.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def import_labels(connection: sqlite3.Connection, catalog_release_id: str, created_at: str, *,
                  areas_labels: Path = DEFAULT_AREAS_LABELS,
                  directions_labels: Path = DEFAULT_DIRECTIONS_LABELS,
                  non_paper_labels: Path = DEFAULT_NON_PAPER,
                  golden: Path = DEFAULT_GOLDEN,
                  metrics: Path = DEFAULT_METRICS,
                  areas_config: Path = DEFAULT_AREAS_CONFIG,
                  directions_config: Path = DEFAULT_DIRECTIONS_CONFIG) -> dict:
    cfg_areas = json.loads(areas_config.read_text(encoding="utf-8"))
    cfg_directions = json.loads(directions_config.read_text(encoding="utf-8"))
    area_ids = {a["id"]: a for a in cfg_areas["areas"]}
    direction_area = {d["direction_id"]: d["area"] for d in cfg_directions["directions"]}
    if len(direction_area) != len(cfg_directions["directions"]):
        raise ValueError("duplicate direction_id in config/directions.json")

    area_rows = read_rows(areas_labels)
    direction_rows = read_rows(directions_labels)
    non_paper_rows = read_rows(non_paper_labels)
    bad_status = [r["entity_id"] for r in area_rows if r.get("status") != "ok"]
    if bad_status:
        raise ValueError(f"{len(bad_status)} area label rows are not ok (e.g. {bad_status[:3]})")

    entity_ids = {r[0] for r in connection.execute("SELECT entity_id FROM paper_entities")}
    area_by_entity = {r["entity_id"]: r["area"] for r in area_rows}
    if len(area_by_entity) != len(area_rows):
        raise ValueError("duplicate entity_id in area labels")
    unknown = set(area_by_entity) - entity_ids
    if unknown:
        raise ValueError(f"{len(unknown)} area rows are not catalog entities (e.g. {sorted(unknown)[:3]})")
    missing = entity_ids - set(area_by_entity)
    if missing:
        raise ValueError(f"{len(missing)} catalog entities have no area (e.g. {sorted(missing)[:3]})")
    invalid = {a for a in area_by_entity.values() if a not in area_ids}
    if invalid:
        raise ValueError(f"unknown areas in labels: {sorted(invalid)[:5]}")

    non_paper = {r["entity_id"]: r["record_kind"] for r in non_paper_rows}
    missing_kind = [e for e, k in non_paper.items() if not k]
    if missing_kind:
        raise ValueError(f"{len(missing_kind)} non-paper rows have no record_kind")
    direction_by_entity = {r["entity_id"]: r["direction_id"] for r in direction_rows}
    if len(direction_by_entity) != len(direction_rows):
        raise ValueError("duplicate entity_id in direction labels")
    expected_non_paper = set(area_by_entity) - set(direction_by_entity)
    if expected_non_paper != set(non_paper):
        raise ValueError(f"non-paper mismatch: labels say {len(expected_non_paper)}, file has {len(non_paper)}")
    direction_unknown = {d for d in direction_by_entity.values() if d not in direction_area}
    if direction_unknown:
        raise ValueError(f"unknown directions: {sorted(direction_unknown)[:5]}")
    wrong_csv_area = [r["entity_id"] for r in direction_rows if r["area"] != area_by_entity[r["entity_id"]]]
    if wrong_csv_area:
        raise ValueError(f"{len(wrong_csv_area)} direction rows disagree with the area file (e.g. {wrong_csv_area[:3]})")
    wrong_area = [e for e, d in direction_by_entity.items() if direction_area[d] != area_by_entity[e]]
    if wrong_area:
        raise ValueError(f"{len(wrong_area)} papers have a direction outside their area (e.g. {wrong_area[:3]})")

    release_id = f"labels-{created_at[:10].replace('-', '')}-{cfg_areas['version']}-{cfg_directions['version']}"
    metrics_payload = json.loads(metrics.read_text(encoding="utf-8")) if metrics.exists() else {}
    golden_rows = read_rows(golden) if golden.exists() else []
    for r in golden_rows:
        if direction_area.get(r["gold_direction"]) != r["gold_area"]:
            raise ValueError(f"gold row {r['entity_id']} has an invalid area/direction pair")

    with connection:
        connection.execute(
            """INSERT INTO label_releases (label_release_id, catalog_release_id, areas_version,
                   directions_version, areas_sha256, directions_sha256, golden_sha256, entity_count,
                   non_paper_count, metrics_json, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'building')""",
            (release_id, catalog_release_id, cfg_areas["version"], cfg_directions["version"],
             sha256(areas_labels), sha256(directions_labels), sha256(golden) if golden.exists() else "",
             len(area_rows), len(non_paper), json.dumps(metrics_payload, ensure_ascii=False), created_at))

        connection.executemany(
            "INSERT INTO area_definitions VALUES (?, ?, ?, ?, ?, ?)",
            [(a["id"], a["zh"], a["en"], a["scope"], i, cfg_areas["version"])
             for i, a in enumerate(cfg_areas["areas"])])
        connection.executemany(
            "INSERT INTO direction_definitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(d["direction_id"], d["area"], d["parent_direction_id"], d["name_zh"], d["name_en"],
              d["definition"], d["status"], i, cfg_directions["version"])
             for i, d in enumerate(cfg_directions["directions"])])
        connection.executemany(
            "INSERT INTO direction_aliases VALUES (?, ?)",
            [(d["direction_id"], alias) for d in cfg_directions["directions"] for alias in d["aliases"]])

        connection.executemany(
            "INSERT INTO entity_area_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r["entity_id"], r["area"], r.get("area_secondary", ""), r.get("area_confidence", ""),
              r.get("model", ""), r.get("prompt_version", ""), cfg_areas["version"],
              1 if r["entity_id"] in non_paper else 0, non_paper.get(r["entity_id"], ""))
             for r in area_rows])
        connection.executemany(
            "INSERT INTO entity_direction_assignments VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["entity_id"], r["direction_id"], r.get("assigned_by", ""),
              float(r["cosine"]) if r.get("cosine") else None,
              float(r["margin"]) if r.get("margin") else None,
              int(r.get("needs_review") or 0), cfg_directions["version"])
             for r in direction_rows])
        if golden_rows:
            connection.executemany(
                "INSERT INTO label_evaluation_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(release_id, r["entity_id"], int(r["sample_order"]), r["system_area"], r["system_direction"],
                  r["pred_area"], r["pred_direction"], r["gold_area"], r["gold_direction"], r["verdict"], r["notes"])
                 for r in golden_rows])
        connection.execute("UPDATE label_releases SET status = 'ready' WHERE label_release_id = ?", (release_id,))

    return {"label_release_id": release_id, "areas_version": cfg_areas["version"],
            "directions_version": cfg_directions["version"], "area_assignments": len(area_rows),
            "direction_assignments": len(direction_rows), "non_paper": len(non_paper),
            "directions": len(cfg_directions["directions"]), "golden_samples": len(golden_rows),
            "metrics_loaded": bool(metrics_payload)}
