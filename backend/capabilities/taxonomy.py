"""Taxonomy capabilities: list_taxonomy (areas + directions + counts)."""
from __future__ import annotations

import sqlite3

from backend.analytics import areas as area_queries


def list_taxonomy(connection: sqlite3.Connection, args: dict) -> dict:
    overview = area_queries.areas_overview(connection)
    if not overview.get("label"):
        raise ValueError("catalog has no label release")
    only = str(args.get("area_id") or "").strip()
    areas = []
    for area in overview["topics"]:
        if only and area["topic_id"] != only:
            continue
        areas.append({
            "area_id": area["topic_id"], "name_zh": area["name"], "name_en": area["name_en"],
            "entity_count": area["entity_count"],
            "directions": [{"direction_id": d["topic_id"], "name_zh": d["name"],
                            "name_en": d["name_en"], "entity_count": d["entity_count"],
                            "status": d.get("status", "")}
                           for d in area["subtopics"]],
        })
    if only and not areas:
        raise ValueError("unknown area_id")
    return {"label": overview["label"], "area_count": len(areas),
            "direction_count": sum(len(a["directions"]) for a in areas), "areas": areas}
