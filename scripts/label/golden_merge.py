"""Merge the author's exported gold review into data/labels/golden_v1.csv.

Requires all sampled papers to be reviewed. Validates the sample hash, ids, areas and that every
direction belongs to the chosen area. The gold columns are the author's judgement; prelabel and
system labels are kept only for comparison.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=Path, default=Path("data/analyses/golden_20261018/sample.csv"))
    ap.add_argument("--prelabel", type=Path, default=Path("data/analyses/golden_20261018/prelabel.csv"))
    ap.add_argument("--review", type=Path, required=True, help="JSON exported by the review page")
    ap.add_argument("--areas", type=Path, default=Path("config/areas.json"))
    ap.add_argument("--directions", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--out", type=Path, default=Path("data/labels/golden_v1.csv"))
    ap.add_argument("--exclude", type=Path, help="CSV with entity_id rows to drop from the gold set")
    a = ap.parse_args()

    with a.sample.open(encoding="utf-8", newline="") as f:
        sample = list(csv.DictReader(f))
    with a.prelabel.open(encoding="utf-8", newline="") as f:
        pre = {r["entity_id"]: r for r in csv.DictReader(f)}
    config = json.loads(a.directions.read_text(encoding="utf-8"))
    area_of = {d["direction_id"]: d["area"] for d in config["directions"]}
    area_ids = {x["id"] for x in json.loads(a.areas.read_text(encoding="utf-8"))["areas"]}
    review = json.loads(a.review.read_text(encoding="utf-8"))
    expected_sha = hashlib.sha256("\n".join(sorted(r["entity_id"] for r in sample)).encode()).hexdigest()
    if review.get("schema") != "golden-review-v1" or review.get("sample_sha") != expected_sha:
        raise SystemExit("review file does not belong to this sample (schema/sample_sha mismatch)")
    reviews = review.get("reviews") or {}
    excluded = set()
    if a.exclude and a.exclude.exists():
        with a.exclude.open(encoding="utf-8", newline="") as f:
            excluded = {r["entity_id"] for r in csv.DictReader(f)}
    missing = [r["entity_id"] for r in sample if r["entity_id"] not in reviews and r["entity_id"] not in excluded]
    extra = set(reviews) - {r["entity_id"] for r in sample}
    if missing or extra:
        raise SystemExit(f"review ids mismatch: {len(missing)} missing, {len(extra)} extra")

    rows, errors = [], []
    for r in sample:
        eid = r["entity_id"]
        if eid in excluded:
            continue
        v = reviews[eid]
        gold_area, gold_dir = v.get("area", ""), v.get("direction", "")
        if gold_area not in area_ids:
            errors.append(f"{eid}: invalid area {gold_area}")
        elif area_of.get(gold_dir) != gold_area:
            errors.append(f"{eid}: direction {gold_dir} is not in {gold_area}")
        else:
            p = pre.get(eid, {})
            if gold_area == r["area"] and gold_dir == r["direction_id"]:
                verdict = "agree_system"
            elif gold_area == p.get("pred_area") and gold_dir == p.get("pred_direction"):
                verdict = "agree_prelabel"
            else:
                verdict = "changed"
            rows.append({"entity_id": eid, "sample_order": r["sample_order"],
                         "system_area": r["area"], "system_direction": r["direction_id"],
                         "pred_area": p.get("pred_area", ""), "pred_direction": p.get("pred_direction", ""),
                         "gold_area": gold_area, "gold_direction": gold_dir, "verdict": verdict,
                         "notes": (v.get("notes") or "").replace("\n", " ")})
    if errors:
        raise SystemExit("\n".join(errors[:10]) + f"\n{len(errors)} errors")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"wrote {a.out}: {len(rows)} gold rows ({len(excluded)} excluded); {counts}")


if __name__ == "__main__":
    main()
