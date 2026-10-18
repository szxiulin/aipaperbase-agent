"""Build a direction draft from an earlier one after primary-area labels changed (e.g. v1.3 -> v1.4).

Only papers whose area changed move; every other assignment is kept. A moved paper goes to:
1. the direction that already holds the same task phrase in its new area (majority if several), so an
   (area, task) node stays in one direction as in cluster_tasks.py; otherwise
2. the nearest direction center in the new area, using the draft's feature
   normalize(0.75 * task vector + 0.25 * paper vector); centers are computed from unmoved members.
Cosine/margin and the needs_review flag (cosine < 0.55 or margin < 0.02) are recomputed for moved papers.
Direction ids are kept; directions left empty are dropped. Directions with a large inflow are flagged for
review, and automatic placeholder names are regenerated from the new top tasks.
No task string changes here, so no new embeddings are needed.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

COS_MIN, MARGIN_MIN = 0.55, 0.02
INFLOW_MIN, INFLOW_SHARE = 20, 0.15  # flag directions whose inflow may have changed what they are about


def norm(x):
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", type=Path, required=True, help="source draft dir (paper_direction_draft.csv, named_clusters.json)")
    ap.add_argument("--vocab", type=Path, required=True, help="task_vocabulary.jsonl matching the task vectors")
    ap.add_argument("--task-vectors", type=Path, required=True)
    ap.add_argument("--paper-index", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True, help="new label CSV")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)

    vix = {json.loads(l)["task"]: i for i, l in enumerate(a.vocab.open())}
    tvec = np.load(a.task_vectors / "tasks.1024.f16.npy", mmap_mode="r")
    ids = [json.loads(l) for l in (a.paper_index / "ids.jsonl").open()]
    pix = {(r["entity_id"] if isinstance(r, dict) else r): i for i, r in enumerate(ids)}
    pvec = np.load(a.paper_index / "papers.1024.f16.npy", mmap_mode="r")
    rows = list(csv.DictReader((a.draft / "paper_direction_draft.csv").open()))
    src_dir = {r["entity_id"]: r["draft_direction"] for r in rows}  # assignment before this update
    clusters = {c["id"]: c for c in json.loads((a.draft / "named_clusters.json").read_text())}
    labels = {r["entity_id"]: r for r in csv.DictReader(a.labels.open())}

    def feature(r):
        t = np.asarray(tvec[vix[r["task"]]], dtype=np.float32)
        p = np.asarray(pvec[pix[r["entity_id"]]], dtype=np.float32)
        return norm(0.75 * t + 0.25 * p)

    moved = [r for r in rows if r["draft_direction"] != "excluded-non-paper" and labels[r["entity_id"]]["area"] != r["area"]]
    moved_ids = {r["entity_id"] for r in moved}
    stay = defaultdict(list)
    for r in rows:
        if r["draft_direction"] != "excluded-non-paper" and r["entity_id"] not in moved_ids:
            stay[r["draft_direction"]].append(r)
    centers = {d: norm(np.mean([feature(r) for r in ms], axis=0)) for d, ms in stay.items()}
    area_dirs = defaultdict(list)
    for d in centers:
        area_dirs[clusters[d]["area"]].append(d)
    task_dir = defaultdict(Counter)
    for d, ms in stay.items():
        for r in ms:
            task_dir[(r["area"], r["task"])][d] += 1

    how = Counter()
    for r in moved:
        new_area = labels[r["entity_id"]]["area"]
        cand = area_dirs[new_area]
        f = feature(r)
        scores = {d: float(f @ centers[d]) for d in cand}
        if task_dir.get((new_area, r["task"])):
            target, how_ = task_dir[(new_area, r["task"])].most_common(1)[0][0], "task_match"
        else:
            target, how_ = max(scores, key=scores.get), "nearest_center"
        others = [s for d, s in scores.items() if d != target]
        best = scores[target]
        margin = best - (max(others) if others else -1.0)
        r.update({"area": new_area, "draft_direction": target, "cosine": round(best, 6), "margin": round(margin, 6),
                  "needs_review": int(best < COS_MIN or margin < MARGIN_MIN), "area_confidence": labels[r["entity_id"]]["area_confidence"],
                  "area_changed": 1, "assigned_by": how_})
        how[how_] += 1
    for r in rows:
        r.setdefault("area_changed", 0)
        r.setdefault("assigned_by", "kept")

    final = defaultdict(list)
    for r in rows:
        final[r["draft_direction"]].append(r)
    moved_in = Counter(r["draft_direction"] for r in moved)
    moved_out = Counter(src_dir[e] for e in moved_ids)

    out = []
    for cid, c in clusters.items():
        ms = final.get(cid, [])
        if not ms:
            continue
        c = dict(c)
        if cid != "excluded-non-paper":
            kept_ids = {r["entity_id"] for r in ms}
            c["representatives"] = [p for p in c["representatives"] if p["entity_id"] in kept_ids]
            c["boundary_papers"] = [p for p in c["boundary_papers"] if p["entity_id"] in kept_ids]
            tc = Counter(r["task"] for r in ms)
            c["paper_count"] = len(ms)
            c["task_count"] = len(tc)
            c["top_tasks"] = [{"task": t, "count": n} for t, n in sorted(tc.items(), key=lambda p: (-p[1], p[0]))[:15]]
            c["needs_review_count"] = sum(int(r["needs_review"]) for r in ms)
            tv = np.asarray(tvec[[vix[r["task"]] for r in ms]], dtype=np.float32)
            c["task_cohesion"] = round(float((tv @ norm(tv.mean(axis=0))).mean()), 4)
            if moved_in[cid] or moved_out[cid]:
                c["boundary_note"] = c["boundary_note"] + f"（v3 按 areas-v1.4 迁入 {moved_in[cid]} 篇、迁出 {moved_out[cid]} 篇。）"
            if moved_in[cid] >= max(INFLOW_MIN, INFLOW_SHARE * len(ms)):
                c["priority_review"] = True
                c["boundary_note"] += "迁入量较大，名称和范围可能已不贴合，建议复核或拆分。"
            if str(c.get("naming_provenance", "")).startswith("automatic placeholder"):
                prefix = "待分流：" if cid.endswith("-r") else "待命名："
                c["name_zh"] = prefix + " / ".join(t["task"] for t in c["top_tasks"][:3])
                c["name_en"] = c["suggested_name_en"] = c["top_tasks"][0]["task"]
        out.append(c)
    dropped = sorted(set(clusters) - {c["id"] for c in out})
    assert sum(c["paper_count"] for c in out) == len(rows)
    assert all(r["draft_direction"] == "excluded-non-paper" or clusters[r["draft_direction"]]["area"] == labels[r["entity_id"]]["area"] for r in rows)

    fields = list(rows[0].keys())
    with (a.out / "paper_direction_draft.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (a.out / "named_clusters.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    rep = json.loads((a.draft / "clustering_report.json").read_text())
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    rep.update({"phase": "draft_v3", "directions": len(out),
                "area_update": {"labels": a.labels.name, "labels_sha256": sha(a.labels), "moved_papers": len(moved),
                                "assigned_by": dict(how), "dropped_empty_directions": dropped,
                                "moved_needs_review": sum(int(r["needs_review"]) for r in moved)}})
    rep["source"] = dict(rep["source"], labels_sha256=sha(a.labels))
    (a.out / "clustering_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=int))
    legacy = json.loads((a.draft / "legacy_run_ids.json").read_text()) if (a.draft / "legacy_run_ids.json").exists() else []
    legacy.append(sha(a.draft / "named_clusters.json"))
    src_rows = sorted((r["entity_id"], src_dir[r["entity_id"]]) for r in rows)
    legacy.append(hashlib.sha256("\n".join(e + "," + d for e, d in src_rows).encode()).hexdigest())
    (a.out / "legacy_run_ids.json").write_text(json.dumps(legacy))
    print(json.dumps({"directions": len(out), "moved": len(moved), "assigned_by": how, "dropped": dropped,
                      "moved_needs_review": rep["area_update"]["moved_needs_review"]}, ensure_ascii=False, default=int))


if __name__ == "__main__":
    main()
