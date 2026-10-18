"""Select the papers affected by the v1.5 rules (R13 VLM x learning paradigm, R14 3D anomaly detection).

Read-only with respect to labels: reads data/labels/areas-v1.4.csv and the catalog titles, then writes
a candidate CSV plus an id list for label_papers.py --ids/--area-only. No LLM calls happen here.

R13 candidates: a vision-language / multimodal large model is the research object AND a learning
paradigm is involved, but the paper is not already in multimodal. Papers that use a VLM as a backbone
for another task are expected to be kept by the relabeler; this list is a recall net, not a decision.
R14 candidates: anomaly detection with 3D input, currently in vision_3d_graphics or cv_understanding.
"""
import argparse
import csv
import re
import sqlite3
from collections import Counter
from pathlib import Path

VLM = re.compile(r"\bvlm\b|\bmllm\b|\blvlm\b|vision[- ]language model|multimodal large language|large vision[- ]language|multimodal llm")
PARADIGM = re.compile(r"domain adaptation|domain generalization|test[- ]time adapt|test[- ]time training|continual learning|incremental learning|few[- ]shot|semi[- ]supervised|meta[- ]learning")
ANOMALY = re.compile(r"anomaly|anomalies|defect detection|outlier")
THREE_D = re.compile(r"\b3d\b|point cloud|point-cloud|depth|voxel|\bmesh\b|rgb-?d")


def load_titles(catalog: Path, ids: set[str]) -> dict[str, str]:
    con = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
    titles = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT entity_id, canonical_title FROM paper_entities WHERE entity_id IN ({','.join('?' * len(chunk))})"
        titles.update({e: t or "" for e, t in con.execute(q, chunk)})
    con.close()
    return titles


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=Path("data/labels/areas-v1.4.csv"))
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--out", type=Path, default=Path("data/analyses/relabel_v1.5"))
    a = ap.parse_args()

    with a.labels.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    titles = load_titles(a.catalog, {r["entity_id"] for r in rows})

    found: dict[str, dict[str, str]] = {}
    for r in rows:
        text = f"{r['task']} {titles.get(r['entity_id'], '')}".lower()
        reasons = []
        if r["area"] != "multimodal" and VLM.search(text) and PARADIGM.search(text):
            reasons.append("R13")
        if r["area"] in {"vision_3d_graphics", "cv_understanding"} and ANOMALY.search(text) and THREE_D.search(text):
            reasons.append("R14")
        if reasons:
            found.setdefault(r["entity_id"], {
                "entity_id": r["entity_id"], "current_area": r["area"], "task": r["task"],
                "title": titles.get(r["entity_id"], ""), "rules": "+".join(reasons),
            })

    a.out.mkdir(parents=True, exist_ok=True)
    rows_out = sorted(found.values(), key=lambda x: (x["rules"], x["entity_id"]))
    with (a.out / "candidates.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["entity_id", "current_area", "task", "title", "rules"])
        w.writeheader()
        w.writerows(rows_out)
    (a.out / "candidate_ids.txt").write_text("\n".join(r["entity_id"] for r in rows_out) + "\n", encoding="utf-8")

    print(f"candidates: {len(rows_out)} -> {a.out / 'candidates.csv'}")
    for rules, n in Counter(r["rules"] for r in rows_out).most_common():
        print(f"  {rules}: {n}")
    for rule in ("R13", "R14"):
        dist = Counter(r["current_area"] for r in rows_out if rule in r["rules"].split("+"))
        print(f"  {rule} current areas: {dict(dist.most_common())}")


if __name__ == "__main__":
    main()
