"""Propose display names for directions that still carry 待命名/待分流 placeholder names.

Reads the frozen draft (top tasks + representative titles) and asks the configured LLM for a concise
Chinese name, an English name and a one-sentence definition. Writes a proposals CSV plus an overlay
JSON consumed by build_config.py; it never edits labels or paper assignments.
"""
import argparse
import csv
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
from raglib.config import load_config, load_dotenv  # noqa: E402

PROMPT_VERSION = "direction-name-v1"
FIELDS = ["direction_id", "area_id", "old_name", "name_zh", "name_en", "definition", "status"]


def system_prompt(area_name: str, area_id: str) -> str:
    return f"""You name research directions inside the area "{area_name}" ({area_id}) for a paper-library UI.

Given the most common task phrases and representative paper titles, return ONE JSON object:
- "name_zh": a concise Chinese direction name, 4-14 Chinese characters, no punctuation, no "待", no "其他", specific enough to distinguish it from sibling directions.
- "name_en": 2-6 English words.
- "definition": one Chinese sentence (<=45 characters) describing the shared research object, not the method.

Name the research object (what is studied), not the technique. Output JSON only."""


def call(gen, system: str, user: str, retries: int = 3) -> dict:
    payload = {"model": gen.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": 200, "temperature": 0.2, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}}
    last = ""
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=90) as client:
                r = client.post(f"{gen.base_url.rstrip('/')}/chat/completions",
                                headers={"Authorization": f"Bearer {gen.api_key}"}, json=payload)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as exc:
            last = str(exc)[:160]
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def clean(name: str) -> str:
    name = re.sub(r"^[：:\s]+|[：:\s]+$", "", str(name or ""))
    return re.sub(r"[，。、；;,.!?！？]", "", name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--clusters", type=Path, default=Path("data/analyses/directions_draft_20261018_v4/draft/named_clusters.json"))
    ap.add_argument("--areas", type=Path, default=Path("config/areas.json"))
    ap.add_argument("--out", type=Path, default=Path("data/analyses/directions_draft_20261018_v4/direction_name_proposals.csv"))
    ap.add_argument("--overlay", type=Path, default=Path("config/direction_names.json"))
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text())["directions"]
    clusters = {c["id"]: c for c in json.loads(a.clusters.read_text())}
    area_name = {x["id"]: x["zh"] for x in json.loads(a.areas.read_text())["areas"]}
    todo = [d for d in cfg if d["name_zh"].startswith("待")]
    done = {}
    if a.out.exists():
        with a.out.open(encoding="utf-8", newline="") as f:
            done = {r["direction_id"]: r for r in csv.DictReader(f) if r["status"] == "ok"}
    todo = [d for d in todo if d["direction_id"] not in done]
    print(f"placeholder directions: {len(cfg and [d for d in cfg if d['name_zh'].startswith('待')])}, to name: {len(todo)}")

    load_dotenv()
    gen = load_config().generator
    if not (gen.base_url and gen.api_key and gen.model):
        raise SystemExit("generator is not configured")
    lock = threading.Lock()
    new_file = not a.out.exists()
    a.out.parent.mkdir(parents=True, exist_ok=True)

    def work(d: dict) -> dict:
        c = clusters.get(d["direction_id"], {})
        tasks = [t["task"] for t in c.get("top_tasks", [])[:8]]
        titles = [p.get("title", "") for p in c.get("representatives", [])[:5]]
        system = system_prompt(area_name.get(d["area"], d["area"]), d["area"])
        user = (f"Direction id: {d['direction_id']}\nCommon tasks: {'; '.join(tasks)}\n"
                f"Representative titles:\n" + "\n".join(f"- {t}" for t in titles))
        obj = call(gen, system, user)
        name = clean(obj.get("name_zh", ""))
        return {"direction_id": d["direction_id"], "area_id": d["area"], "old_name": d["name_zh"],
                "name_zh": name, "name_en": clean(obj.get("name_en", "")),
                "definition": str(obj.get("definition", "")).strip()[:90],
                "status": "ok" if name and not name.startswith("待") else "invalid"}

    with a.out.open("a", encoding="utf-8", newline="") as f, ThreadPoolExecutor(a.concurrency) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for fut in as_completed([pool.submit(work, d) for d in todo]):
            row = fut.result()
            with lock:
                w.writerow(row); f.flush()
            print(f"  {row['status']:7s} {row['direction_id']:28s} {row['name_zh']}")

    with a.out.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["status"] == "ok"]
    # flag duplicates inside an area (the author/next pass can rename them)
    dup = Counter((r["area_id"], r["name_zh"]) for r in rows)
    for key, n in dup.items():
        if n > 1:
            print(f"warning: duplicate name {key}")
    overlay = {r["direction_id"]: {"name_zh": r["name_zh"], "name_en": r["name_en"], "definition": r["definition"]}
               for r in rows}
    a.overlay.write_text(json.dumps(overlay, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {len(rows)} names -> {a.overlay}")


if __name__ == "__main__":
    main()
