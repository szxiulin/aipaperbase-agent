"""Propose keyword-grouped splits for the known mixed directions (read-only review aid).

Reads a frozen draft and the catalog, then groups each paper of the selected directions by
explicit keyword rules on task + title, evaluated in priority order (first match wins). It
writes a CSV/Markdown/JSON proposal next to the draft. It never edits labels, config,
directions or the catalog: the output is a review aid, not a classification result.
"""
import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path

# (group id, Chinese name, regex). Order is the priority order; a paper joins the first match.
GROUPS = {
    "trustworthy_ai-d04": [
        ("face_antispoof", "人脸呈现攻击与防伪",
         r"anti-?spoof|presentation attack|liveness|face spoof|spoofing"),
        ("audio_deepfake", "音频与语音深伪",
         r"audio deepfake|speech deepfake|audio-visual deepfake|voice spoof|voice anti|audio forgery|synthesized voice|voice cloning|fake voice"),
        ("deepfake", "深度伪造与伪造人脸",
         r"deep[ -]?fake|face forgery|forged face|face manipulation|face[- ]?swap|talking[- ]head|synthetic face|face reenactment|identity swap"),
        ("synthetic_detection", "AI 生成图像/视频检测与归因",
         r"ai-?generated|generated image|generated video|synthetic image|synthetic video|diffusion-generated|generated content|attribution|aigc|fake image|forged image"),
        ("forgery_localization", "图像篡改检测与定位",
         r"forgery|tamper|splicing|copy[- ]move|localization|image manipulation|manipulated image"),
        ("misinformation", "虚假信息与有害内容",
         r"fake news|misinformation|disinformation|rumor|rumour|fact[- ]?check|claim verif|hate|harmful|offensive|propaganda|misleading|deception|media manipulation"),
        ("face_privacy", "人脸隐私保护与去标识",
         r"privacy|de-?identif|anonymiz|template protection|template inversion|identity protection"),
    ],
    "cv_lowlevel-d11": [
        ("derain", "去雨", r"derain|de-rain|rain removal|rain streak|raindrop|rainy|rain and mist"),
        ("dehaze", "去雾", r"dehaz|de-haz|haze removal|hazy"),
        ("weather_other", "其他恶劣天气/多天气复原",
         r"snow|weather|fog|sandstorm|hail|all-in-one|cloud|turbulence|mist"),
    ],
    "cv_lowlevel-d15": [
        ("video_sr", "视频超分", r"video"),
        ("real_world_sr", "真实世界/盲超分", r"real[- ]world|blind|real image|realistic|real sr"),
        ("arbitrary_scale_sr", "任意倍率/连续超分", r"arbitrary[- ]scale|continuous|implicit|any-scale"),
        ("special_imaging_sr", "非可见光与特殊成像超分",
         r"hyperspectral|infrared|thermal|remote sensing|\bsar\b|depth map|depth image|depth super|light field|omnidirectional|event camera|neuromorphic|\bmri\b|computed tomography|\bct\b|medical image|ultrasound|spectral"),
        ("content_specific_sr", "特定内容超分（人脸/文本等）",
         r"\bface\b|facial|scene text|text image|\btext\b|document|license plate"),
        ("efficient_sr", "高效/量化超分", r"efficient|lightweight|real-time|quantiz|single-step|few-step|distill"),
        ("generic_sr", "通用图像超分（未细分）", r"super[- ]?resolution|upsampl|\bsr\b"),
    ],
    "llm-d11": [
        ("diffusion_lm", "扩散语言模型", r"diffusion"),
        ("speculative", "推测解码", r"speculative|draft model|verification decoding"),
        ("distillation", "蒸馏", r"distillation|distill"),
        ("parallel_decoding", "并行解码与高效生成", r"parallel|efficient|acceleration|speed"),
    ],
    "multimodal-d06": [
        ("misinformation", "虚假信息、争议与有害内容",
         r"misinformation|fake|rumor|harmful|hate|abusive|offensive|moderation|deception|evasive|propaganda|controversy|claim"),
        ("meme_humor", "迷因与幽默理解", r"meme|humor|sarcas|irony|pun|satire|joke"),
        ("ads_persuasion", "广告与说服", r"advertis|persuasion|marketing|sponsor"),
        ("social_news", "社会事件、立场与情感", r"stance|sentiment|emotion|news|event|disaster|humanitarian|climate|community|debate"),
    ],
}
UNMATCHED = ("unmatched", "未命中关键词（需人工或 LLM 复核）")

