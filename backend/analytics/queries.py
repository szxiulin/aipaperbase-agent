from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------- helpers


def _dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _topic_definitions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """All topic nodes (including families/containers/leaves), returned in config order."""
    return _dicts(connection.execute(
        """SELECT topic_id, parent_topic_id, name, description, config_version, sort_order
           FROM topic_definitions ORDER BY sort_order"""
    ).fetchall())


def _latest_topic_run(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM analysis_runs
           WHERE analysis_type = 'topic_classification' AND status = 'ready'
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()


def _latest_method_run(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM analysis_runs
           WHERE analysis_type = 'tech_tag_classification' AND status = 'ready'
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()


def _node_primary_counts(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per-node (family/direction/leaf) primary roll-up entity count and average score.

    An entity belongs to node N iff its primary leaf's ancestor chain contains N (topic_ancestors includes itself depth=0).
    Families are mutually exclusive ⇒ each family's entity counts can be summed to the classified total.
    """
    rows = connection.execute(
        """SELECT x.ancestor_topic_id AS node_id,
                  COUNT(DISTINCT a.entity_id) AS entity_count,
                  ROUND(AVG(a.score), 1) AS average_score
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           WHERE a.role = 'primary'
           GROUP BY x.ancestor_topic_id"""
    ).fetchall()
    return {row["node_id"]: dict(row) for row in rows}


def _family_top_venues(
    connection: sqlite3.Connection,
    family_ids: list[str],
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    if not family_ids:
        return {}
    placeholders = ",".join("?" for _ in family_ids)
    rows = connection.execute(
        f"""SELECT fam.ancestor_topic_id AS family_id, p.venue,
                   COUNT(DISTINCT a.entity_id) AS entity_count
            FROM entity_topic_assignments a
            JOIN topic_ancestors fam ON fam.topic_id = a.topic_id
            JOIN entity_memberships m ON m.entity_id = a.entity_id
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE a.role = 'primary' AND fam.ancestor_topic_id IN ({placeholders})
            GROUP BY fam.ancestor_topic_id, p.venue
            ORDER BY fam.ancestor_topic_id, entity_count DESC, p.venue""",
        family_ids,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if len(result[row["family_id"]]) < limit:
            result[row["family_id"]].append({"venue": row["venue"], "entity_count": row["entity_count"]})
    return dict(result)


# ---------------------------------------------------------------- topic_overview


def topic_overview(connection: sqlite3.Connection) -> dict[str, Any]:
    """The "Paper Classification" section of the home page: returns the whole topic tree (family→direction/leaf, with counts)
    using the v2 primary basis.

    - family counts are mutually exclusive (one primary per entity), so shares can be summed;
    - topics is a recursive tree (subtopics nested), renderable directly by the frontend;
    - also includes classification quality metrics: classified/coverage/multi_label_rate.
    """
    run = _latest_topic_run(connection)
    total_entities = connection.execute("SELECT COUNT(*) FROM paper_entities").fetchone()[0]
    classified = connection.execute(
        "SELECT COUNT(*) FROM entity_topic_assignments WHERE role = 'primary'"
    ).fetchone()[0]
    extra_entities = connection.execute(
        """SELECT COUNT(*) FROM (
               SELECT entity_id FROM entity_topic_assignments GROUP BY entity_id HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]

    definitions = _topic_definitions(connection)
    counts = _node_primary_counts(connection)
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    families: list[dict[str, Any]] = []
    for item in definitions:
        children[item["parent_topic_id"]].append(item)
        if item["parent_topic_id"] == "":
            families.append(item)
    top_venues = _family_top_venues(connection, [item["topic_id"] for item in families])

    def build_node(item: dict[str, Any]) -> dict[str, Any]:
        node_id = item["topic_id"]
        stat = counts.get(node_id, {"entity_count": 0, "average_score": None})
        entry = {
            "topic_id": node_id,
            "parent_topic_id": item["parent_topic_id"],
            "name": item["name"],
            "description": item["description"],
            "sort_order": item["sort_order"],
            "entity_count": stat["entity_count"],
            "average_score": stat["average_score"],
            "subtopics": [build_node(child) for child in sorted(children[node_id], key=lambda d: d["sort_order"])],
        }
        if item["parent_topic_id"] == "":
            entry["coverage_rate"] = (
                round(100.0 * stat["entity_count"] / total_entities, 1) if total_entities else 0
            )
            entry["top_venues"] = top_venues.get(node_id, [])
        return entry

    return {
        "run": dict(run) if run else None,
        "method_run": dict(_latest_method_run(connection)) if run else None,
        "entity_count": total_entities,
        "classified_entity_count": classified,
        "classified_rate": round(100 * classified / total_entities, 1) if total_entities else 0,
        "unclassified_entity_count": total_entities - classified,
        "extra_entity_count": extra_entities,
        "multi_label_rate": round(100 * extra_entities / classified, 1) if classified else 0,
        "root_assignment_count": sum(
            counts.get(item["topic_id"], {"entity_count": 0})["entity_count"] for item in families
        ),
        "topics": [build_node(item) for item in sorted(families, key=lambda d: d["sort_order"])],
    }


# ---------------------------------------------------------------- topic_trends


def topic_trends(connection: sqlite3.Connection, topic_id: str = "") -> dict[str, Any]:
    """Yearly trend. Empty topic_id → all families; otherwise the per-year counts of the given node (family/direction/leaf).

    All counts are on the primary basis (each entity counts at most once under a given node).
    """
    definitions = _topic_definitions(connection)
    if topic_id:
        valid = any(item["topic_id"] == topic_id for item in definitions)
        if not valid:
            raise ValueError("未知研究主题")
        scope = [(topic_id, topic_id)]
        scope_sql = " AND x.ancestor_topic_id = ?"
    else:
        scope = [(item["topic_id"], item["name"]) for item in definitions if item["parent_topic_id"] == ""]
        scope_sql = ""

    years = _dicts(connection.execute(
        """SELECT p.year,
                  COUNT(DISTINCT m.entity_id) AS total_entities,
                  SUM(p.list_status = 'final') AS final_records,
                  SUM(p.list_status = 'rolling') AS rolling_records
           FROM paper_records p
           JOIN entity_memberships m ON m.record_id = p.record_id
           GROUP BY p.year ORDER BY p.year"""
    ).fetchall())
    rows = _dicts(connection.execute(
        f"""SELECT p.year, x.ancestor_topic_id AS topic_id,
                   COUNT(DISTINCT a.entity_id) AS entity_count
            FROM entity_topic_assignments a
            JOIN topic_ancestors x ON x.topic_id = a.topic_id
            JOIN entity_memberships m ON m.entity_id = a.entity_id
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE a.role = 'primary'{scope_sql}
            GROUP BY p.year, x.ancestor_topic_id
            ORDER BY p.year""",
        [topic_id] if topic_id else [],
    ).fetchall())
    name_by_id = {item["topic_id"]: item["name"] for item in definitions}
    for item in rows:
        item["name"] = name_by_id.get(item["topic_id"], item["topic_id"])
    denominator = {item["year"]: item["total_entities"] for item in years}
    for item in rows:
        item["share_rate"] = round(100 * item["entity_count"] / denominator[item["year"]], 1)
    return {"years": years, "items": rows, "scope": [{"topic_id": sid, "name": name} for sid, name in scope]}


# ---------------------------------------------------------------- topic drilldown


def _topic_paper_where(
    connection: sqlite3.Connection,
    topic_id: str,
    year: int | None,
    venue: str,
    search: str,
) -> tuple[str, list[Any]]:
    definitions = _topic_definitions(connection)
    if not any(item["topic_id"] == topic_id for item in definitions):
        raise ValueError("未知研究主题")
    clauses = ["a.role = 'primary'", "x.ancestor_topic_id = ?"]
    params: list[Any] = [topic_id]
    if year is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM entity_memberships ym JOIN paper_records yp ON yp.record_id = ym.record_id WHERE ym.entity_id = a.entity_id AND yp.year = ?)"
        )
        params.append(year)
    if venue:
        clauses.append(
            "EXISTS (SELECT 1 FROM entity_memberships vm JOIN paper_records vp ON vp.record_id = vm.record_id WHERE vm.entity_id = a.entity_id AND vp.venue = ?)"
        )
        params.append(venue)
    if search:
        clauses.append("(e.canonical_title LIKE ? OR p.authors LIKE ? OR p.abstract LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term])
    return " WHERE " + " AND ".join(clauses), params


_PAPER_BASE = """ FROM entity_topic_assignments a
                  JOIN topic_ancestors x ON x.topic_id = a.topic_id
                  JOIN paper_entities e ON e.entity_id = a.entity_id
                  JOIN paper_records p ON p.record_id = e.canonical_record_id"""


def topic_papers_entity_ids(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    year: int | None = None,
    venue: str = "",
    search: str = "",
) -> dict[str, Any]:
    """All entity_ids of primary papers under a given node (family/direction/leaf), unpaginated."""
    where, params = _topic_paper_where(connection, topic_id, year, venue, search)
    rows = connection.execute(
        f"SELECT DISTINCT a.entity_id{_PAPER_BASE}{where} ORDER BY a.entity_id", params
    ).fetchall()
    entity_ids = [row["entity_id"] for row in rows]
    return {"entity_ids": entity_ids, "total": len(entity_ids)}


def topic_papers(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    page: int = 1,
    page_size: int = 25,
    year: int | None = None,
    venue: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Paged list of primary papers under a given node, ordered by primary score."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    where, params = _topic_paper_where(connection, topic_id, year, venue, search)
    total = connection.execute(f"SELECT COUNT(*){_PAPER_BASE}{where}", params).fetchone()[0]
    items = _dicts(connection.execute(
        f"""SELECT a.entity_id, a.score, a.evidence_json, a.classifier_version,
                   e.canonical_title AS title, e.first_year, e.last_year,
                   e.record_count, p.authors, p.abstract, p.paper_url, p.doi, p.arxiv_id
            {_PAPER_BASE}{where}
            ORDER BY a.score DESC, e.last_year DESC, e.canonical_title
            LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall())
    ids = [item["entity_id"] for item in items]
    appearances: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT m.entity_id, p.venue, p.year, p.list_status, p.paper_url
                FROM entity_memberships m
                JOIN paper_records p ON p.record_id = m.record_id
                WHERE m.entity_id IN ({placeholders})
                ORDER BY m.entity_id, p.year, p.venue""",
            ids,
        ).fetchall()
        for row in rows:
            item = dict(row)
            appearances[item.pop("entity_id")].append(item)
    for item in items:
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["appearances"] = appearances[item["entity_id"]]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


# ---------------------------------------------------------------- evaluation (v1 interface, unchanged structure)


def _wilson_interval(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    ratio = successes / total
    denominator = 1 + z * z / total
    center = (ratio + z * z / (2 * total)) / denominator
    half = z * math.sqrt(ratio * (1 - ratio) / total + z * z / (4 * total * total)) / denominator
    return [round(100 * max(0, center - half), 1), round(100 * min(1, center + half), 1)]


def _evaluation_metrics(rows: list[sqlite3.Row]) -> dict[str, Any]:
    counts = defaultdict(int)
    for row in rows:
        counts[row["verdict"]] += 1
    reviewed = sum(counts[key] for key in ("relevant", "mention_only", "wrong_signal", "uncertain"))
    decided = reviewed - counts["uncertain"]
    relevant = counts["relevant"]
    return {
        "sampled_count": len(rows),
        "reviewed_count": reviewed,
        "pending_count": counts["pending"],
        "relevant_count": relevant,
        "mention_only_count": counts["mention_only"],
        "wrong_signal_count": counts["wrong_signal"],
        "uncertain_count": counts["uncertain"],
        "preliminary_precision": round(100 * relevant / decided, 1) if decided else None,
        "wilson_95": _wilson_interval(relevant, decided),
    }


def topic_evaluation(connection: sqlite3.Connection) -> dict[str, Any]:
    evaluation = connection.execute(
        "SELECT * FROM topic_evaluation_sets ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not evaluation:
        raise ValueError("尚未加载主题分类评估集")
    version = evaluation["evaluation_version"]
    rows = connection.execute(
        """SELECT s.*, t.name AS topic_name, t.sort_order
           FROM topic_evaluation_samples s
           JOIN topic_definitions t ON t.topic_id = s.topic_id
           WHERE s.evaluation_version = ?
           ORDER BY t.sort_order, s.score DESC, s.sample_id""",
        (version,),
    ).fetchall()
    overall = _evaluation_metrics(rows)
    by_topic: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_topic[row["topic_id"]].append(row)
    topics = []
    for topic_rows in by_topic.values():
        topic = {
            "topic_id": topic_rows[0]["topic_id"],
            "topic_name": topic_rows[0]["topic_name"],
            **_evaluation_metrics(topic_rows),
            "bands": [],
        }
        for band in ("high", "middle", "boundary"):
            band_rows = [row for row in topic_rows if row["score_band"] == band]
            topic["bands"].append({"score_band": band, **_evaluation_metrics(band_rows)})
        topics.append(topic)
    error_rows = connection.execute(
        """SELECT error_type, COUNT(*) AS count
           FROM topic_evaluation_samples
           WHERE evaluation_version = ? AND error_type <> ''
           GROUP BY error_type ORDER BY count DESC, error_type""",
        (version,),
    ).fetchall()
    return {
        "evaluation": dict(evaluation),
        "overall": overall,
        "review_progress": round(100 * overall["reviewed_count"] / overall["sampled_count"], 1) if overall["sampled_count"] else 0,
        "topics": topics,
        "error_groups": _dicts(error_rows),
        "metric_note": "初步精度仅基于已审阅且非 uncertain 的样本；当前为模型辅助标注，不是人工金标准。",
    }


def topic_evaluation_samples(
    connection: sqlite3.Connection,
    *,
    topic_id: str = "",
    verdict: str = "",
    score_band: str = "",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    evaluation = connection.execute(
        "SELECT evaluation_version FROM topic_evaluation_sets ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not evaluation:
        raise ValueError("尚未加载主题分类评估集")
    clauses = ["s.evaluation_version = ?"]
    params: list[Any] = [evaluation[0]]
    if topic_id:
        clauses.append("s.topic_id = ?")
        params.append(topic_id)
    if verdict:
        if verdict not in {"pending", "relevant", "mention_only", "wrong_signal", "uncertain"}:
            raise ValueError("未知 verdict")
        clauses.append("s.verdict = ?")
        params.append(verdict)
    if score_band:
        if score_band not in {"high", "middle", "boundary"}:
            raise ValueError("未知 score_band")
        clauses.append("s.score_band = ?")
        params.append(score_band)
    where = " WHERE " + " AND ".join(clauses)
    base = """ FROM topic_evaluation_samples s
               JOIN topic_definitions t ON t.topic_id = s.topic_id"""
    total = connection.execute(f"SELECT COUNT(*){base}{where}", params).fetchone()[0]
    items = _dicts(connection.execute(
        f"""SELECT s.sample_id, s.topic_id, t.name AS topic_name, s.entity_id,
                   s.score, s.score_band, s.title_snapshot AS title,
                   s.appearances_snapshot AS appearances, s.evidence_json,
                   s.verdict, s.error_type, s.rationale, s.reviewer_type,
                   s.reviewer_name, s.evidence_level, s.reviewed_at
            {base}{where}
            ORDER BY CASE s.verdict
                       WHEN 'wrong_signal' THEN 0 WHEN 'mention_only' THEN 1
                       WHEN 'uncertain' THEN 2 WHEN 'relevant' THEN 3 ELSE 4 END,
                     t.sort_order, s.score_band, s.score DESC, s.sample_id
            LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall())
    for item in items:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


# ---------------------------------------------------------------- venue_overview (Data Insights 2.0 · conferences & journals section)


_UNCLASSIFIED_BUCKET = "未分类"


def venue_overview(connection: sqlite3.Connection) -> dict[str, Any]:
    """Each venue's collection size + topic composition (the "Conferences & Journals" section of Data Insights).

    Basis:
    - entity_count = deduped entity count that has appeared (≥1 record) at that venue;
    - topic_mix rolls up each entity's primary leaf to the family root; entities without a primary
      fall into the "unclassified" bucket; the sum of all buckets' entity_count always equals that
      venue's entity_count (integer invariant, locked by tests);
    - an entity appearing at multiple venues is counted in each venue's composition (describing
      "what directions this venue covers").
    """
    records = _dicts(connection.execute(
        """SELECT venue, venue_type,
                  COUNT(*) AS record_count,
                  COUNT(DISTINCT year) AS year_count,
                  MIN(year) AS min_year,
                  MAX(year) AS max_year,
                  SUM(list_status = 'final') AS final_count,
                  SUM(list_status = 'rolling') AS rolling_count
           FROM paper_records
           GROUP BY venue, venue_type"""
    ).fetchall())
    if not records:
        return {"items": [], "total": 0}

    entity_counts = {
        row["venue"]: row["entity_count"]
        for row in connection.execute(
            """SELECT p.venue, COUNT(DISTINCT m.entity_id) AS entity_count
               FROM paper_records p
               JOIN entity_memberships m ON m.record_id = p.record_id
               GROUP BY p.venue"""
        ).fetchall()
    }

    # venue × family composition: roll each entity's primary leaf up to the family root; no primary → unclassified bucket.
    # The ancestor chain only allows family roots (parent_topic_id=''), otherwise direction-container rows would
    # LEFT JOIN into name=NULL pseudo buckets due to mismatched td.
    mix_rows = connection.execute(
        """WITH roots AS (SELECT topic_id FROM topic_definitions WHERE parent_topic_id = '')
           SELECT p.venue,
                  x.ancestor_topic_id AS family_id,
                  td.name AS family_name,
                  COUNT(DISTINCT m.entity_id) AS entity_count
           FROM paper_records p
           JOIN entity_memberships m ON m.record_id = p.record_id
           LEFT JOIN entity_topic_assignments a
                  ON a.entity_id = m.entity_id AND a.role = 'primary'
           LEFT JOIN topic_ancestors x
                  ON x.topic_id = a.topic_id
                 AND x.ancestor_topic_id IN (SELECT topic_id FROM roots)
           LEFT JOIN topic_definitions td ON td.topic_id = x.ancestor_topic_id
           GROUP BY p.venue, x.ancestor_topic_id, td.name"""
    ).fetchall()
    mix_by_venue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in mix_rows:
        mix_by_venue[row["venue"]].append({
            "topic_id": row["family_id"] or "",
            "name": row["family_name"] or _UNCLASSIFIED_BUCKET,
            "entity_count": row["entity_count"],
        })

    items: list[dict[str, Any]] = []
    for item in records:
        venue = item["venue"]
        denominator = entity_counts.get(venue) or 0
        mix = sorted(mix_by_venue.get(venue, []), key=lambda d: (-d["entity_count"], d["name"]))
        for entry in mix:
            entry["share_rate"] = round(100 * entry["entity_count"] / denominator, 1) if denominator else 0
        item["entity_count"] = denominator
        item["topic_mix"] = mix
        items.append(item)
    items.sort(key=lambda d: (-d["record_count"], d["venue"]))
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------- topic_detail (Data Insights 2.0 · family detail page)


def _topic_node_top_venues(
    connection: sqlite3.Connection,
    node_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top venues of primary entities under any node (family/direction/leaf)."""
    rows = connection.execute(
        """SELECT p.venue,
                  COUNT(DISTINCT a.entity_id) AS entity_count,
                  COUNT(DISTINCT p.record_id) AS record_count
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           JOIN entity_memberships m ON m.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.role = 'primary' AND x.ancestor_topic_id = ?
           GROUP BY p.venue
           ORDER BY entity_count DESC, p.venue
           LIMIT ?""",
        (node_id, limit),
    ).fetchall()
    return _dicts(rows)


def _topic_node_tech_tags(
    connection: sqlite3.Connection,
    node_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top tech_tags of primary entities under a node (the "how it's done" adverb-cloud data)."""
    rows = connection.execute(
        """SELECT t.tag,
                  COUNT(DISTINCT t.entity_id) AS entity_count
           FROM entity_tech_tags t
           JOIN topic_ancestors x ON x.topic_id = (
               SELECT a.topic_id FROM entity_topic_assignments a
               WHERE a.entity_id = t.entity_id AND a.role = 'primary' LIMIT 1
           )
           WHERE x.ancestor_topic_id = ?
           GROUP BY t.tag
           ORDER BY entity_count DESC, t.tag
           LIMIT ?""",
        (node_id, limit),
    ).fetchall()
    return _dicts(rows)


def _topic_node_mix(
    connection: sqlite3.Connection,
    node_id: str,
) -> list[dict[str, Any]]:
    """Topic distribution of a node's sub-directions/leaves: roll primary leaves up via topic_ancestors to node_id's one-hop descendants.

    - family-root node: returns its direct child (direction container) distribution
    - direction node: returns the leaf-topic distribution under it
    - leaf node: returns itself (single row, count = node.entity_count)
    """
    node = connection.execute(
        "SELECT * FROM topic_definitions WHERE topic_id = ?", (node_id,)
    ).fetchone()
    if not node:
        return []
    children = connection.execute(
        "SELECT topic_id, name, sort_order FROM topic_definitions WHERE parent_topic_id = ? ORDER BY sort_order, topic_id",
        (node_id,),
    ).fetchall()
    if not children:
        # leaf node: return itself
        own = connection.execute(
            """SELECT COUNT(DISTINCT a.entity_id) AS entity_count
               FROM entity_topic_assignments a
               JOIN topic_ancestors x ON x.topic_id = a.topic_id
               WHERE a.role = 'primary' AND x.ancestor_topic_id = ?""",
            (node_id,),
        ).fetchone()
        return [{
            "topic_id": node_id,
            "name": node["name"],
            "level": "leaf",
            "entity_count": own["entity_count"] or 0,
        }]
    rows = connection.execute(
        """SELECT c.topic_id, c.name,
                  COUNT(DISTINCT a.entity_id) AS entity_count
           FROM topic_definitions c
           LEFT JOIN topic_ancestors x ON x.ancestor_topic_id = c.topic_id
           LEFT JOIN entity_topic_assignments a
                  ON a.topic_id = x.topic_id AND a.role = 'primary'
           WHERE c.parent_topic_id = ?
           GROUP BY c.topic_id, c.name, c.sort_order
           ORDER BY c.sort_order, c.topic_id""",
        (node_id,),
    ).fetchall()
    result = _dicts(rows)
    for item in result:
        item["level"] = "leaf"  # v2 topic tree is 2-level (family→leaf), no direction containers
    return result


def _topic_node_year_spread(
    connection: sqlite3.Connection,
    node_id: str,
) -> dict[str, Any]:
    """Year distribution of primary entities under a node."""
    row = connection.execute(
        """SELECT MIN(p.year) AS min_year,
                  MAX(p.year) AS max_year,
                  COUNT(DISTINCT p.year) AS year_count,
                  SUM(p.list_status = 'final') AS final_records,
                  SUM(p.list_status = 'rolling') AS rolling_records
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           JOIN entity_memberships m ON m.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.role = 'primary' AND x.ancestor_topic_id = ?""",
        (node_id,),
    ).fetchone()
    return dict(row) if row else {
        "min_year": None, "max_year": None, "year_count": 0,
        "final_records": 0, "rolling_records": 0,
    }


def topic_detail(connection: sqlite3.Connection, topic_id: str) -> dict[str, Any]:
    """Family/direction/leaf detail-page data: base metrics + subtopic distribution + top venues + tech_tags + trend + total papers."""
    if not topic_id:
        raise ValueError("topic_id required")
    definitions = _topic_definitions(connection)
    node = next((d for d in definitions if d["topic_id"] == topic_id), None)
    if not node:
        raise ValueError("未知研究主题")

    # base metrics
    primary_row = connection.execute(
        """SELECT COUNT(DISTINCT a.entity_id) AS entity_count,
                  ROUND(AVG(a.score), 2) AS average_score
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           WHERE a.role = 'primary' AND x.ancestor_topic_id = ?""",
        (topic_id,),
    ).fetchone()
    assignment_row = connection.execute(
        """SELECT COUNT(*) AS assignment_count,
                  SUM(CASE WHEN role = 'extra' THEN 1 ELSE 0 END) AS extra_count
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           WHERE x.ancestor_topic_id = ?""",
        (topic_id,),
    ).fetchone()
    venue_row = connection.execute(
        """SELECT COUNT(DISTINCT p.venue) AS venue_count
           FROM entity_topic_assignments a
           JOIN topic_ancestors x ON x.topic_id = a.topic_id
           JOIN entity_memberships m ON m.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.role = 'primary' AND x.ancestor_topic_id = ?""",
        (topic_id,),
    ).fetchone()
    year = _topic_node_year_spread(connection, topic_id)
    sub_mix = _topic_node_mix(connection, topic_id)
    top_venues_raw = _topic_node_top_venues(connection, topic_id, limit=10)
    tech_tags_raw = _topic_node_tech_tags(connection, topic_id, limit=10)
    total_entities = primary_row["entity_count"] or 0
    for v in top_venues_raw:
        v["share_rate"] = round(100 * v["entity_count"] / total_entities, 1) if total_entities else 0
    for t in tech_tags_raw:
        t["share_rate"] = round(100 * t["entity_count"] / total_entities, 1) if total_entities else 0
    for item in sub_mix:
        item["share_rate"] = round(100 * item["entity_count"] / total_entities, 1) if total_entities else 0
    trend = topic_trends(connection, topic_id=topic_id)
    papers_total = topic_papers_entity_ids(connection, topic_id=topic_id)["total"]

    catalog_total = connection.execute("SELECT COUNT(*) FROM paper_entities").fetchone()[0]
    coverage = round(100 * total_entities / catalog_total, 1) if catalog_total else 0
    level = "family" if node["parent_topic_id"] == "" else "leaf"
    return {
        "topic": {**dict(node), "level": level},
        "metrics": {
            "entity_count": total_entities,
            "average_score": primary_row["average_score"],
            "assignment_count": assignment_row["assignment_count"] or 0,
            "extra_count": assignment_row["extra_count"] or 0,
            "venue_count": venue_row["venue_count"] or 0,
            "year_count": year["year_count"] or 0,
            "min_year": year["min_year"],
            "max_year": year["max_year"],
            "final_records": year["final_records"] or 0,
            "rolling_records": year["rolling_records"] or 0,
            "coverage_rate": coverage,
        },
        "subtopics": sub_mix,
        "top_venues": top_venues_raw,
        "tech_tags": tech_tags_raw,
        "trend": {"years": trend["years"], "items": trend["items"]},
        "papers_total": papers_total,
    }


# ---------------------------------------------------------------- venue_detail (Data Insights 2.0 · venue detail page)


def _venue_topic_mix(
    connection: sqlite3.Connection,
    venue: str,
) -> list[dict[str, Any]]:
    """Distribution of entities at a venue by family root (10 families) + "unclassified" bucket.

    Same basis as venue_overview: an entity's primary leaf rolls up via topic_ancestors to the family root; no primary → "unclassified".
    """
    family_rows = connection.execute(
        "SELECT topic_id, name FROM topic_definitions WHERE parent_topic_id = '' ORDER BY sort_order"
    ).fetchall()
    family_ids = [row["topic_id"] for row in family_rows]
    if not family_ids:
        return []
    placeholders = ",".join("?" for _ in family_ids)
    rows = connection.execute(
        f"""SELECT x.ancestor_topic_id AS family_id,
                   td.name AS family_name,
                   COUNT(DISTINCT m.entity_id) AS entity_count
            FROM paper_records p
            JOIN entity_memberships m ON m.record_id = p.record_id
            LEFT JOIN entity_topic_assignments a
                   ON a.entity_id = m.entity_id AND a.role = 'primary'
            LEFT JOIN topic_ancestors x
                   ON x.topic_id = a.topic_id
                  AND x.ancestor_topic_id IN ({placeholders})
            LEFT JOIN topic_definitions td ON td.topic_id = x.ancestor_topic_id
            WHERE p.venue = ?
            GROUP BY x.ancestor_topic_id, td.name""",
        [*family_ids, venue],
    ).fetchall()
    name_by_id = {row["topic_id"]: row["name"] for row in family_rows}
    family_counts = {row["family_id"]: row["entity_count"] for row in rows}
    unclassified_row = next((row for row in rows if row["family_id"] is None), None)
    unclassified = unclassified_row["entity_count"] if unclassified_row else 0
    mix = []
    for fid in family_ids:
        mix.append({
            "topic_id": fid,
            "name": name_by_id[fid],
            "level": "family",
            "entity_count": family_counts.get(fid, 0),
        })
    mix.append({
        "topic_id": "",
        "name": _UNCLASSIFIED_BUCKET,
        "level": "unclassified",
        "entity_count": unclassified,
    })
    return mix


def _venue_tech_tags(
    connection: sqlite3.Connection,
    venue: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top tech_tags of entities at a venue."""
    rows = connection.execute(
        """SELECT t.tag,
                  COUNT(DISTINCT t.entity_id) AS entity_count
           FROM entity_tech_tags t
           JOIN entity_memberships m ON m.entity_id = t.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE p.venue = ?
           GROUP BY t.tag
           ORDER BY entity_count DESC, t.tag
           LIMIT ?""",
        (venue, limit),
    ).fetchall()
    return _dicts(rows)


def _venue_trend(
    connection: sqlite3.Connection,
    venue: str,
) -> dict[str, Any]:
    """venue 4-year trend (aggregated by year for record + entity + final/rolling)."""
    years = _dicts(connection.execute(
        """SELECT p.year,
                  COUNT(DISTINCT p.record_id) AS total_records,
                  COUNT(DISTINCT m.entity_id) AS total_entities,
                  SUM(p.list_status = 'final') AS final_records,
                  SUM(p.list_status = 'rolling') AS rolling_records
           FROM paper_records p
           LEFT JOIN entity_memberships m ON m.record_id = p.record_id
           WHERE p.venue = ?
           GROUP BY p.year ORDER BY p.year""",
        (venue,),
    ).fetchall())
    return {"years": years}


def _venue_top_topics(
    connection: sqlite3.Connection,
    venue: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top leaf topics of primary entities at a venue (ordered by entity_count descending)."""
    rows = connection.execute(
        """SELECT a.topic_id, t.name,
                  COUNT(DISTINCT a.entity_id) AS entity_count
           FROM entity_topic_assignments a
           JOIN topic_definitions t ON t.topic_id = a.topic_id
           JOIN entity_memberships m ON m.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.role = 'primary' AND p.venue = ?
           GROUP BY a.topic_id, t.name
           ORDER BY entity_count DESC, a.topic_id
           LIMIT ?""",
        (venue, limit),
    ).fetchall()
    return _dicts(rows)


def venue_detail(connection: sqlite3.Connection, venue: str) -> dict[str, Any]:
    """venue detail-page data: base metrics + family mix + tech_tags + trend + top leaves + total papers."""
    if not venue:
        raise ValueError("venue required")
    head = connection.execute(
        """SELECT venue, venue_type,
                  COUNT(*) AS record_count,
                  COUNT(DISTINCT entity_id) AS entity_count_raw,
                  COUNT(DISTINCT year) AS year_count,
                  MIN(year) AS min_year, MAX(year) AS max_year,
                  SUM(list_status = 'final') AS final_count,
                  SUM(list_status = 'rolling') AS rolling_count
           FROM (
               SELECT p.venue, p.venue_type, p.record_id, p.year, p.list_status, m.entity_id
               FROM paper_records p
               LEFT JOIN entity_memberships m ON m.record_id = p.record_id
               WHERE p.venue = ?
           )
           GROUP BY venue, venue_type""",
        (venue,),
    ).fetchone()
    if not head:
        return {
            "venue": {"venue": venue, "venue_type": "unknown"},
            "metrics": {
                "entity_count": 0, "record_count": 0, "year_count": 0,
                "min_year": None, "max_year": None,
                "final_count": 0, "rolling_count": 0,
            },
            "topic_mix": [], "tech_tags": [], "trend": {"years": []},
            "top_topics": [], "papers_total": 0,
        }

    entity_count = head["entity_count_raw"] or 0
    mix = _venue_topic_mix(connection, venue)
    for item in mix:
        item["share_rate"] = round(100 * item["entity_count"] / entity_count, 1) if entity_count else 0
    mix.sort(key=lambda d: (-d["entity_count"], d["name"]))
    tech_tags = _venue_tech_tags(connection, venue, limit=10)
    for t in tech_tags:
        t["share_rate"] = round(100 * t["entity_count"] / entity_count, 1) if entity_count else 0
    trend = _venue_trend(connection, venue)
    top_topics = _venue_top_topics(connection, venue, limit=10)
    for t in top_topics:
        t["share_rate"] = round(100 * t["entity_count"] / entity_count, 1) if entity_count else 0
    papers_total = connection.execute(
        "SELECT COUNT(*) FROM paper_records WHERE venue = ?", (venue,)
    ).fetchone()[0]
    return {
        "venue": {
            "venue": head["venue"],
            "venue_type": head["venue_type"],
        },
        "metrics": {
            "entity_count": entity_count,
            "record_count": head["record_count"],
            "year_count": head["year_count"],
            "min_year": head["min_year"],
            "max_year": head["max_year"],
            "final_count": head["final_count"] or 0,
            "rolling_count": head["rolling_count"] or 0,
        },
        "topic_mix": mix,
        "tech_tags": tech_tags,
        "trend": trend,
        "top_topics": top_topics,
        "papers_total": papers_total,
    }


# ---------------------------------------------------------------- venue_papers (venue paper list)


def venue_papers(
    connection: sqlite3.Connection,
    *,
    venue: str,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Paged paper list at a venue: deduplicate by entity picking a representative record (final first → smallest record_id)."""
    if not venue:
        raise ValueError("venue required")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = connection.execute(
        "SELECT COUNT(*) FROM paper_records WHERE venue = ?", (venue,)
    ).fetchone()[0]
    rows = connection.execute(
        """WITH venue_records AS (
               SELECT p.record_id, p.venue, p.year, p.list_status,
                      p.title, p.authors, p.abstract, p.paper_url, p.doi, p.arxiv_id,
                      m.entity_id,
                      ROW_NUMBER() OVER (
                          PARTITION BY m.entity_id
                          ORDER BY CASE p.list_status WHEN 'final' THEN 0 ELSE 1 END, p.record_id
                      ) AS rn
               FROM paper_records p
               JOIN entity_memberships m ON m.record_id = p.record_id
               WHERE p.venue = ?
           )
           SELECT * FROM venue_records WHERE rn = 1
           ORDER BY year DESC, record_id
           LIMIT ? OFFSET ?""",
        (venue, page_size, (page - 1) * page_size),
    ).fetchall()
    items = _dicts(rows)
    for item in items:
        item.pop("rn", None)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }
