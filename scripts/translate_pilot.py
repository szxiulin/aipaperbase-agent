#!/usr/bin/env python3
"""Pilot calibration: cost/quality probe for title+abstract -> Simplified Chinese.

Samples N entities with a canonical abstract, translates each via the configured
GENERATOR_* LLM (thinking disabled, low temperature), records real prompt/completion
tokens and Chinese lengths, and extrapolates to the full catalog so you can decide
whether to precompute all translations locally.

Output: prints a summary + extrapolation, saves per-item records to a JSON under
data/analyses (gitignored), and prints 2-3 samples for a quality eyeball.
Run from the repo root:
    .venv/bin/python scripts/translate_pilot.py --sample 24 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from raglib.config import load_config, load_dotenv

DB = Path("data/database/catalog.sqlite")
MIN_ABS, MAX_ABS = 200, 12000

SYSTEM = (
    "你是严谨的学术翻译。把英文 AI 论文的标题与摘要直译成简体中文。要求："
    "忠实直译，不要改写、概括或增删信息；数字、公式、代码、模型与数据集名"
    "（如 LoRA、GPT、ImageNet、diffusion、Transformer）保留原文或按其公认译法自然处理；"
    "只输出一个 JSON 对象，含两个键 title_zh 与 abstract_zh。"
)


def _connect_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def sample_entities(n: int, seed: int) -> tuple[list[dict], dict]:
    con = _connect_ro()
    try:
        agg = con.execute(
            f"""SELECT COUNT(*) AS cnt,
                       COALESCE(SUM(length(trim(p.abstract))),0) AS abs_chars,
                       COALESCE(SUM(length(trim(e.canonical_title))),0) AS title_chars
                FROM paper_entities e
                JOIN paper_records p ON p.record_id = e.canonical_record_id
                WHERE p.abstract IS NOT NULL AND length(trim(p.abstract)) BETWEEN {MIN_ABS} AND {MAX_ABS}"""
        ).fetchone()
        rows = con.execute(
            f"""SELECT e.entity_id AS id,
                       length(trim(p.abstract)) AS alen,
                       length(trim(e.canonical_title)) AS tlen
                FROM paper_entities e
                JOIN paper_records p ON p.record_id = e.canonical_record_id
                WHERE p.abstract IS NOT NULL AND length(trim(p.abstract)) BETWEEN {MIN_ABS} AND {MAX_ABS}"""
        ).fetchall()
        rng = random.Random(seed)
        chosen = rng.sample(list(rows), min(n, len(rows)))
        ids = [r["id"] for r in chosen]
        qmarks = ",".join("?" for _ in ids)
        details = con.execute(
            f"""SELECT e.entity_id AS id, e.canonical_title AS title, p.abstract AS abstract
                FROM paper_entities e
                JOIN paper_records p ON p.record_id = e.canonical_record_id
                WHERE e.entity_id IN ({qmarks})""",
            ids,
        ).fetchall()
        return [dict(r) for r in details], dict(agg)
    finally:
        con.close()


def translate_one(base_url: str, api_key: str, model: str, max_tokens: int, title: str, abstract: str):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"title": title, "abstract": abstract}, ensure_ascii=False)},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "top_p": 1.0,
    }
    with httpx.Client(timeout=240) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage") or {}
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    return (
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        content,
        data.get("model") or model,
    )


def parse_zh(content: str) -> dict:
    s, e = content.find("{"), content.rfind("}")
    if s >= 0 and e > s:
        try:
            obj = json.loads(content[s : e + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="data/analyses/translate_pilot.json")
    args = ap.parse_args()

    load_dotenv()
    cfg = load_config().generator
    if not (cfg.base_url and cfg.api_key and cfg.model):
        raise SystemExit("GENERATOR_BASE_URL / API_KEY / MODEL 未配置，无法跑试点。")

    entities, agg = sample_entities(args.sample, args.seed)
    print(f"抽样 {len(entities)} 篇 | 样本池实体 {agg['cnt']:,} · 摘要字符 {agg['abs_chars']:,} · 标题字符 {agg['title_chars']:,}")
    print(f"模型: {cfg.model}")

    records, ok = [], 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {
            pool.submit(translate_one, cfg.base_url, cfg.api_key, cfg.model, cfg.max_tokens, r["title"], r["abstract"]): r
            for r in entities
        }
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                pt, ct, content, model = fut.result()
                obj = parse_zh(content)
                title_zh = (obj.get("title_zh") or "").strip()
                abstract_zh = (obj.get("abstract_zh") or "").strip()
                if not abstract_zh and not obj:
                    abstract_zh = content.strip()
                rec = {
                    "entity_id": r["id"],
                    "title_len": len(r["title"]),
                    "abstract_len": len(r["abstract"]),
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "title_zh_len": len(title_zh),
                    "abstract_zh_len": len(abstract_zh),
                    "title_zh": title_zh,
                    "abstract_zh": abstract_zh[:400],
                    "model": model,
                }
                records.append(rec)
                ok += 1
            except Exception as exc:  # network/HTTP errors — record and continue
                records.append({"entity_id": r["id"], "error": str(exc)[:200]})

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

    good = [x for x in records if "error" not in x and x["abstract_zh_len"] > 0]
    print(f"\n成功 {ok}/{len(entities)}（其中含中文正文 {len(good)}）")
    if not good:
        print("无可用样本，检查上方错误。")
        return

    tot_in = sum(x["prompt_tokens"] for x in good)
    tot_out = sum(x["completion_tokens"] for x in good)
    en_abs = sum(x["abstract_len"] for x in good)
    en_title = sum(x["title_len"] for x in good)
    zh_abs = sum(x["abstract_zh_len"] for x in good)
    zh_title = sum(x["title_zh_len"] for x in good)
    print(f"\n[实测] 输入 tokens {tot_in:,} | 输出 tokens {tot_out:,} | 中文正文 {zh_abs:,} 字符")
    print(f"[实测] 英文摘要 每 token 输入 ≈ {en_abs / max(tot_in, 1):.2f} 字符  |  中文 每 token 输出 ≈ {zh_abs / max(tot_out, 1):.2f} 字符")
    print(f"[实测] 中文/英文 字符比 ≈ {zh_abs / max(en_abs, 1):.3f}（摘要）、{zh_title / max(en_title, 1):.3f}（标题）")

    def extrap(rate_in_per_char, rate_out_per_char, zh_title_mult):
        en_total = agg["abs_chars"] + agg["title_chars"]
        in_t = round(en_total * rate_in_per_char)
        zh_abs_chars = round(agg["abs_chars"] * (zh_abs / max(en_abs, 1)))
        zh_title_chars = round(agg["title_chars"] * zh_title_mult)
        out_t = round((zh_abs_chars + zh_title_chars) * rate_out_per_char)
        return in_t, out_t, zh_abs_chars + zh_title_chars

    in_rate = tot_in / max(en_abs + en_title, 1)
    out_rate = tot_out / max(zh_abs + zh_title, 1)
    zh_mult = zh_title / max(en_title, 1)
    ein, eout, ezh = extrap(in_rate, out_rate, zh_mult)
    print(f"\n[外推·全样本池] 输入 ≈ {ein:,} tokens | 输出 ≈ {eout:,} tokens | 合计 ≈ {ein + eout:,} tokens（中文合计约 {ezh:,} 字符）")
    print("成本 ≈ 输入×价 + 输出×价（价按你供应商填）：")
    for label, pi, po in (("USD $/M", 0.2, 0.8), ("CNY ¥/M", 0.5, 2.0)):
        print(f"   {label}: in {pi}/M, out {po}/M -> 约 {(ein * pi + eout * po) / 1e6:.2f}")

    print("\n[抽 3 条供质量目检]")
    for rec in good[:3]:
        print(f"\n• {rec['title_zh']}\n  {rec['abstract_zh'][:150]}…")
    print(f"\n详细记录已写 {args.out}")


if __name__ == "__main__":
    main()
