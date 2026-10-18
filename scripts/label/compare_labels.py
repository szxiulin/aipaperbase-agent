#!/usr/bin/env python3
"""Compare two label CSVs (same schema as label_papers.py output) on their shared papers.

Reports coverage, primary-area agreement, Cohen's kappa, secondary-area rate and the
most frequent disagreements, and optionally writes the disagreeing papers to a CSV for
review.

    .venv/bin/python scripts/label/compare_labels.py \
        data/labels/pilot/areas-v1.1_pilot500.csv data/labels/pilot/codex_pilot500.csv \
        --disagreements data/analyses/codex_vs_deepseek.csv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path("data/database/catalog.sqlite")


def read(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return {r["entity_id"]: r for r in csv.DictReader(f) if r.get("status") == "ok"}


def cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    n = len(pairs)
    if not n:
        return 0.0
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in left) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--disagreements", type=Path, help="write disagreeing papers to this CSV")
    args = ap.parse_args()

    a, b = read(args.a), read(args.b)
    shared = sorted(set(a) & set(b))
    print(f"A: {args.a} ({len(a)} ok)\nB: {args.b} ({len(b)} ok)\nshared: {len(shared)}")
    if not shared:
        return
    pairs = [(a[k]["area"], b[k]["area"]) for k in shared]
    agree = sum(x == y for x, y in pairs)
    # Count as "soft" agreement when one side's primary is the other's secondary.
    soft = sum(1 for k in shared if a[k]["area"] == b[k]["area"]
               or a[k]["area"] == b[k]["area_secondary"] or b[k]["area"] == a[k]["area_secondary"])
    print(f"primary agreement: {agree}/{len(shared)} = {agree / len(shared):.1%}")
    print(f"agreement incl. secondary: {soft}/{len(shared)} = {soft / len(shared):.1%}")
    print(f"Cohen's kappa: {cohen_kappa(pairs):.3f}")
    for name, rows in (("A", a), ("B", b)):
        sec = sum(1 for k in shared if rows[k]["area_secondary"])
        print(f"secondary rate {name}: {sec / len(shared):.1%}")
    print("\nmost frequent disagreements (A -> B):")
    for (x, y), n in Counter(p for p in pairs if p[0] != p[1]).most_common(12):
        print(f"  {n:3}  {x} -> {y}")

    if args.disagreements:
        titles = {}
        if DB.exists():
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            ids = [k for k in shared if a[k]["area"] != b[k]["area"]]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                titles.update(con.execute(
                    f"SELECT entity_id, canonical_title FROM paper_entities WHERE entity_id IN ({q})", chunk))
            con.close()
        args.disagreements.parent.mkdir(parents=True, exist_ok=True)
        with args.disagreements.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["entity_id", "title", "a_area", "a_secondary", "a_task", "b_area", "b_secondary", "b_task"])
            for k in shared:
                if a[k]["area"] != b[k]["area"]:
                    w.writerow([k, titles.get(k, ""), a[k]["area"], a[k]["area_secondary"], a[k]["task"],
                                b[k]["area"], b[k]["area_secondary"], b[k]["task"]])
        print(f"\nwrote disagreements to {args.disagreements}")


if __name__ == "__main__":
    main()
