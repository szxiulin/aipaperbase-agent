"""Area/direction queries for the v0.2 data layer.

The payload keeps the shape the existing insights tree already renders (`topics -> subtopics`,
`topic_id`, `name`, `entity_count`, `coverage_rate`) while adding `node_type` and label-version
metadata. Denominators exclude the 251 non-paper records unless stated otherwise.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any


def _dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def latest_label_release(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM label_releases WHERE status = 'ready' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()


def _metrics(release: sqlite3.Row | None) -> dict[str, Any]:
    if not release:
        return {}
    try:
        return json.loads(release["metrics_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def label_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    release = latest_label_release(connection)
    if not release:
        return {"release": None, "metrics": {}, "entity_count": 0, "non_paper_count": 0}
    return {"release": dict(release), "metrics": _metrics(release),
            "entity_count": release["entity_count"], "non_paper_count": release["non_paper_count"]}


def areas_overview(connection: sqlite3.Connection) -> dict[str, Any]:
    release = latest_label_release(connection)
    if not release:
        return {"label": None, "topics": [], "entity_count": 0, "classified_entity_count": 0}

    area_defs = _dicts(connection.execute(
        "SELECT * FROM area_definitions ORDER BY sort_order").fetchall())
    direction_defs = _dicts(connection.execute(
        "SELECT * FROM direction_definitions ORDER BY sort_order").fetchall())
    area_counts = {r["area_id"]: r["n"] for r in connection.execute(
        "SELECT area_id, COUNT(*) AS n FROM entity_area_assignments WHERE is_non_paper = 0 GROUP BY area_id")}
    direction_counts = {r["direction_id"]: dict(r) for r in connection.execute(
        """SELECT direction_id, COUNT(*) AS n, SUM(needs_review) AS review
           FROM entity_direction_assignments GROUP BY direction_id""")}

    total = connection.execute("SELECT COUNT(*) FROM paper_entities").fetchone()[0]
    direction_classified = connection.execute(
        "SELECT COUNT(*) FROM entity_direction_assignments").fetchone()[0]
    classified = sum(n for area_id, n in area_counts.items() if area_id != "pending_classification")
    pending_directions = {r[0]: r[1] for r in connection.execute("""SELECT a.area_id, COUNT(*)
        FROM entity_area_assignments a LEFT JOIN entity_direction_assignments d USING(entity_id)
        WHERE a.is_non_paper=0 AND a.area_id!='pending_classification' AND d.entity_id IS NULL
        GROUP BY a.area_id""")}
    non_paper = connection.execute(
        "SELECT COUNT(*) FROM entity_area_assignments WHERE is_non_paper = 1").fetchone()[0]

    venue_rows = _dicts(connection.execute(
        """SELECT a.area_id, p.venue, COUNT(DISTINCT a.entity_id) AS entity_count
           FROM entity_area_assignments a
           JOIN entity_memberships m ON m.entity_id = a.entity_id AND m.is_canonical = 1
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.is_non_paper = 0
           GROUP BY a.area_id, p.venue ORDER BY a.area_id, entity_count DESC""").fetchall())
    top_venues: dict[str, list[dict]] = defaultdict(list)
    for row in venue_rows:
        if len(top_venues[row["area_id"]]) < 3:
            top_venues[row["area_id"]].append({"venue": row["venue"], "entity_count": row["entity_count"]})

    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in direction_defs:
        children[d["area_id"]].append(d)

    topics = []
    for area in area_defs:
        subs = []
        for d in children.get(area["area_id"], []):
            stat = direction_counts.get(d["direction_id"], {"n": 0, "review": 0})
            subs.append({
                "node_type": "direction", "topic_id": d["direction_id"],
                "parent_topic_id": d["area_id"], "name": d["name_zh"], "name_en": d["name_en"],
                "description": d["definition"], "status": d["status"], "sort_order": d["sort_order"],
                "entity_count": stat["n"], "needs_review_count": stat["review"] or 0, "subtopics": [],
            })
        n = area_counts.get(area["area_id"], 0)
        topics.append({
            "node_type": "area", "topic_id": area["area_id"], "parent_topic_id": "",
            "name": area["name_zh"], "name_en": area["name_en"], "description": area["scope"],
            "sort_order": area["sort_order"], "entity_count": n,
            "coverage_rate": round(100 * n / (total - non_paper), 1) if total - non_paper else 0,
            "direction_count": len(subs), "top_venues": top_venues.get(area["area_id"], []),
            "pending_direction_count": pending_directions.get(area["area_id"], 0),
            "subtopics": subs,
        })
    return {
        "label": {"release_id": release["label_release_id"], "areas_version": release["areas_version"],
                  "directions_version": release["directions_version"], "created_at": release["created_at"],
                  "metrics": _metrics(release)},
        "entity_count": total, "non_paper_count": non_paper,
        "classified_entity_count": classified,
        "direction_classified_entity_count": direction_classified,
        "pending_direction_count": sum(pending_directions.values()),
        "pending_classification_count": area_counts.get("pending_classification", 0),
        "classified_rate": round(100 * classified / (total - non_paper), 1) if total - non_paper else 0,
        "area_count": sum(a["area_id"] not in {"pending_classification", "other"} for a in area_defs), "direction_count": len(direction_defs),
        "topics": topics,
    }


def _year_series(connection: sqlite3.Connection, area_id: str = "", direction_id: str = "") -> tuple[list[dict], list[dict]]:
    where, params, join = "a.is_non_paper = 0", [], ""
    if direction_id:
        join = " JOIN entity_direction_assignments d ON d.entity_id = a.entity_id"
        where += " AND d.direction_id = ?"
        params.append(direction_id)
    if area_id:
        where += " AND a.area_id = ?"
        params.append(area_id)
    totals = _dicts(connection.execute(
        f"""SELECT p.year, COUNT(DISTINCT a.entity_id) AS total_entities,
                   COUNT(m.record_id) AS total_records,
                   SUM(p.list_status = 'final') AS final_records,
                   SUM(p.list_status = 'rolling') AS rolling_records
            FROM entity_area_assignments a{join}
            JOIN entity_memberships m ON m.entity_id = a.entity_id AND m.is_canonical = 1
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE {where} GROUP BY p.year ORDER BY p.year""", params).fetchall())
    rows = _dicts(connection.execute(
        f"""SELECT p.year, a.area_id, COUNT(DISTINCT a.entity_id) AS entity_count
            FROM entity_area_assignments a{join}
            JOIN entity_memberships m ON m.entity_id = a.entity_id AND m.is_canonical = 1
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE {where} GROUP BY p.year, a.area_id ORDER BY p.year""", params).fetchall())
    denominator = {r["year"]: r["total_entities"] for r in totals}
    names = {r["area_id"]: r["name_zh"] for r in connection.execute("SELECT area_id, name_zh FROM area_definitions")}
    for r in rows:
        r["topic_id"] = r["area_id"]  # compatibility with the existing trends table
        r["name"] = names.get(r["area_id"], r["area_id"])
        r["share_rate"] = round(100 * r["entity_count"] / denominator[r["year"]], 1) if denominator.get(r["year"]) else 0
    return totals, rows


def area_trends(connection: sqlite3.Connection, area_id: str = "") -> dict[str, Any]:
    if area_id and not connection.execute("SELECT 1 FROM area_definitions WHERE area_id = ?", (area_id,)).fetchone():
        raise ValueError("未知领域")
    totals, rows = _year_series(connection, area_id)
    return {"years": totals, "items": rows, "scope": [{"topic_id": area_id, "name": area_id}] if area_id else []}


def _top_venues(connection: sqlite3.Connection, *, area_id: str = "", direction_id: str = "",
                limit: int = 8) -> list[dict]:
    clauses, params = ["a.is_non_paper = 0"], []
    join = ""
    if direction_id:
        join = " JOIN entity_direction_assignments d ON d.entity_id = a.entity_id"
        clauses.append("d.direction_id = ?")
        params.append(direction_id)
    if area_id:
        clauses.append("a.area_id = ?")
        params.append(area_id)
    return _dicts(connection.execute(
        f"""SELECT p.venue, p.venue_type, COUNT(DISTINCT a.entity_id) AS entity_count,
                   COUNT(DISTINCT m.record_id) AS record_count
            FROM entity_area_assignments a{join}
            JOIN entity_memberships m ON m.entity_id = a.entity_id AND m.is_canonical = 1
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE {' AND '.join(clauses)}
            GROUP BY p.venue, p.venue_type ORDER BY entity_count DESC, p.venue LIMIT ?""",
        [*params, limit]).fetchall())


def _node_stats(connection: sqlite3.Connection, *, area_id: str = "", direction_id: str = "") -> dict[str, Any]:
    clauses, params = ["a.is_non_paper = 0"], []
    join = ""
    if direction_id:
        join = " JOIN entity_direction_assignments d ON d.entity_id = a.entity_id"
        clauses.append("d.direction_id = ?")
        params.append(direction_id)
    if area_id:
        clauses.append("a.area_id = ?")
        params.append(area_id)
    row = connection.execute(
        f"""SELECT COUNT(DISTINCT a.entity_id) AS entity_count,
                   COUNT(DISTINCT p.venue) AS venue_count,
                   MIN(p.year) AS min_year, MAX(p.year) AS max_year,
                   SUM(p.list_status = 'final') AS final_records,
                   SUM(p.list_status = 'rolling') AS rolling_records
            FROM entity_area_assignments a{join}
            JOIN entity_memberships m ON m.entity_id = a.entity_id AND m.is_canonical = 1
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE {' AND '.join(clauses)}""", params).fetchone()
    total_papers = connection.execute(
        "SELECT COUNT(*) FROM entity_area_assignments WHERE is_non_paper = 0").fetchone()[0]
    result = dict(row)
    result["coverage_rate"] = round(100 * result["entity_count"] / total_papers, 1) if total_papers else 0
    return result


def _detail_payload(connection: sqlite3.Connection, node: dict[str, Any], *, area_id: str = "",
                    direction_id: str = "", subtopics: list[dict] | None = None) -> dict[str, Any]:
    stat = _node_stats(connection, area_id=area_id, direction_id=direction_id)
    total = stat["entity_count"] or 0
    for item in subtopics or []:
        item["share_rate"] = round(100 * item["entity_count"] / total, 1) if total else 0
    venues = _top_venues(connection, area_id=area_id, direction_id=direction_id, limit=10)
    for v in venues:
        v["share_rate"] = round(100 * v["entity_count"] / total, 1) if total else 0
    totals, rows = _year_series(connection, area_id, direction_id)
    return {
        "topic": node,
        "metrics": {"entity_count": stat["entity_count"], "average_score": None,
                    "assignment_count": stat["entity_count"], "extra_count": 0,
                    "venue_count": stat["venue_count"], "year_count": (stat["max_year"] or 0) - (stat["min_year"] or 0) + 1,
                    "min_year": stat["min_year"], "max_year": stat["max_year"],
                    "final_records": stat["final_records"] or 0, "rolling_records": stat["rolling_records"] or 0,
                    "coverage_rate": stat["coverage_rate"]},
        "subtopics": subtopics or [],
        "top_venues": venues,
        "tech_tags": [],
        "trend": {"years": totals, "items": rows},
        "papers_total": stat["entity_count"],
    }


def area_detail(connection: sqlite3.Connection, area_id: str) -> dict[str, Any]:
    area = connection.execute("SELECT * FROM area_definitions WHERE area_id = ?", (area_id,)).fetchone()
    if not area:
        raise ValueError("未知领域")
    directions = _dicts(connection.execute(
        """SELECT d.direction_id AS topic_id, d.name_zh AS name, d.name_en, d.definition AS description,
                  d.status, COUNT(a.entity_id) AS entity_count,
                  COALESCE(SUM(a.needs_review), 0) AS needs_review_count
           FROM direction_definitions d
           LEFT JOIN entity_direction_assignments a ON a.direction_id = d.direction_id
           WHERE d.area_id = ? GROUP BY d.direction_id ORDER BY entity_count DESC, d.direction_id""",
        (area_id,)).fetchall())
    for d in directions:
        d["node_type"] = "direction"
    node = {"topic_id": area["area_id"], "parent_topic_id": "", "name": area["name_zh"],
            "name_en": area["name_en"], "description": area["scope"], "level": "area",
            "node_type": "area", "status": "confirmed"}
    return _detail_payload(connection, node, area_id=area_id, subtopics=directions)


def direction_detail(connection: sqlite3.Connection, direction_id: str) -> dict[str, Any]:
    d = connection.execute("SELECT * FROM direction_definitions WHERE direction_id = ?", (direction_id,)).fetchone()
    if not d:
        raise ValueError("未知方向")
    area = connection.execute("SELECT * FROM area_definitions WHERE area_id = ?", (d["area_id"],)).fetchone()
    node = {"topic_id": d["direction_id"], "parent_topic_id": d["area_id"], "name": d["name_zh"],
            "name_en": d["name_en"], "description": d["definition"], "level": "direction",
            "node_type": "direction", "status": d["status"], "area_name": area["name_zh"] if area else ""}
    return _detail_payload(connection, node, area_id=d["area_id"], direction_id=direction_id)


def _paper_where(area_id: str, direction_id: str, year: int | None, venue: str, search: str) -> tuple[str, list[Any]]:
    clauses, params = ["a.is_non_paper = 0"], []
    if area_id:
        clauses.append("a.area_id = ?")
        params.append(area_id)
    if direction_id:
        clauses.append("d.direction_id = ?")
        params.append(direction_id)
    if year is not None:
        clauses.append("EXISTS (SELECT 1 FROM entity_memberships ym JOIN paper_records yp ON yp.record_id = ym.record_id "
                       "WHERE ym.entity_id = a.entity_id AND ym.is_canonical = 1 AND yp.year = ?)")
        params.append(year)
    if venue:
        clauses.append("EXISTS (SELECT 1 FROM entity_memberships vm JOIN paper_records vp ON vp.record_id = vm.record_id "
                       "WHERE vm.entity_id = a.entity_id AND vm.is_canonical = 1 AND vp.venue = ?)")
        params.append(venue)
    if search:
        clauses.append("(e.canonical_title LIKE ? OR p.authors LIKE ? OR p.abstract LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term])
    return " WHERE " + " AND ".join(clauses), params


def papers(connection: sqlite3.Connection, *, area_id: str = "", direction_id: str = "", page: int = 1,
           page_size: int = 25, year: int | None = None, venue: str = "", search: str = "") -> dict[str, Any]:
    if area_id and not connection.execute("SELECT 1 FROM area_definitions WHERE area_id = ?", (area_id,)).fetchone():
        raise ValueError("未知领域")
    if direction_id and not connection.execute("SELECT 1 FROM direction_definitions WHERE direction_id = ?",
                                                (direction_id,)).fetchone():
        raise ValueError("未知方向")
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    where, params = _paper_where(area_id, direction_id, year, venue, search)
    base = """ FROM entity_area_assignments a
               LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
               JOIN paper_entities e ON e.entity_id = a.entity_id
               JOIN paper_records p ON p.record_id = e.canonical_record_id"""
    total = connection.execute(f"SELECT COUNT(*){base}{where}", params).fetchone()[0]
    items = _dicts(connection.execute(
        f"""SELECT a.entity_id, a.area_id, d.direction_id, e.canonical_title AS title, e.first_year, e.last_year,
                   e.record_count, p.authors, p.abstract, p.paper_url, p.doi, p.arxiv_id
            {base}{where}
            ORDER BY e.last_year DESC, e.canonical_title LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size]).fetchall())
    ids = [x["entity_id"] for x in items]
    appearances = defaultdict(list)
    if ids:
        marks = ",".join("?" for _ in ids)
        for row in connection.execute(
                f"""SELECT m.entity_id, p.venue, p.year, p.list_status, p.paper_url
                    FROM entity_memberships m JOIN paper_records p ON p.record_id = m.record_id
                    WHERE m.entity_id IN ({marks}) ORDER BY m.entity_id, p.year, p.venue""", ids):
            item = dict(row)
            appearances[item.pop("entity_id")].append(item)
    for item in items:
        item["appearances"] = appearances[item["entity_id"]]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size}


def label_evaluation(connection: sqlite3.Connection) -> dict[str, Any]:
    release = latest_label_release(connection)
    if not release:
        return {"release": None, "metrics": {}, "samples": []}
    rows = _dicts(connection.execute(
        "SELECT * FROM label_evaluation_samples WHERE label_release_id = ? ORDER BY sample_order",
        (release["label_release_id"],)).fetchall())
    return {"release": dict(release), "metrics": _metrics(release), "samples": rows}
