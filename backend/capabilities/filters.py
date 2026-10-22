"""Shared SQL filter builder for capabilities (area/direction/venue/year/keyword)."""
from __future__ import annotations

from typing import Any

MAX_LIST = 50


def _list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    if len(value) > MAX_LIST:
        raise ValueError(f"{name} supports at most {MAX_LIST} values")
    return [str(v) for v in value if str(v).strip()]


def normalize_filters(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("filters must be an object")
    allowed = {"area_ids", "area_names", "direction_ids", "direction_names", "venues", "years", "query", "pending_direction"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown filter keys: {sorted(unknown)}")
    if "pending_direction" in raw and type(raw["pending_direction"]) is not bool:
        raise ValueError("pending_direction must be a boolean")
    out = {"area_ids": _list(raw.get("area_ids"), "area_ids"),
           "area_names": _list(raw.get("area_names"), "area_names"),
           "direction_ids": _list(raw.get("direction_ids"), "direction_ids"),
           "direction_names": _list(raw.get("direction_names"), "direction_names"),
           "venues": _list(raw.get("venues"), "venues"),
           "years": [int(y) for y in _list(raw.get("years"), "years")],
           "query": str(raw.get("query") or "").strip()[:200],
           "pending_direction": raw.get("pending_direction", False)}
    return out


def where_clause(filters: dict) -> tuple[str, list[Any]]:
    """Return (WHERE ..., params) for the canonical entity-level base join (aliases a/d/e/p)."""
    clauses = ["a.is_non_paper = 0"]
    params: list[Any] = []
    if filters.get("pending_direction"):
        clauses.extend(["a.area_id <> 'pending_classification'", "d.entity_id IS NULL"])
    if filters.get("area_ids"):
        clauses.append(f"a.area_id IN ({','.join('?' * len(filters['area_ids']))})")
        params.extend(filters["area_ids"])
    if filters.get("area_names"):
        parts = " OR ".join(["name_zh LIKE ? OR name_en LIKE ?"] * len(filters["area_names"]))
        clauses.append(f"a.area_id IN (SELECT area_id FROM area_definitions WHERE {parts})")
        for name in filters["area_names"]:
            params.extend([f"%{name}%", f"%{name}%"])
    if filters.get("direction_ids"):
        clauses.append(f"d.direction_id IN ({','.join('?' * len(filters['direction_ids']))})")
        params.extend(filters["direction_ids"])
    if filters.get("direction_names"):
        parts = " OR ".join(["name_zh LIKE ? OR name_en LIKE ?"] * len(filters["direction_names"]))
        clauses.append(f"d.direction_id IN (SELECT direction_id FROM direction_definitions WHERE {parts})")
        for name in filters["direction_names"]:
            params.extend([f"%{name}%", f"%{name}%"])
    if filters.get("venues"):
        marks = ",".join("?" * len(filters["venues"]))
        clauses.append(f"p.venue IN ({marks})")  # canonical venue, consistent with the benchmark basis
        params.extend(filters["venues"])
    if filters.get("years"):
        marks = ",".join("?" * len(filters["years"]))
        clauses.append("p.year IN (" + marks + ")")
        params.extend(filters["years"])
    if filters.get("query"):
        term = f"%{filters['query']}%"
        clauses.append("(e.canonical_title LIKE ? OR p.abstract LIKE ? OR p.authors LIKE ?)")
        params.extend([term, term, term])
    return " WHERE " + " AND ".join(clauses), params


BASE = """ FROM entity_area_assignments a
           LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
           JOIN paper_entities e ON e.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = e.canonical_record_id"""

GROUP_EXPR = {"year": "p.year", "venue": "p.venue", "area": "a.area_id", "direction": "d.direction_id"}


def group_expr(field: str) -> str:
    if field not in GROUP_EXPR:
        raise ValueError(f"unsupported group_by: {field}; use {sorted(GROUP_EXPR)}")
    return GROUP_EXPR[field]