# Reviewer-facing guidance, derived from this run's numbers and the known issues in Sprint-029/030.
RECOMMENDATIONS = {
    "trustworthy_ai-d04": "建议拆为「深度伪造与伪造人脸」「AI 生成图像/视频检测」「图像篡改检测与定位」「虚假信息与有害内容」四个方向；"
                          "「人脸防伪」「音频深伪」「人脸隐私保护」可各自成方向，也可先并入相邻方向。其中虚假信息组与 "
                          "multimodal-d06、multimodal 领域的虚假信息论文存在跨方向重叠，需一并决定归属。",
    "cv_lowlevel-d11": "去雨与去雾规模相当（各约 70 篇），建议拆成两个独立方向；「其他恶劣天气/多天气」保留为第三方向或并入去雾。",
    "cv_lowlevel-d15": "建议至少拆出「视频超分」「真实世界/盲超分」「非可见光与特殊成像超分」三个方向；"
                       "「任意倍率」「特定内容」「高效超分」可作为子方向或保留在通用超分下；通用超分仍有约 150 篇，后续可按退化类型再细分。",
    "llm-d11": "扩散语言模型 146 篇、推测解码 103 篇是两条独立技术线，应拆开；但未命中 55% 说明该方向实际是"
               "「LLM 解码/生成/效率」的杂项集合（模型合并、模型编辑、约束生成、MoE、字节级建模都在里面），"
               "拆完两条主线后剩余部分需要重新命名或并入其他方向，不能直接定稿。",
    "multimodal-d06": "该方向名与内容不符：迷因/幽默 26 篇、社会与广告 10 篇，真正的虚假信息只有 12 篇。"
                      "建议改名为「多模态社交媒体内容理解」，或把虚假信息子集并入 trustworthy_ai 的虚假信息方向，"
                      "其余按迷因/幽默、广告说服、社会立场拆开。",
}


def group_of(text, rules):
    for gid, zh, pattern in rules:
        if re.search(pattern, text):
            return gid, zh, pattern
    return UNMATCHED[0], UNMATCHED[1], ""


def load_meta(catalog, ids):
    con = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
    meta = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ("SELECT e.entity_id, e.canonical_title, e.first_year, r.venue FROM paper_entities e "
             "LEFT JOIN paper_records r ON r.record_id = e.canonical_record_id "
             f"WHERE e.entity_id IN ({','.join('?' * len(chunk))})")
        for eid, title, year, venue in con.execute(q, chunk):
            meta[eid] = {"title": title or "", "year": year or "", "venue": venue or ""}
    con.close()
    return meta


