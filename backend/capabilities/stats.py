"""Statistical capabilities: count / share / trend / compare / top_values / cooccurrence.

All counts are entity-level with one canonical year/venue per paper (paper_entities.canonical_record_id).
The denominator excludes non-paper entities unless stated otherwise.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.capabilities.filters import BASE, group_expr, normalize_filters, where_clause
from backend.catalog.metrics import coverage, LAST_COMPLETE_YEAR


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
    data = {"total": total, "filters": filters, "count_unit": "canonical_research_entities",
            "classification_coverage": coverage(connection),
            "coverage_scope": "whole corpus by year; not the filtered topic"}
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
            "denominator_total": sum(denominator.values()), "items": items,
            "classification_coverage": coverage(connection),
            "coverage_scope": "whole corpus by year; not the filtered topic"}


def trend(connection: sqlite3.Connection, args: dict) -> dict:
    filters = normalize_filters(args.get("filters"))
    rows = _group_counts(connection, filters, "year")
    denominator = _denominator_by(connection, "year")
    # Include zero-count years from the corpus, so absent endpoint hits cannot shorten the window.
    counts = {r["key"]: r["count"] for r in rows}
    years = sorted(y for y in denominator if not filters.get("years") or y in filters["years"])
    items = [{"key": y, "count": counts.get(y, 0), "total": denominator[y],
              "share_rate": round(100 * counts.get(y, 0) / denominator[y], 2) if denominator[y] else 0,
              "year_status": "complete_window" if y <= LAST_COMPLETE_YEAR else "collecting"} for y in years]
    selected = items if filters.get("years") else [r for r in items if r["key"] <= LAST_COMPLETE_YEAR]
    growth = None
    if len(selected) >= 2:
        first, last = selected[0], selected[-1]
        comparable = last["key"] <= LAST_COMPLETE_YEAR
        growth = {"first_year": first["key"], "last_year": last["key"],
                  "first": first["count"], "last": last["count"], "comparable": comparable,
                  "change_rate": round((last["count"]-first["count"])/first["count"], 4)
                                 if comparable and first["count"] else None,
                  "reason": "incomplete_year" if not comparable else "zero_baseline" if not first["count"] else "complete_window"}
    return {"items": items, "growth": growth, "classification_coverage": coverage(connection),
            "last_complete_year": LAST_COMPLETE_YEAR,
            "note": "Canonical research entities by canonical publication year. Growth excludes collecting years by default; classification coverage is for the entire corpus in each year, not the filtered topic. Complete window does not guarantee exhaustive collection."}


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
