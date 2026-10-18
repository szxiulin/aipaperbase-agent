"""Recall papers for an ad-hoc topic (keyword + structured filters); no LLM calls.

Example:
  .venv/bin/python scripts/topics/recall_topic.py --query "agent image restoration" --limit 10
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.capabilities import REGISTRY  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True, help="topic keywords")
    ap.add_argument("--database", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--area", default="", help="area name or id (partial name match)")
    ap.add_argument("--direction", default="", help="direction name (partial match)")
    ap.add_argument("--venue", default="")
    ap.add_argument("--year", type=int)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    filters = {}
    if a.area:
        filters["area_names"] = [a.area]
    if a.direction:
        filters["direction_names"] = [a.direction]
    if a.venue:
        filters["venues"] = [a.venue]
    if a.year:
        filters["years"] = [a.year]
    conn = sqlite3.connect(f"file:{a.database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    result = REGISTRY.call(conn, "recall_topic", {"query": a.query, "filters": filters, "limit": a.limit})
    conn.close()
    if not result.ok:
        raise SystemExit(result.error)
    data = result.data
    if a.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=1))
        return
    print(f"topic: {data['query']}")
    print(f"candidates: {data['candidate_count']} ({data['method']}); {data['verification']['status']}")
    print("by year:", {r["key"]: r["count"] for r in data["stats"]["by_year"]})
    print("top venues:", {r["key"]: r["count"] for r in data["stats"]["by_venue"][:5]})
    for item in data["items"]:
        print(f"  {item['year']} {item['venue']:9s} {item['area_id']:18s} {item['title'][:80]}")
    print("\n注意：这是未验证的召回候选，不是命中数；精度需要抽样 LLM 逐篇判断（scripts/topics/verify_topic.py）。")


if __name__ == "__main__":
    main()