def md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", type=Path, required=True, help="draft dir with paper_direction_draft.csv and named_clusters.json")
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"), help="catalog.sqlite (read-only)")
    ap.add_argument("--out", type=Path, help="output dir (default: <draft>/split_proposals)")
    a = ap.parse_args()
    out = a.out or a.draft / "split_proposals"
    out.mkdir(parents=True, exist_ok=True)

    clusters = {c["id"]: c for c in json.loads((a.draft / "named_clusters.json").read_text())}
    rows = [r for r in csv.DictReader((a.draft / "paper_direction_draft.csv").open())
            if r["draft_direction"] in GROUPS]
    if not rows:
        raise SystemExit("no papers found for the configured directions")
    meta = load_meta(a.catalog, {r["entity_id"] for r in rows})

    grouped = []
    for r in rows:
        text = (r["task"] + " " + meta.get(r["entity_id"], {}).get("title", "")).lower()
        gid, zh, pattern = group_of(text, GROUPS[r["draft_direction"]])
        grouped.append({**r, "group_id": gid, "group_zh": zh, "matched_by": f"rule: {pattern}" if pattern else "unmatched",
                        **meta.get(r["entity_id"], {})})
    grouped.sort(key=lambda x: (list(GROUPS).index(x["draft_direction"]),
                                [g[0] for g in GROUPS[x["draft_direction"]] + [UNMATCHED]].index(x["group_id"]),
                                x["entity_id"]))

    with (out / "split_proposal.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["direction", "direction_name", "group_id", "group_zh",
                                          "entity_id", "task", "title", "year", "venue", "matched_by"])
        w.writeheader()
        for r in grouped:
            w.writerow({"direction": r["draft_direction"], "direction_name": clusters[r["draft_direction"]].get("name_zh", ""),
                        "group_id": r["group_id"], "group_zh": r["group_zh"], "entity_id": r["entity_id"],
                        "task": r["task"], "title": r["title"], "year": r["year"], "venue": r["venue"],
                        "matched_by": r["matched_by"]})

    summary = {}
    lines = ["# 已知混簇拆分建议（只读审核辅助）", "",
             "> 由 `scripts/directions/propose_splits.py` 生成。分组只依据 task + 标题的关键词，按给定优先级取首个命中；",
             "> 它**不是**分类结果，也没有改动标签、配置或目录。全部命中情况需结合代表论文人工复核。", "",
             f"> 草案：`{a.draft.parent.name}`；纳入 {len(grouped)} 篇；分组规则与关键词见脚本 `GROUPS`。", ""]
    for did, rules in GROUPS.items():
        sub = [r for r in grouped if r["draft_direction"] == did]
        total = len(sub)
        c = clusters.get(did, {})
        lines += [f"## {did}｜{c.get('name_zh', '')}", "",
                  f"当前方向共 **{total}** 篇，内聚度 {c.get('task_cohesion', '?')}；"
                  f"本次规则命中 {sum(1 for r in sub if r['group_id'] != 'unmatched')} 篇。", "",
                  "| 分组 | 篇数 | 占比 | 示例论文 |", "|---|---|---|---|"]
        summary[did] = {"name_zh": c.get("name_zh", ""), "papers": total, "groups": {}}
        for gid, zh, pattern in rules + [(UNMATCHED[0], UNMATCHED[1], "")]:
            g = [r for r in sub if r["group_id"] == gid]
            summary[did]["groups"][gid] = {"zh": zh, "papers": len(g), "patch": pattern}
            by_task = {}
            for r in g:
                by_task.setdefault(r["task"], []).append(r)
            top_tasks = sorted(by_task.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:2]
            ex = "；".join(md(v[0]["title"])[:80] for _, v in top_tasks) or "—"
            lines.append(f"| {zh} | {len(g)} | {len(g) / total * 100:.1f}% | {ex} |")
        lines += ["", f"**建议**：{RECOMMENDATIONS[did]}", ""]
        unm = [r for r in sub if r["group_id"] == "unmatched"]
        if unm:
            top = {}
            for r in unm:
                top[r["task"]] = top.get(r["task"], 0) + 1
            lines += ["", "未命中中最常见的 task：" +
                      "、".join(f"{k}（{v}）" for k, v in sorted(top.items(), key=lambda x: -x[1])[:12]), ""]
        lines.append("")
    lines += ["## 使用边界", "",
              "- 关键词分组只能说明“方向上大致有哪些子任务”，不能证明边界正确；例如同时含 video 与 real-world 的论文按优先级归入视频组。",
              "- 未命中部分往往包含真实的细分方向；需要读代表论文后决定是补规则、改名，还是保留为独立方向。",
              "- 代表论文按组内最常见的 task 选取；上游 task 抽取错误会直接反映在示例里（例如标题讲去噪、task 写超分）。",
              "- 正式定稿仍需你在审核页确认，产物写入 `config/directions.json` 属于后续独立步骤。", ""]
    (out / "README.md").write_text("\n".join(lines))
    (out / "split_proposal.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"wrote {out}/split_proposal.csv, README.md, split_proposal.json ({len(grouped)} papers)")


if __name__ == "__main__":
    main()
