"""Run the 30-question analysis benchmark against the capability registry.

Each question calls one capability and compares its output with independently written SQL over the
same canonical entity basis. This measures capability correctness, not NL->tool routing; an LLM
routing mode can reuse the same file.

Run: .venv/bin/python scripts/benchmark/run_analysis_benchmark.py
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

BASE = """WITH base AS (
  SELECT a.entity_id, p.year, p.venue, a.area_id, d.direction_id,
         e.canonical_title AS title, p.abstract AS abstract, p.authors AS authors
  FROM entity_area_assignments a
  LEFT JOIN entity_direction_assignments d ON d.entity_id = a.entity_id
  JOIN paper_entities e ON e.entity_id = a.entity_id
  JOIN paper_records p ON p.record_id = e.canonical_record_id
  WHERE a.is_non_paper = 0
) """


def norm(value):
    if isinstance(value, dict):
        return {str(k): norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def actual_value(result, check: str, question: dict):
    data = result.data
    if check == "scalar":
        return norm(data["total"])
    if check == "rows":
        return {str(r["key"]): int(r["count"]) for r in data["items"]}
    if check == "pairs":
        return {f"{r['a']}|{r['b']}": int(r["count"]) for r in data["items"]}
    if check == "compare_totals":
        return tuple(int(s["total"]) for s in data["series"])  # labels are user-facing, order is stable
    if check == "compare_year":
        row = next((r for r in data["by_year"] if r["year"] == question["year"]), {})
        return tuple(int(row.get(s["label"], 0)) for s in data["series"])
    if check == "field":
        return norm(data.get(question["field"]))
    if check == "taxonomy":
        return (int(data["area_count"]), int(data["direction_count"]))
    raise ValueError(check)


def expected_value(conn, question: dict):
    rows = conn.execute(BASE + question["sql"]).fetchall()
    check = question["check"]
    if check in ("scalar", "field"):
        return norm(rows[0][0])
    if check == "rows":
        return {str(r[0]): int(r[1]) for r in rows}
    if check == "pairs":
        return {f"{r[0]}|{r[1]}": int(r[2]) for r in rows}
    if check in ("compare_totals", "compare_year"):
        return tuple(int(r[1]) for r in rows)
    if check == "taxonomy":
        return (int(rows[0][0]), int(rows[0][1]))
    raise ValueError(check)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--questions", type=Path, default=Path("data/benchmarks/analysis_questions_v1.json"))
    ap.add_argument("--out", type=Path, default=Path("output/benchmarks/analysis_v1_scorecard.json"))
    a = ap.parse_args()
    spec = json.loads(a.questions.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{a.database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    results = []
    for q in spec["questions"]:
        result = REGISTRY.call(conn, q["capability"], q.get("arguments") or {})
        if not result.ok:
            results.append({"id": q["id"], "question": q["question"], "pass": False, "error": result.error})
            continue
        try:
            actual = actual_value(result, q["check"], q)
            expected = expected_value(conn, q)
            ok = actual == expected
        except Exception as exc:  # noqa: BLE001 - report, don't abort the suite
            results.append({"id": q["id"], "question": q["question"], "pass": False, "error": str(exc)})
            continue
        results.append({"id": q["id"], "question": q["question"], "capability": q["capability"],
                        "check": q["check"], "pass": ok, "actual": actual, "expected": expected})
    conn.close()
    passed = sum(1 for r in results if r["pass"])
    scorecard = {"version": spec["version"], "total": len(results), "passed": passed,
                 "score": round(passed / len(results), 4) if results else 0, "results": results}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(scorecard, ensure_ascii=False, indent=1) + "\n")
    print(f"analysis benchmark: {passed}/{len(results)} pass -> {a.out}")
    for r in results:
        if not r["pass"]:
            print(f"  FAIL {r['id']} {r['question'][:40]} actual={r.get('actual')} expected={r.get('expected')} {r.get('error','')}")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
