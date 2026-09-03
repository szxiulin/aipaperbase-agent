"""Read-only simulation: quantify the impact of two topic-classification v2 fixes on the acceptance metrics.

Fix A (vocabulary cleanup, ordinary English words / generic corpora masquerading as benchmarks):
  - rst_dehaze: remove its (English pronoun), ots
  - rst_deblur: remove hide (verb)
  - mm_driving: remove once (adverb)
  - nlp_ie:    remove ace (ordinary word)
  - nlp_code:  remove apps (ordinary word)
  - agent_eval_infra: remove gym (ordinary word)
  - nlp_summary: remove arxiv, pubmed (generic corpus)
  - hv_recognition: remove cifar (generic toy benchmark, drags methodology papers into f7)
  - llm_eval:  remove swe-bench (its main semantics live in agent_eval_infra)
  - gen_text2image/gen_editing: remove coco, imagenet, ms-coco (ImageNet/COCO are not generative evaluation benchmarks)

Fix B (rule): an additional leaf must have non-benchmark textual evidence — a bench-only hit no longer constitutes a cross-family extra on its own
(benchmarks can still help a primary leaf and leaves with textual evidence, but do not create multi-label on their own).

Usage: python3 scripts/validate/sim_topics_clean.py   (read only, does not write the database)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.analytics.topics import _entity_texts, _normalize, classify, load_topics

DB = ROOT / "data" / "database" / "catalog.sqlite"

# ---------------------------------------------------------------- Fix A removal list
# (topic_id, benchmark_label_to_remove, reason)
BENCH_REMOVE = [
    ("rst_dehaze", "its", "英文代词 its 命中所有英文摘要"),
    ("rst_dehaze", "ots", "ots 普通词"),
    ("rst_deblur", "hide", "动词 hide"),
    ("mm_driving", "once", "副词 once"),
    ("nlp_ie", "ace", "普通词 ace"),
    ("nlp_code", "apps", "普通复数词 apps"),
    ("agent_eval_infra", "gym", "普通词 gym"),
    ("nlp_summary", "arxiv", "arXiv 泛语料"),
    ("nlp_summary", "pubmed", "PubMed 泛语料"),
    ("hv_recognition", "cifar", "CIFAR 通用玩具集"),
    ("llm_eval", "swe-bench", "主语义在 agent_eval_infra"),
    ("gen_text2image", "coco", "COCO 非生成评测基准"),
    ("gen_text2image", "ms-coco", "COCO 非生成评测基准"),
    ("gen_text2image", "imagenet", "ImageNet 非生成评测基准"),
    ("gen_editing", "coco", "COCO 非编辑评测基准"),
    ("gen_editing", "imagenet", "ImageNet 非编辑评测基准"),
]


def build_clean_topics(topics):
    """Deep-copy the Topic list and apply the removal list."""
    cleaned = []
    for t in topics:
        if not t.benchmarks or not any(topic_id == t.topic_id for topic_id, _, _ in BENCH_REMOVE):
            cleaned.append(t)
            continue
        banned = {label for topic_id, label, _ in BENCH_REMOVE if topic_id == t.topic_id}
        new_bench = tuple((label, norm) for label, norm in t.benchmarks if label not in banned)
        cleaned.append(t.__class__(
            topic_id=t.topic_id, parent_topic_id=t.parent_topic_id, name=t.name,
            description=t.description, sort_order=t.sort_order, threshold=t.threshold,
            strong=t.strong, weak=t.weak, benchmarks=new_bench,
            cooccurrences=t.cooccurrences, is_leaf=t.is_leaf,
        ))
    return cleaned


def classify_with_fix_b(title, abstract, topics):
    """Fix B: after the original classify, filter out bench-only extras (whose evidence is entirely benchmark)."""
    out = classify(title, abstract, topics)
    if not out:
        return out
    result = {}
    for topic_id, payload in out.items():
        if payload["role"] == "extra":
            kinds = {ev.get("kind") for ev in payload["evidence"]}
            if kinds <= {"benchmark"}:
                continue  # bench-only is not used as an additional leaf
        result[topic_id] = payload
    return result


def main() -> None:
    cfg, topics = load_topics(ROOT / "config" / "topics.json")
    clean_topics = build_clean_topics(topics)
    print(f"修复 A 删除 {len(BENCH_REMOVE)} 条噪声 benchmark；叶数不变")
    for topic_id, label, why in BENCH_REMOVE:
        print(f"  - {topic_id:<20} {label:<12} {why}")

    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    # entity -> has_abs (canonical record abstract length > 200)
    has_abs = {
        r["entity_id"]: bool(r["has_abs"])
        for r in c.execute(
            """SELECT e.entity_id,
                      CASE WHEN pr.abstract IS NOT NULL AND length(trim(pr.abstract)) > 200
                           THEN 1 ELSE 0 END AS has_abs
               FROM paper_entities e
               LEFT JOIN paper_records pr ON pr.record_id = e.canonical_record_id"""
        )
    }

    def run_variant(label, topic_list, fix_b: bool) -> None:
        t0 = time.time()
        prim_total = 0
        prim_hasabs = 0
        uncls_hasabs = 0
        multi_label = 0
        extra_count = 0
        family_primary = Counter()
        top_prim = Counter()
        no_abs_total = 0
        for entity_id, title, abstract in _entity_texts(c):
            out = classify_with_fix_b(title, abstract, topic_list) if fix_b else classify(title, abstract, topic_list)
            prim = None
            extras = 0
            for tid, payload in out.items():
                if payload["role"] == "primary":
                    prim = tid
                else:
                    extras += 1
            if prim is None:
                if has_abs.get(entity_id):
                    uncls_hasabs += 1
                continue
            prim_total += 1
            if has_abs.get(entity_id):
                prim_hasabs += 1
            else:
                no_abs_total += 1
            top_prim[prim] += 1
            if extras:
                multi_label += 1
                extra_count += extras
            # roll up to the family
            node = next(t for t in topic_list if t.topic_id == prim)
            while node.parent_topic_id:
                node = next(t for t in topic_list if t.topic_id == node.parent_topic_id)
            family_primary[node.topic_id] += 1
        total_hasabs = prim_hasabs + uncls_hasabs
        dt = time.time() - t0
        print(f"\n===== {label} ({dt:.0f}s) =====")
        print(f"primary 总数        : {prim_total}")
        print(f"有摘要已分类        : {prim_hasabs} / {total_hasabs}")
        print(f"有摘要未分类率      : {uncls_hasabs / total_hasabs * 100:.1f}%   (目标 ≤8%)")
        print(f"多标签率            : {multi_label / prim_total * 100:.1f}%   (目标 <30%, 基准36.4%)")
        print(f"附加叶行数          : {extra_count}   (基准 41230)")
        print(f"无摘要却已分类      : {no_abs_total}")
        print("族 primary top12    :", family_primary.most_common(12))
        print("叶 primary top8     :", top_prim.most_common(8))

    run_variant("V0 现状(复现校准)", topics, fix_b=False)
    run_variant("V1 修复A 清噪声benchmark", clean_topics, fix_b=False)
    run_variant("V2 修复A+B bench-only不作extra", clean_topics, fix_b=True)


if __name__ == "__main__":
    main()
