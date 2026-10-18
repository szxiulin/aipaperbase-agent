"""Build draft v2 from draft v1: set aside non-paper records and split low-cohesion (catch-all) directions.

Why: in v1 the number of directions per area was fixed by a size formula, so tasks far from every
center collected into residual clusters that still received topical-sounding names
(e.g. "特征选择与自动特征工程" holding WiFi sensing and POI papers). Moving those papers to other
directions does not help (a prototype moved 1 of ~9.7k), so the residual itself is re-clustered.

Steps
1. Records flagged by flag_records.py go to the pseudo-direction `excluded-non-paper`; they keep
   their row so totals still reconcile, but are not counted in any research direction.
2. Task cohesion = paper-weighted mean cosine between each member's task vector and the
   direction's task centroid. Directions below COHESION_MIN (other-* excluded) are split.
3. Inside each such direction, (area, task) nodes are re-clustered with the v1 feature
   (0.75 task + 0.25 paper context) and sqrt(paper count) weights, weighted spherical k-means,
   k-means++ init, best of SEEDS restarts. k is the smallest value whose sub-clusters reach
   TARGET_COHESION, capped at MAX_K. Sub-clusters smaller than MIN_SUB_PAPERS or below KEEP_COHESION go to a
   `<parent>-r` residual that is explicitly named 待分流.
4. Directions whose membership only lost non-paper records keep their v1 id (so v1 review
   opinions still apply); split parents disappear and their children get new ids.

Sub-direction names are placeholders built from top tasks; nothing here is a user decision.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

COHESION_MIN = 0.80
TARGET_COHESION = 0.84
MAX_K = 10
MIN_SUB_PAPERS = 15
KEEP_COHESION = 0.83  # a kept sub-direction must be clearly tighter than the split threshold
SEEDS = (42, 43, 44, 45, 46)


def norm(x):
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def spherical_kmeans(x, w, k, seed, iters=50):
    rng = np.random.default_rng(seed)
    centers = [x[rng.choice(len(x), p=w / w.sum())]]
    for _ in range(1, k):  # k-means++ on cosine distance
        d = 1 - np.max(x @ np.stack(centers).T, axis=1)
        p = w * np.maximum(d, 0) ** 2
        centers.append(x[rng.choice(len(x), p=p / p.sum())] if p.sum() > 0 else x[rng.integers(len(x))])
    c = np.stack(centers)
    for _ in range(iters):
        a = np.argmax(x @ c.T, axis=1)
        s = np.zeros_like(c)
        np.add.at(s, a, x * w[:, None])
        good = np.linalg.norm(s, axis=1) > 0
        new = c.copy()
        new[good] = norm(s[good])
        if np.allclose(new, c):
            break
        c = new
    a = np.argmax(x @ c.T, axis=1)
    objective = float(np.sum(w * np.max(x @ c.T, axis=1)))
    return c, a, objective


def ari(a, b):
    """Adjusted Rand index (no sklearn dependency)."""
    from math import comb
    n = len(a)
    table = Counter(zip(a, b))
    sum_ij = sum(comb(v, 2) for v in table.values())
    sum_a = sum(comb(v, 2) for v in Counter(a).values())
    sum_b = sum(comb(v, 2) for v in Counter(b).values())
    expected = sum_a * sum_b / comb(n, 2) if n > 1 else 0
    maximum = (sum_a + sum_b) / 2
    return 1.0 if maximum == expected else (sum_ij - expected) / (maximum - expected)


def cohesion(task_rows, tvec):
    if len(task_rows) == 0:
        return 0.0
    c = norm(tvec[task_rows].mean(axis=0))
    return float((tvec[task_rows] @ c).mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v1", type=Path, required=True, help="v1 run dir (task_vocabulary.jsonl, draft/)")
    ap.add_argument("--task-vectors", type=Path, required=True)
    ap.add_argument("--paper-index", type=Path, required=True)
    ap.add_argument("--flags", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--out", type=Path, required=True, help="v2 draft dir (created; must not exist)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)

    d1 = a.v1 / "draft"
    vocab = [json.loads(l) for l in (a.v1 / "task_vocabulary.jsonl").open()]
    vix = {v["task"]: i for i, v in enumerate(vocab)}
    tvec = np.asarray(np.load(a.task_vectors / "tasks.1024.f16.npy", mmap_mode="r"), dtype=np.float32)
    ids = [json.loads(l) for l in (a.paper_index / "ids.jsonl").open()]
    pix = {(r["entity_id"] if isinstance(r, dict) else r): i for i, r in enumerate(ids)}
    pvec = np.load(a.paper_index / "papers.1024.f16.npy", mmap_mode="r")
    rows = list(csv.DictReader((d1 / "paper_direction_draft.csv").open()))
    v1 = {c["id"]: c for c in json.loads((d1 / "named_clusters.json").read_text())}
    flags = {r["entity_id"]: r["record_kind"] for r in csv.DictReader(a.flags.open())}
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    papers = {e: {"title": t, "abstract": ab or ""} for e, t, ab in con.execute(
        "SELECT e.entity_id, e.canonical_title, r.abstract FROM paper_entities e "
        "JOIN paper_records r ON r.record_id = e.canonical_record_id")}

    members = defaultdict(list)
    for r in rows:
        r["record_kind"] = flags.get(r["entity_id"], "")
        r["parent_direction"] = r["draft_direction"]
        if r["record_kind"]:
            r["draft_direction"] = "excluded-non-paper"
        else:
            members[r["draft_direction"]].append(r)

    coh = {d: cohesion([vix[r["task"]] for r in ms], tvec) for d, ms in members.items()}
    low = sorted(d for d in members if coh[d] < COHESION_MIN and not d.startswith("other"))

    report = {"cohesion_min": COHESION_MIN, "target_cohesion": TARGET_COHESION, "max_k": MAX_K,
              "min_sub_papers": MIN_SUB_PAPERS, "keep_cohesion": KEEP_COHESION, "seeds": list(SEEDS), "excluded_records": Counter(flags.values()),
              "split": {}}
    new_clusters = {}  # id -> dict(area, parent, rows, center)
    for d in low:
        ms = members[d]
        groups = defaultdict(list)
        for r in ms:
            groups[r["task"]].append(r)
        tasks = sorted(groups)
        w = np.sqrt(np.array([len(groups[t]) for t in tasks], dtype=np.float32))
        ctx = np.stack([norm(np.asarray(pvec[[pix[r["entity_id"]] for r in groups[t]]], dtype=np.float32).mean(axis=0))
                        for t in tasks])
        x = norm(0.75 * tvec[[vix[t] for t in tasks]] + 0.25 * ctx)
        chosen = None
        for k in range(2, min(MAX_K, len(tasks) // 5) + 1):
            runs = [spherical_kmeans(x, w, k, s) for s in SEEDS]
            best = max(runs, key=lambda r: r[2])
            subs = [[vix[t] for i, t in enumerate(tasks) if best[1][i] == z for _ in groups[t]] for z in range(k)]
            score = sum(len(s) * cohesion(s, tvec) for s in subs if s) / len(ms)
            chosen = (k, best, score, [ari(list(best[1]), list(r[1])) for r in runs if r is not best])
            if score >= TARGET_COHESION:
                break
        k, (centers, assign, _), score, aris = chosen
        kept, residual = [], []
        for z in range(k):
            sub_rows = [r for i, t in enumerate(tasks) if assign[i] == z for r in groups[t]]
            c_sub = cohesion([vix[r["task"]] for r in sub_rows], tvec) if sub_rows else 0
            (kept if len(sub_rows) >= MIN_SUB_PAPERS and c_sub >= KEEP_COHESION else residual).append((z, sub_rows, c_sub))
        kept.sort(key=lambda s: -len(s[1]))
        for n, (z, sub_rows, c_sub) in enumerate(kept, 1):
            new_clusters[f"{d}-s{n:02}"] = {"parent": d, "rows": sub_rows, "center": centers[z], "cohesion": c_sub}
        res_rows = [r for _, sr, _ in residual for r in sr]
        if res_rows:
            new_clusters[f"{d}-r"] = {"parent": d, "rows": res_rows, "center": norm(np.mean([centers[z] for z, _, _ in residual], axis=0)),
                                      "cohesion": cohesion([vix[r["task"]] for r in res_rows], tvec), "residual": True}
        report["split"][d] = {"papers": len(ms), "cohesion_before": round(coh[d], 4), "k": k,
                              "weighted_cohesion_after": round(score, 4), "kept_subdirections": len(kept),
                              "residual_papers": len(res_rows), "seed_ari_vs_best": [round(v, 3) for v in aris]}
        for name, info in new_clusters.items():
            if info["parent"] == d:
                for r in info["rows"]:
                    r["draft_direction"] = name

    # Assemble review clusters in the v1 schema.
    final = defaultdict(list)
    for r in rows:
        final[r["draft_direction"]].append(r)
    area_names = {c["area"]: c["area_name"] for c in v1.values()}

    def paper_entry(r, cos=None, margin=None):
        p = papers[r["entity_id"]]
        return {"entity_id": r["entity_id"], "task": r["task"], "title": p["title"], "abstract": p["abstract"],
                "cosine": float(r["cosine"]) if cos is None else cos, "margin": float(r["margin"]) if margin is None else margin,
                "needs_review": bool(int(r["needs_review"]))}

    def representatives(ms, center):
        scored = sorted(ms, key=lambda r: (-float(tvec[vix[r["task"]]] @ center), r["entity_id"]))
        chosen, seen = [], set()
        for need_abstract in (True, False):
            for r in scored:
                if r["task"] in seen or (need_abstract and not papers[r["entity_id"]]["abstract"].strip()):
                    continue
                chosen.append(r)
                seen.add(r["task"])
                if len(chosen) >= (3 if need_abstract else 4):
                    break
        return chosen

    out = []
    centers_by_id = {}
    for cid, info in new_clusters.items():
        centers_by_id[cid] = norm(tvec[[vix[r["task"]] for r in info["rows"]]].mean(axis=0))
    for cid, ms in members.items():
        if cid not in low:
            centers_by_id[cid] = norm(tvec[[vix[r["task"]] for r in ms]].mean(axis=0))

    for cid in sorted(final):
        ms = final[cid]
        if cid == "excluded-non-paper":
            kinds = Counter(r["record_kind"] for r in ms)
            out.append({"id": cid, "area": "excluded", "area_name": "非论文记录（不计入方向）", "name_zh": "非论文记录（已从方向统计中剔除）",
                        "name_en": "non-paper records", "suggested_name_en": "non-paper records", "paper_count": len(ms),
                        "task_count": len({r["task"] for r in ms}), "top_tasks": [{"task": k, "count": n} for k, n in kinds.most_common()],
                        "representatives": [paper_entry(r) for r in ms[:4]], "boundary_papers": [], "nearest_directions": [],
                        "mean_cosine": 0.0, "needs_review_count": 0, "title_only_count": sum(not papers[r["entity_id"]]["abstract"].strip() for r in ms),
                        "definition": "期刊/会议的编委、目录、封面、委员会与审稿人名单、奖项与声明、编者按，以及勘误和撤稿通知。按标题规则识别（scripts/directions/flag_records.py），已逐条人工核对。",
                        "boundary_note": "只是不计入研究方向统计，原始记录与领域标签均未删除或修改。", "status": "pending_review",
                        "priority_review": False, "naming_provenance": "rule-based record flags; user approval pending"})
            continue
        tc = Counter(r["task"] for r in ms)
        if cid in v1:
            base = dict(v1[cid])
            kept_ids = {r["entity_id"] for r in ms}
            base["representatives"] = [p for p in base["representatives"] if p["entity_id"] in kept_ids]
            base["boundary_papers"] = [p for p in base["boundary_papers"] if p["entity_id"] in kept_ids]
            removed = v1[cid]["paper_count"] - len(ms)
            if removed:
                base["boundary_note"] = base["boundary_note"] + f"（v2 已剔除 {removed} 条非论文记录。）"
            if cid == "other-d02":
                base["name_zh"] = "其他·待分流（原总目簇，已剔除非论文记录）"
                base["definition"] = "剔除编委、目录等非论文记录后剩下的真实文章：立场文章、领域外研究及少数被上游标为 other 的方法论文，需逐条回到上游领域标签复核。"
        else:
            info = new_clusters[cid]
            parent = v1[info["parent"]]
            top = [t for t, _ in tc.most_common(3)]
            residual = info.get("residual", False)
            reps = representatives(ms, centers_by_id[cid])
            base = {"id": cid, "area": parent["area"], "area_name": parent["area_name"],
                    "name_zh": ("待分流：" if residual else "待命名：") + " / ".join(top),
                    "name_en": top[0], "suggested_name_en": top[0], "name_en_role": "top task",
                    "definition": (f"从「{parent['name_zh']}」拆出的剩余部分：子簇过小或内聚度不足，需逐条审核。" if residual
                                   else f"从「{parent['name_zh']}」（内聚度 {coh[info['parent']]:.3f}，判定为兜底簇）中细分出的子方向；名称为占位，按常见任务生成。"),
                    "boundary_note": f"任务内聚度 {info['cohesion']:.3f}；父方向 {info['parent']}。名称尚未人工确认。",
                    "representatives": [paper_entry(r, float(tvec[vix[r['task']]] @ centers_by_id[cid])) for r in reps],
                    "boundary_papers": [paper_entry(r, float(tvec[vix[r['task']]] @ centers_by_id[cid]))
                                        for r in sorted(ms, key=lambda r: float(tvec[vix[r['task']]] @ centers_by_id[cid]))[:3]],
                    "status": "pending_review", "priority_review": True,
                    "naming_provenance": "automatic placeholder from top tasks; user approval pending",
                    "parent_direction": info["parent"], "task_cohesion": round(info["cohesion"], 4)}
        base["paper_count"] = len(ms)
        base["task_count"] = len(tc)
        base["top_tasks"] = [{"task": t, "count": n} for t, n in sorted(tc.items(), key=lambda p: (-p[1], p[0]))[:15]]
        base["needs_review_count"] = sum(int(r["needs_review"]) for r in ms)
        base["title_only_count"] = sum(not papers[r["entity_id"]]["abstract"].strip() for r in ms)
        base["task_cohesion"] = round(cohesion([vix[r["task"]] for r in ms], tvec), 4)
        out.append(base)

    # Nearest directions within the same area, by task centroid.
    by_area = defaultdict(list)
    for c in out:
        if c["id"] in centers_by_id:
            by_area[c["area"]].append(c["id"])
    for c in out:
        if c["id"] not in centers_by_id:
            continue
        peers = [p for p in by_area[c["area"]] if p != c["id"]]
        sims = sorted(((float(centers_by_id[c["id"]] @ centers_by_id[p]), p) for p in peers), reverse=True)[:2]
        c["nearest_directions"] = [{"id": p, "cosine": s} for s, p in sims]

    assert sum(c["paper_count"] for c in out) == len(rows) == len({r["entity_id"] for r in rows})
    fields = ["entity_id", "area", "task", "draft_direction", "parent_direction", "record_kind", "cosine", "margin",
              "needs_review", "area_confidence"]
    with (a.out / "paper_direction_draft.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    (a.out / "named_clusters.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    v1_report = json.loads((d1 / "clustering_report.json").read_text())
    v1_report.update({"phase": "draft_v2", "directions": len(out), "refinement": report,
                      "refined_from": {"named_clusters_sha256": hashlib.sha256((d1 / "named_clusters.json").read_bytes()).hexdigest()}})
    (a.out / "clustering_report.json").write_text(json.dumps(v1_report, ensure_ascii=False, indent=2, default=int))
    legacy = [hashlib.sha256((d1 / "named_clusters.json").read_bytes()).hexdigest()]
    v1_rows = sorted((r["entity_id"], r["parent_direction"]) for r in rows)
    legacy.append(hashlib.sha256("\n".join(e + "," + c for e, c in v1_rows).encode()).hexdigest())
    (a.out / "legacy_run_ids.json").write_text(json.dumps(legacy))
    (a.out / "refine_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=int))
    print(json.dumps({"directions": len(out), "split": len(low), "new_subdirections": sum(1 for c in new_clusters if not c.endswith("-r")),
                      "residuals": sum(1 for c in new_clusters if c.endswith("-r")), "excluded": len(flags)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
