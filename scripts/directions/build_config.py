"""Build config/directions.json from a frozen draft plus the reviewed split proposal.

Inputs
  - a direction draft (named_clusters.json, paper_direction_draft.csv)
  - split_proposals/split_proposal.csv from scripts/directions/propose_splits.py
  - config/areas.json

The five directions the author approved for splitting are replaced by their child directions; every
other direction is carried over with its review status. Non-paper records (front matter, editorials,
...) stay out of the taxonomy.

Aliases are task phrases used as exact-match shortcuts when assigning papers to a direction. A task
phrase can be split across children by title, so an alias is kept only in the child that holds the
majority of that task's papers inside the family, and only if no carried-over direction in the same
area already claims it. A child left without aliases falls back to embedding nearest-center mapping;
its `notes` says so.

This script writes the taxonomy only. Paper -> direction assignment is data and is imported separately.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

# parent direction -> ordered children: (slug, Chinese name, English name, proposal group id)
SPLITS = {
    "trustworthy_ai-d04": [
        ("deepfake", "深度伪造与伪造人脸", "deepfake & face forgery", "deepfake"),
        ("synthetic", "AI 生成图像与视频检测", "AI-generated media detection", "synthetic_detection"),
        ("forgery", "图像篡改检测与定位", "image forgery detection & localization", "forgery_localization"),
        ("misinformation", "虚假信息与有害内容", "misinformation & harmful content", "misinformation"),
        ("antispoof", "人脸呈现攻击与防伪", "face anti-spoofing", "face_antispoof"),
        ("privacy", "人脸隐私保护与去标识", "face privacy & de-identification", "face_privacy"),
        ("audio", "音频与语音深伪", "audio deepfake detection", "audio_deepfake"),
        ("other", "其他深伪与数字取证（待细分）", "other forensics", "unmatched"),
    ],
    "cv_lowlevel-d11": [
        ("rain", "图像去雨", "image deraining", "derain"),
        ("haze", "图像去雾", "image dehazing", "dehaze"),
        ("weather", "多天气与恶劣天气复原", "adverse weather restoration", "weather_other"),
    ],
    "cv_lowlevel-d15": [
        ("video", "视频超分辨率", "video super-resolution", "video_sr"),
        ("real-world", "真实世界与盲超分辨率", "real-world & blind super-resolution", "real_world_sr"),
        ("arbitrary-scale", "任意倍率与连续超分辨率", "arbitrary-scale super-resolution", "arbitrary_scale_sr"),
        ("special-imaging", "非可见光与特殊成像超分辨率", "special imaging super-resolution", "special_imaging_sr"),
        ("content", "人脸与文本等特定内容超分辨率", "content-specific super-resolution", "content_specific_sr"),
        ("efficient", "高效与量化超分辨率", "efficient super-resolution", "efficient_sr"),
        ("generic", "通用图像超分辨率", "generic image super-resolution", "generic_sr"),
        ("other", "其他超分与上采样（待细分）", "other super-resolution", "unmatched"),
    ],
    "llm-d11": [
        ("diffusion", "扩散语言模型", "diffusion language models", "diffusion_lm"),
        ("speculative", "推测解码", "speculative decoding", "speculative"),
        ("distillation", "语言模型蒸馏", "language model distillation", "distillation"),
        ("parallel", "并行解码与高效生成", "parallel decoding & efficient generation", "parallel_decoding"),
        ("other", "LLM 解码与生成（其他，待分流）", "other LLM decoding & generation", "unmatched"),
    ],
    "multimodal-d06": [
        ("meme", "迷因与幽默理解", "meme & humor understanding", "meme_humor"),
        ("misinformation", "多模态虚假信息检测", "multimodal misinformation", "misinformation"),
        ("ads", "广告与说服理解", "advertising & persuasion", "ads_persuasion"),
        ("social", "社会事件、立场与情感", "social events, stance & emotion", "social_news"),
        ("other", "其他多模态社媒内容（待细分）", "other multimodal social content", "unmatched"),
    ],
}

# Parent whose unmatched papers still belong to a named child (group id, not child slug).
UNMATCHED_TARGET = {"cv_lowlevel-d11": "dehaze"}

MAX_ALIASES = 20


def draft_id(d):
    rows = sorted((r["entity_id"], r["draft_direction"]) for r in csv.DictReader((d / "paper_direction_draft.csv").open()))
    return hashlib.sha256("\n".join(e + "," + c for e, c in rows).encode()).hexdigest()


def majority_aliases(group_counts):
    """(task -> group) for the group holding most papers of that task; ties break on group id."""
    majority = {}
    for group, counts in group_counts.items():
        for task, n in counts.items():
            best = majority.get(task)
            if best is None or n > best[1] or (n == best[1] and group < best[0]):
                majority[task] = (group, n)
    return majority


def build_split(cid, parent, members, group_of, task_of):
    group_counts, tasks_by_group = defaultdict(Counter), defaultdict(list)
    for r in members:
        g = group_of[r["entity_id"]]
        if g == "unmatched" and cid in UNMATCHED_TARGET:
            g = UNMATCHED_TARGET[cid]
        task = task_of.get(r["entity_id"], r["task"])
        group_counts[g][task] += 1
        tasks_by_group[g].append(r)
    majority = majority_aliases(group_counts)
    children = []
    for slug, zh, en, group in SPLITS[cid]:
        ms = tasks_by_group.get(group, [])
        if not ms:
            continue
        aliases = sorted((t for t, (g, _) in majority.items() if g == group),
                         key=lambda t: (-group_counts[group][t], t))[:MAX_ALIASES]
        children.append({"direction_id": f"{cid}-{slug}", "area": parent["area"], "parent_direction_id": cid,
                         "name_zh": zh, "name_en": en, "aliases": aliases, "_group": group, "_members": ms})
    return children


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--proposal", type=Path, help="split_proposals dir (default: <draft>/split_proposals)")
    ap.add_argument("--areas", type=Path, default=Path("config/areas.json"))
    ap.add_argument("--out", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--version", default="directions-v1.0", help="taxonomy version written to the output")
    ap.add_argument("--names", type=Path, default=Path("config/direction_names.json"),
                    help="optional overlay with human/LLM-readable names for placeholder directions")
    a = ap.parse_args()
    proposal = a.proposal or a.draft / "split_proposals"

    clusters = {c["id"]: c for c in json.loads((a.draft / "named_clusters.json").read_text())}
    areas = json.loads(a.areas.read_text())
    area_ids = [x["id"] for x in areas["areas"]]
    rows = list(csv.DictReader((a.draft / "paper_direction_draft.csv").open()))
    papers = [r for r in rows if r["draft_direction"] != "excluded-non-paper"]

    group_of, task_of = {}, {}
    with (proposal / "split_proposal.csv").open() as f:
        for r in csv.DictReader(f):
            group_of[r["entity_id"]] = r["group_id"]
            task_of[r["entity_id"]] = r["task"]
    missing = [r["entity_id"] for r in papers if r["draft_direction"] in SPLITS and r["entity_id"] not in group_of]
    if missing:
        raise SystemExit(f"papers missing from split proposal: {len(missing)} (e.g. {missing[:3]})")

    children, assigned = [], {}
    for cid in SPLITS:
        if cid not in clusters:
            raise SystemExit(f"split parent not found in draft: {cid}")
        members = [r for r in papers if r["draft_direction"] == cid]
        for ch in build_split(cid, clusters[cid], members, group_of, task_of):
            for r in ch["_members"]:
                assigned[r["entity_id"]] = ch["direction_id"]
            children.append(ch)

    carried = []
    for cid, c in clusters.items():
        if cid in SPLITS or cid == "excluded-non-paper":
            continue
        if c["area"] not in area_ids:
            raise SystemExit(f"{cid}: unknown area {c['area']}")
        aliases = [t["task"].lower().strip() for t in c.get("top_tasks", []) if t.get("task")][:15]
        carried.append({"direction_id": cid, "area": c["area"], "parent_direction_id": "",
                        "name_zh": c.get("name_zh") or c.get("suggested_name_en") or cid,
                        "name_en": c.get("name_en") or c.get("suggested_name_en") or cid,
                        "definition": c.get("definition") or f"候选范围：{c.get('name_zh', cid)}。",
                        "aliases": aliases, "status": "pending_review",
                        "notes": (c.get("boundary_note") or "").strip()})
        for r in papers:
            if r["draft_direction"] == cid:
                assigned[r["entity_id"]] = cid

    # Carried-over directions keep their aliases; split children may not claim the same (area, alias).
    taken = {(d["area"], al) for d in carried for al in d["aliases"]}
    for ch in sorted(children, key=lambda x: x["direction_id"]):
        kept = []
        for al in ch["aliases"]:
            key = (ch["area"], al)
            if key in taken:
                continue
            taken.add(key)
            kept.append(al)
        ch["aliases"] = kept
        parent = clusters[ch["parent_direction_id"]]
        top = "、".join(kept[:3])
        if ch["_group"] == "unmatched":
            ch["definition"] = f"候选范围：{ch['name_zh']}。由「{parent['name_zh']}」拆分出的未归类部分，需继续细分或并入相邻方向。"
            ch["status"] = "pending_review"
        else:
            ch["definition"] = f"候选范围：{ch['name_zh']}。常见研究任务：{top}。"
            ch["status"] = "confirmed"
        ch["notes"] = (f"2026-10-18 经作者确认，从「{parent['name_zh']}」按关键词分组拆分；"
                       f"成员归属见 split_proposal.csv，未逐篇人工复核。")
        if not kept:
            ch["notes"] += " 该方向没有独占的 task 别名，新论文需按向量就近映射。"

    directions = [{k: v for k, v in d.items() if not k.startswith("_")} for d in children] + carried
    if a.names.exists():
        overlay = json.loads(a.names.read_text(encoding="utf-8"))
        for d in directions:
            if d["direction_id"] in overlay:
                d.update({k: overlay[d["direction_id"]][k] for k in ("name_zh", "name_en", "definition")
                          if overlay[d["direction_id"]].get(k)})

    if len(assigned) != len(papers):
        missing = {r["entity_id"] for r in papers} - set(assigned)
        raise SystemExit(f"unassigned papers: {len(missing)} (e.g. {sorted(missing)[:3]})")
    ids = [d["direction_id"] for d in directions]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate direction ids")
    for d in directions:
        if d["area"] not in area_ids:
            raise SystemExit(f"{d['direction_id']}: unknown area {d['area']}")
    alias_keys = [(d["area"], al) for d in directions for al in d["aliases"]]
    dup = [k for k, n in Counter(alias_keys).items() if n > 1]
    if dup:
        raise SystemExit(f"duplicate (area, alias): {dup[:5]}")
    no_alias = [d["direction_id"] for d in directions if not d["aliases"]]
    if no_alias:
        print(f"note: {len(no_alias)} directions have no exclusive alias: {no_alias}")

    order = {x: i for i, x in enumerate(area_ids)}
    directions.sort(key=lambda d: (order[d["area"]], d["direction_id"]))
    out = {
        "version": a.version,
        "description": "Level-2 research directions. Area comes from config/areas.json. aliases are lower-case task phrases used as exact-match shortcuts; when no alias matches, papers fall back to embedding nearest-center. status=confirmed means the split decision was accepted by the author; pending_review means carried over from the draft and not individually reviewed.",
        "source": {
            "draft": a.draft.parent.name,
            "draft_id": draft_id(a.draft),
            "areas_version": areas["version"],
            "proposal": "split_proposals/split_proposal.csv",
            "built_at": date.today().isoformat(),
            "papers": len(papers),
            "non_paper_records": len(rows) - len(papers),
        },
        "directions": directions,
    }
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    split_n = sum(1 for d in directions if d["parent_direction_id"])
    print(f"wrote {a.out}: {len(directions)} directions ({split_n} split children), "
          f"{len(papers)} papers covered, {len(rows) - len(papers)} non-paper records excluded")


if __name__ == "__main__":
    main()
