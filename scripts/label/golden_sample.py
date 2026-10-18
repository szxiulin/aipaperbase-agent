"""Sample the 400-paper gold set, stratified by area, from the final label version.

Deterministic for a given seed. Excludes the 251 non-paper records (they have no direction) and
records whether each sampled paper has an abstract, because title-only rows are harder to judge.
Writes sample.csv and sample_report.json; no LLM calls.
"""
import argparse
import csv
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


def allocate(counts: dict[str, int], n: int, minimum: int) -> dict[str, int]:
    total = sum(counts.values())
    raw = {k: n * v / total for k, v in counts.items()}
    quota = {k: max(minimum, math.floor(raw[k])) for k in counts}
    order = sorted(counts, key=lambda k: (-(raw[k] - math.floor(raw[k])), k))
    while sum(quota.values()) < n:
        for k in order:
            if sum(quota.values()) >= n:
                break
            if quota[k] < counts[k]:
                quota[k] += 1
    while sum(quota.values()) > n:
        for k in sorted(counts, key=lambda k: (raw[k] - math.floor(raw[k]), k)):
            if sum(quota.values()) <= n:
                break
            if quota[k] > minimum:
                quota[k] -= 1
    return quota


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--areas", type=Path, default=Path("data/labels/areas-v1.5.csv"))
    ap.add_argument("--directions", type=Path, default=Path("data/labels/directions-v1.1.csv"))
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-per-area", type=int, default=5)
    ap.add_argument("--out", type=Path, default=Path("data/analyses/golden_20261018"))
    a = ap.parse_args()

    with a.areas.open(encoding="utf-8", newline="") as f:
        labels = {r["entity_id"]: r for r in csv.DictReader(f)}
    with a.directions.open(encoding="utf-8", newline="") as f:
        direction = {r["entity_id"]: r["direction_id"] for r in csv.DictReader(f)}
    ids = sorted(set(labels) & set(direction))  # drops the 251 non-paper records
    if len(ids) != len(direction):
        raise SystemExit(f"area/direction id mismatch: {len(ids)} vs {len(direction)}")

    by_area: dict[str, list[str]] = defaultdict(list)
    for eid in ids:
        by_area[labels[eid]["area"]].append(eid)
    quota = allocate({k: len(v) for k, v in by_area.items()}, a.n, a.min_per_area)

    con = sqlite3.connect(f"file:{a.catalog}?mode=ro", uri=True)
    has_abstract = {}
    for eid in ids:
        row = con.execute("""SELECT r.abstract FROM paper_entities e
                             JOIN paper_records r ON r.record_id = e.canonical_record_id
                             WHERE e.entity_id = ?""", (eid,)).fetchone()
        has_abstract[eid] = bool((row and row[0] or "").strip())
    con.close()

    picked = []
    for area in sorted(by_area):
        pool = by_area[area]
        rng = random.Random(f"{a.seed}:{area}")
        picked += [(eid, area) for eid in rng.sample(pool, quota[area])]
    rng = random.Random(f"{a.seed}:order")
    rng.shuffle(picked)

    a.out.mkdir(parents=True, exist_ok=True)
    with (a.out / "sample.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sample_order", "entity_id", "area", "direction_id", "has_abstract"])
        w.writeheader()
        for i, (eid, area) in enumerate(picked, 1):
            w.writerow({"sample_order": i, "entity_id": eid, "area": area,
                        "direction_id": direction[eid], "has_abstract": int(has_abstract[eid])})
    report = {
        "seed": a.seed, "n": len(picked), "population": len(ids),
        "excluded_non_paper": len(labels) - len(ids),
        "has_abstract": sum(has_abstract[e] for e, _ in picked),
        "missing_abstract": sum(not has_abstract[e] for e, _ in picked),
        "quotas": {k: v for k, v in sorted(quota.items())},
        "strata": {k: len(v) for k, v in sorted(by_area.items())},
    }
    (a.out / "sample_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"sampled {len(picked)} / {len(ids)} papers; missing abstract {report['missing_abstract']}")
    print("quotas:", dict(sorted(quota.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
