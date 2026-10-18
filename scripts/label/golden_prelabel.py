"""LLM prelabel the gold sample: first the area (21 classes), then a direction inside that area.

The output is a *prelabel*, not gold: only the author's review makes a gold label. Calls are
resumable (rows already ok are skipped) and token usage is recorded per row. No files outside the
output directory are written.
"""
import argparse
import csv
import json
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
from raglib.config import load_config, load_dotenv  # noqa: E402

PROMPT_VERSION = "gold-prelabel-v1"
MAX_ABSTRACT_CHARS = 3000
FIELDS = ["entity_id", "pred_area", "pred_area_confidence", "pred_direction", "pred_direction_confidence",
          "area_reason", "direction_reason", "model", "prompt_version", "prompt_tokens", "cached_tokens",
          "completion_tokens", "status"]


def area_system(cfg: dict) -> str:
    areas = "\n".join(f"- {a['id']}: {a['en']}. {a['scope']}" for a in cfg["areas"])
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(cfg["decision_rules"], 1))
    return f"""You label AI research papers for a bibliometric database. Read the title and abstract and return ONE JSON object.

Research areas (choose ids exactly from this list):
{areas}

Decision rules:
{rules}

Return JSON: {{"area": "<id>", "area_confidence": "high|medium|low", "reason": "<one short sentence>"}}.
Output JSON only."""


def direction_system(area: dict, directions: list[dict]) -> str:
    lines = []
    for d in directions:
        aliases = "、".join(d["aliases"][:6])
        lines.append(f"- {d['direction_id']}: {d['name_zh']} ({d['name_en']}). {d['definition']}"
                     + (f" 常见任务：{aliases}" if aliases else ""))
    return f"""You assign a research DIRECTION inside the area "{area['id']}: {area['en']}".

Directions (choose direction_id exactly from this list):
{chr(10).join(lines)}

Pick the single direction that best matches what the paper actually studies. Prefer the direction
whose common tasks match the paper's task; do not pick a broader direction just because it is larger.
Return JSON: {{"direction": "<direction_id>", "confidence": "high|medium|low", "reason": "<one short sentence>"}}.
Output JSON only."""


def call_llm(gen, system: str, user: str, max_tokens: int, retries: int = 3) -> dict:
    payload = {"model": gen.model, "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": 0.0, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}}
    last = ""
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(f"{gen.base_url.rstrip('/')}/chat/completions",
                                   headers={"Authorization": f"Bearer {gen.api_key}"}, json=payload)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage") or {}
            return {"content": data["choices"][0]["message"]["content"] or "",
                    "model": data.get("model") or gen.model,
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "cached_tokens": int(usage.get("prompt_cache_hit_tokens")
                                         or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0)}
        except Exception as exc:  # network / HTTP / malformed response
            last = str(exc)[:200]
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=Path, default=Path("data/analyses/golden_20261018/sample.csv"))
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--areas", type=Path, default=Path("config/areas.json"))
    ap.add_argument("--directions", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--out", type=Path, default=Path("data/analyses/golden_20261018/prelabel.csv"))
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    cfg = json.loads(a.areas.read_text(encoding="utf-8"))
    areas = {x["id"]: x for x in cfg["areas"]}
    directions = json.loads(a.directions.read_text(encoding="utf-8"))["directions"]
    by_area = defaultdict(list)
    for d in directions:
        by_area[d["area"]].append(d)

    with a.sample.open(encoding="utf-8", newline="") as f:
        sample = {r["entity_id"]: r for r in csv.DictReader(f)}
    con = sqlite3.connect(f"file:{a.catalog}?mode=ro", uri=True)
    papers = {}
    for eid in sample:
        row = con.execute("""SELECT e.canonical_title, r.abstract, r.venue, r.year
                             FROM paper_entities e JOIN paper_records r ON r.record_id = e.canonical_record_id
                             WHERE e.entity_id = ?""", (eid,)).fetchone()
        if row:
            papers[eid] = {"title": row[0] or "", "abstract": row[1] or "", "venue": row[2] or "", "year": row[3] or ""}
    con.close()

    done = set()
    if a.out.exists():
        with a.out.open(encoding="utf-8", newline="") as f:
            done = {r["entity_id"] for r in csv.DictReader(f) if r["status"] == "ok"}
    todo = [e for e in sample if e not in done]
    if a.limit:
        todo = todo[:a.limit]

    load_dotenv()
    gen = load_config().generator
    if not (gen.base_url and gen.api_key and gen.model):
        raise SystemExit("generator is not configured")
    area_sys = area_system(cfg)
    stats: Counter = Counter()
    lock = threading.Lock()

    def work(eid: str) -> dict:
        p = papers.get(eid, {"title": "", "abstract": "", "venue": "", "year": ""})
        abstract = (p["abstract"] or "").strip()[:MAX_ABSTRACT_CHARS] or "(no abstract available)"
        # No system label in the prompt: the prelabel must be independent or it cannot measure the system.
        user = f"Venue: {p['venue']} {p['year']}\nTitle: {p['title']}\nAbstract: {abstract}"
        first = call_llm(gen, area_sys, user, 300)
        obj = parse_json(first["content"])
        area = str(obj.get("area", "")).strip()
        row = {"entity_id": eid, "pred_area": area if area in areas else "",
               "pred_area_confidence": str(obj.get("area_confidence", "")).strip(),
               "pred_direction": "", "pred_direction_confidence": "",
               "area_reason": str(obj.get("reason", ""))[:200], "direction_reason": "",
               "model": first["model"], "prompt_version": PROMPT_VERSION,
               "prompt_tokens": first["prompt_tokens"], "cached_tokens": first["cached_tokens"],
               "completion_tokens": first["completion_tokens"], "status": "ok" if area in areas else "invalid_area"}
        if area not in areas:
            return row
        system2 = direction_system(areas[area], by_area[area])
        second = call_llm(gen, system2, user, 300)
        obj2 = parse_json(second["content"])
        valid = {d["direction_id"] for d in by_area[area]}
        direction = str(obj2.get("direction", "")).strip()
        row.update({"pred_direction": direction if direction in valid else "",
                    "pred_direction_confidence": str(obj2.get("confidence", "")).strip(),
                    "direction_reason": str(obj2.get("reason", ""))[:200],
                    "prompt_tokens": row["prompt_tokens"] + second["prompt_tokens"],
                    "cached_tokens": row["cached_tokens"] + second["cached_tokens"],
                    "completion_tokens": row["completion_tokens"] + second["completion_tokens"],
                    "status": "ok" if direction in valid else "invalid_direction"})
        return row

    write_header = not a.out.exists() or a.out.stat().st_size == 0
    with a.out.open("a", encoding="utf-8", newline="") as f, ThreadPoolExecutor(a.concurrency) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        futures = [pool.submit(work, eid) for eid in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            with lock:
                w.writerow(row)
                f.flush()
            stats[row["status"]] += 1
            for k in ("prompt_tokens", "cached_tokens", "completion_tokens"):
                stats[k] += int(row[k] or 0)
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} {dict((k, v) for k, v in stats.items() if not k.endswith('tokens'))}", flush=True)
    print(f"tokens: prompt={stats['prompt_tokens']:,} (cache {stats['cached_tokens']:,}) completion={stats['completion_tokens']:,}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
