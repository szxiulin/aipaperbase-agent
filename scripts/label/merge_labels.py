#!/usr/bin/env python3
"""Overlay relabeled rows onto a base label CSV and write a new full label version.

Rows in OVERRIDE (status ok) replace the base row with the same entity_id; every other
base row is kept unchanged, so each row still records the model/prompt that produced it.

    .venv/bin/python scripts/label/merge_labels.py data/labels/areas-v1.2.csv \
        data/labels/relabel/trustworthy_v1.3_deepseek.csv --out data/labels/areas-v1.3.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("override", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    with args.base.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        base = list(reader)
    with args.override.open(encoding="utf-8", newline="") as f:
        override = {r["entity_id"]: r for r in csv.DictReader(f) if r["status"] == "ok"}
    known = {r["entity_id"] for r in base}
    unknown = set(override) - known
    if unknown:
        raise SystemExit(f"{len(unknown)} override ids are not in the base file")

    moved = Counter()
    rows = []
    for row in base:
        new = override.get(row["entity_id"])
        if new:
            moved[(row["area"], new["area"])] += 1
            row = {k: new.get(k, "") for k in fields}
        rows.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"base {len(base)} rows, replaced {len(override)} -> {args.out}")
    for (old, new), n in moved.most_common(15):
        print(f"  {n:6}  {old} -> {new}")


if __name__ == "__main__":
    main()
