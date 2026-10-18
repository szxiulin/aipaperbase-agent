"""Statistical capabilities: count / share / trend / compare / top_values / cooccurrence.

All counts are entity-level with one canonical year/venue per paper (paper_entities.canonical_record_id).
The denominator excludes the 251 non-paper records unless stated otherwise.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.capabilities.filters import BASE, group_expr, normalize_filters, where_clause


def _rows(connection: sqlite3.Connection, sql: str, params: list[Any]) -> list[dict]:
    return [dict(r) for r in connection.execute(sql, params).fetchall()]


def _total(connection: sqlite3.Connection, filters: dict) -> int:
    where, params = where_clause(filters)
    return connection.execute(f"SELECT COUNT(*) {BASE}{where}", params).fetchone()[0]


def _group_counts(connection: sqlite3.Connection, filters: dict, field: str) -> list[dict]:
    expr = group_expr(field)
    where, params = where_clause(filters)
    return _rows(connection, f"SELECT {expr} AS key, COUNT(*) AS count {BASE}{where} "
                            f"GROUP BY {expr} ORDER BY count DESC, key", params)


def _denominator_by(connection: sqlite3.Connection, field: str) -> dict:
    expr = group_expr(field)
    where, params = where_clause({})
    rows = _rows(connection, f"SELECT {expr} AS key, COUNT(*) AS count {BASE}{where} GROUP BY {expr}", params)
    return {r["key"]: r["count"] for r in rows}


def count_papers(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    group_by = args.get("group_by") or ""
    total = _total(connection, filters)
    data = {"total": total, "filters": filters}
    if group_by:
        data["group_by"] = group_by
        data["items"] = _group_counts(connection, filters, group_by)
    return data


def share(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    by = args.get("by") or "year"
    numerator = _group_counts(connection, filters, by)
    denominator = _denominator_by(connection, by)
    items = []
    for row in numerator:
        base = denominator.get(row["key"], 0)
        items.append({**row, "denominator": base, "share_rate": round(100 * row["count"] / base, 2) if base else 0})
    if by == "year":
        items.sort(key=lambda r: r["key"] or 0)
    return {"by": by, "numerator_total": sum(r["count"] for r in numerator),
            "denominator_total": sum(denominator.values()), "items": items}


def trend(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    rows = _group_counts(connection, filters, "year")
    denominator = _denominator_by(connection, "year")
    items = []
    for row in sorted(rows, key=lambda r: r["key"] or 0):
        base = denominator.get(row["key"], 0)
        items.append({**row, "total": base, "share_rate": round(100 * row["count"] / base, 2) if base else 0})
    growth = None
    if len(items) >= 2:
        first, last = items[0], items[-1]
        if first["count"]:
            growth = {"first_year": first["key"], "last_year": last["key"],
                      "first": first["count"], "last": last["count"],
                      "change_rate": round((last["count"] - first["count"]) / first["count"], 4),
                      "note": "yearly counts are entity first-canonical-record years; 2026 is a rolling list"}
    return {"items": items, "growth": growth,
            "note": "2026 is a rolling list; absolute counts are not comparable with full years"}


def compare(connection: sqlite3.Connection, args: dict) -> dict:
    groups = args.get("groups")
    if not isinstance(groups, list) or not 2 <= len(groups) <= 4:
        raise ValueError("groups must be an array of 2-4 {label, filters} objects")
    series = []
    for group in groups:
        if not isinstance(group, dict) or not group.get("label"):
            raise ValueError("each group needs a label")
        filters = normalize_filters(group.get("filters"))
        rows = _group_counts(connection, filters, "year")
        series.append({"label": str(group["label"]), "filters": filters,
                       "total": _total(connection, filters),
                       "series": [{"year": r["key"], "count": r["count"]} for r in sorted(rows, key=lambda r: r["key"] or 0)]})
    years = sorted({p["year"] for s in series for p in s["series"] if p["year"] is not None})
    table = []
    for year in years:
        row = {"year": year}
        for s in series:
            row[s["label"]] = next((p["count"] for p in s["series"] if p["year"] == year), 0)
        table.append(row)
    return {"series": series, "by_year": table}


def top_values(connection: sqlite3.Connection, args: dict) -> dict:
    field = args.get("field") or "venue"
    filters = normalize_filters(args.get("filters"))
    limit = min(max(int(args.get("limit") or 10), 1), 100)
    rows = _group_counts(connection, filters, field)[:limit]
    total = _total(connection, filters)
    for row in rows:
        row["share_rate"] = round(100 * row["count"] / total, 2) if total else 0
    return {"field": field, "total": total, "items": rows}


def cooccurrence(connection: sqlite3.Connection, args: dict) -> dict:
    field_a = args.get("field_a") or "area"
    field_b = args.get("field_b") or "direction"
    if field_a == field_b:
        raise ValueError("field_a and field_b must differ")
    expr_a, expr_b = group_expr(field_a), group_expr(field_b)
    filters = normalize_filters(args.get("filters"))
    where, params = where_clause(filters)
    limit = min(max(int(args.get("limit") or 50), 1), 500)
    rows = _rows(connection, f"SELECT {expr_a} AS a, {expr_b} AS b, COUNT(*) AS count {BASE}{where} "
                             f"GROUP BY {expr_a}, {expr_b} ORDER BY count DESC LIMIT ?", [*params, limit])
    return {"field_a": field_a, "field_b": field_b, "items": rows}
