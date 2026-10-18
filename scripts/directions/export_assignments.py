"""Export a draft's paper -> direction assignment as a versioned label CSV for the catalog import.

The draft keeps the pre-split parent ids (for example cv_lowlevel-d11), so this script reuses
build_config's SPLITS mapping to resolve the split children exactly as config/directions.json does.
It reads paper_direction_draft.csv, named_clusters.json, the split proposal and config/directions.json,
drops the excluded non-paper group, and validates that every direction exists and matches the paper's
area. The CSV is data, not configuration: it is the source the catalog import reads.
"""
import argparse
import csv
import importlib.util
import json
from collections import Counter
from pathlib import Path

FIELDS = ["entity_id", "area", "direction_id", "assigned_by", "cosine", "margin", "needs_review"]

_spec = importlib.util.spec_from_file_location("build_config", Path(__file__).with_name("build_config.py"))
build_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_config)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--proposal", type=Path, help="split_proposals dir (default: <draft>/split_proposals)")
    ap.add_argument("--config", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--non-paper-out", type=Path, default=Path("data/labels/non_paper_records.csv"))
    a = ap.parse_args()
    proposal = a.proposal or a.draft / "split_proposals"

    config = json.loads(a.config.read_text(encoding="utf-8"))
    directions = {d["direction_id"]: d["area"] for d in config["directions"]}
    clusters = {c["id"]: c for c in json.loads((a.draft / "named_clusters.json").read_text())}
    with (a.draft / "paper_direction_draft.csv").open(encoding="utf-8", newline="") as f:
        all_rows = list(csv.DictReader(f))
    rows = [r for r in all_rows if r["draft_direction"] != "excluded-non-paper"]

    group_of, task_of = {}, {}
    with (proposal / "split_proposal.csv").open() as f:
        for r in csv.DictReader(f):
            group_of[r["entity_id"]] = r["group_id"]
            task_of[r["entity_id"]] = r["task"]

    assigned = {}
    for cid in build_config.SPLITS:
        members = [r for r in rows if r["draft_direction"] == cid]
        for child in build_config.build_split(cid, clusters[cid], members, group_of, task_of):
            for r in child["_members"]:
                assigned[r["entity_id"]] = child["direction_id"]
    for r in rows:
        if r["draft_direction"] not in build_config.SPLITS:
            assigned[r["entity_id"]] = r["draft_direction"]

    out_rows, errors = [], []
    for r in rows:
        direction = assigned.get(r["entity_id"])
        area = directions.get(direction)
        if area is None:
            errors.append(f"{r['entity_id']}: unknown direction {direction}")
        elif area != r["area"]:
            errors.append(f"{r['entity_id']}: direction {direction} is in {area}, paper in {r['area']}")
        else:
            out_rows.append({"entity_id": r["entity_id"], "area": r["area"], "direction_id": direction,
                             "assigned_by": r.get("assigned_by", "kept"), "cosine": r.get("cosine", ""),
                             "margin": r.get("margin", ""), "needs_review": r.get("needs_review", "0")})
    if errors:
        raise SystemExit("\n".join(errors[:10]) + f"\n{len(errors)} errors")
    if len({r["entity_id"] for r in out_rows}) != len(out_rows):
        raise SystemExit("duplicate entity_id in assignment export")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {a.out}: {len(out_rows)} papers, {len(all_rows) - len(rows)} non-paper excluded, "
          f"{len(directions)} directions")
    print(f"  assigned_by: {dict(Counter(r['assigned_by'] for r in out_rows).most_common())}")

    non_paper = [{"entity_id": r["entity_id"], "record_kind": r.get("record_kind", "")}
                 for r in all_rows if r["draft_direction"] == "excluded-non-paper"]
    a.non_paper_out.parent.mkdir(parents=True, exist_ok=True)
    with a.non_paper_out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["entity_id", "record_kind"])
        w.writeheader()
        w.writerows(non_paper)
    print(f"wrote {a.non_paper_out}: {len(non_paper)} non-paper records")


if __name__ == "__main__":
    main()
