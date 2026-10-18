"""Verify an ad-hoc topic: sample recall candidates and ask the LLM whether each paper is on-topic.

Costs API calls. Use a small --sample for a pilot. Writes a verification report under
data/analyses/topics/<slug>/verification.json; it does not modify labels or the catalog.

Example:
  .venv/bin/python scripts/topics/verify_topic.py --query "agent image restoration" --sample 30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.capabilities.topics import candidate_ids  # noqa: E402
from raglib.config import load_config, load_dotenv  # noqa: E402

SYSTEM = """You judge whether a paper is substantively about a user-defined research topic.
Return one JSON object: {"verdict": "relevant" | "uncertain" | "not_relevant", "reason": "<short Chinese reason>"}.
Be strict: a paper that only mentions the topic as a tool or related work is not_relevant.
Output JSON only."""


def wilson(k: int, n: int) -> list[float]:
    if not n:
        return [0.0, 1.0]
    z, p = 1.96, k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(100 * max(0, center - half), 1), round(100 * min(1, center + half), 1)]


def call(gen, topic: str, paper: dict) -> dict:
    user = (f"Topic: {topic}\nTitle: {paper['title']}\n"
            f"Abstract: {(paper['abstract'] or '(no abstract available)')[:3000]}")
    payload = {"model": gen.model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
               "max_tokens": 200, "temperature": 0.0, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}}
    last = ""
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(f"{gen.base_url.rstrip('/')}/chat/completions",
                                       headers={"Authorization": f"Bearer {gen.api_key}"}, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request, response=response)
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage") or {}
            return {"content": data["choices"][0]["message"]["content"] or "", "usage": usage}
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True)
    ap.add_argument("--database", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-candidates", type=int, default=5000)
    ap.add_argument("--out-dir", type=Path, default=Path("data/analyses/topics"))
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    recall = candidate_ids(conn, a.query, {}, limit=a.limit_candidates)
    ids = recall["entity_ids"]
    if not ids:
        raise SystemExit("no candidates")
    rng = random.Random(a.seed)
    sample_ids = rng.sample(ids, min(a.sample, len(ids)))
    papers = []
    for eid in sample_ids:
        row = conn.execute("""SELECT e.entity_id, e.canonical_title AS title, p.abstract, p.year, p.venue,
                                     a.area_id, d.direction_id
                              FROM paper_entities e
                              JOIN paper_records p ON p.record_id = e.canonical_record_id
                              JOIN entity_area_assignments a ON a.entity_id = e.entity_id
                              LEFT JOIN entity_direction_assignments d ON d.entity_id = e.entity_id
                              WHERE e.entity_id = ?""", (eid,)).fetchone()
        if row:
            papers.append(dict(row))
    conn.close()

    load_dotenv()
    gen = load_config().generator
    if not (gen.base_url and gen.api_key and gen.model):
        raise SystemExit("generator is not configured")
    reviews, tokens = [], {"prompt": 0, "completion": 0}
    for paper in papers:
        reply = call(gen, a.query, paper)
        obj = json.loads(reply["content"][reply["content"].find("{"):reply["content"].rfind("}") + 1])
        verdict = obj.get("verdict") if obj.get("verdict") in ("relevant", "uncertain", "not_relevant") else "uncertain"
        reviews.append({**paper, "verdict": verdict, "reason": str(obj.get("reason", ""))[:200]})
        tokens["prompt"] += int((reply["usage"] or {}).get("prompt_tokens") or 0)
        tokens["completion"] += int((reply["usage"] or {}).get("completion_tokens") or 0)
        print(f"  {verdict:13s} {paper['title'][:70]}")

    relevant = sum(r["verdict"] == "relevant" for r in reviews)
    not_relevant = sum(r["verdict"] == "not_relevant" for r in reviews)
    uncertain = sum(r["verdict"] == "uncertain" for r in reviews)
    decided = relevant + not_relevant
    precision = round(100 * relevant / decided, 1) if decided else None
    slug = re.sub(r"[^a-z0-9]+", "-", a.query.lower()).strip("-")[:60]
    report = {"query": a.query, "method": recall["method"], "candidate_count": len(ids),
              "sample_size": len(reviews), "seed": a.seed, "model": gen.model,
              "relevant": relevant, "not_relevant": not_relevant, "uncertain": uncertain,
              "precision_percent": precision,
              "ci95": wilson(relevant, decided) if decided else None,
              "tokens": tokens, "estimated_cost_usd": round(tokens["prompt"] / 1e6 * 0.14 + tokens["completion"] / 1e6 * 0.28, 4),
              "reviewed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
              "samples": reviews}
    out = a.out_dir / (slug or "topic") / "verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nprecision {precision}% ({relevant}/{decided}); uncertain {uncertain}; "
          f"candidates {len(ids)}; est ${report['estimated_cost_usd']} -> {out}")


if __name__ == "__main__":
    main()
