#!/usr/bin/env python3
"""Label papers with a research area and structured attributes via the configured LLM.

One LLM call per paper (title + abstract) returns: primary/secondary area (from
config/areas.json), task, method families, datasets, modalities, application domain
and contribution type. Results are appended to a CSV under data/labels/ that is kept
in version control: labels are expensive to regenerate and are the single source the
catalog import reads.

The run is resumable: entity ids already present in the output CSV are skipped.

Run from the repo root:
    .venv/bin/python scripts/label/label_papers.py --sample 500 --seed 42 \
        --out data/labels/pilot/areas-v1_pilot500.csv
    .venv/bin/python scripts/label/label_papers.py --all --out data/labels/areas-v1.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from raglib.config import load_config, load_dotenv  # noqa: E402

DB = Path("data/database/catalog.sqlite")
AREAS_FILE = Path("config/areas.json")
PROMPT_VERSION = "label-v2"
MAX_ABSTRACT_CHARS = 3000
FIELDS = [
    "entity_id", "area", "area_secondary", "area_confidence", "task", "method_family",
    "datasets", "modality", "application", "contribution_type",
    "model", "prompt_version", "areas_version", "prompt_tokens", "cached_tokens",
    "completion_tokens", "status", "labeled_at",
]
LIST_FIELDS = ("method_family", "datasets", "modality")


def build_system_prompt(cfg: dict) -> str:
    areas = "\n".join(f"- {a['id']}: {a['en']}. {a['scope']}" for a in cfg["areas"])
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(cfg["decision_rules"], 1))
    return f"""You label AI research papers for a bibliometric database. Read the title and abstract and return ONE JSON object.

Research areas (choose ids exactly from this list):
{areas}

Decision rules:
{rules}

JSON fields:
- "area": primary area id.
- "area_secondary": usually "". Fill it ONLY when the paper is a genuine cross-area contribution that the other community would also count as its own core work (e.g. a new graph method for molecules). Using a tool from another area (an LLM, a diffusion model, a transformer) is NOT a reason. Expect fewer than 1 in 4 papers to have one.
- "area_confidence": "high" | "medium" | "low".
- "task": the concrete research problem as a short lowercase English noun phrase (2-6 words), e.g. "image super-resolution", "llm jailbreak defense", "traffic forecasting". Prefer the established community name.
- "method_family": up to 4 lowercase technique families the paper relies on, e.g. ["diffusion", "transformer", "llm", "graph neural network", "reinforcement learning"]. [] if none stated.
- "datasets": datasets or benchmarks explicitly named in the abstract, original spelling, up to 6. [] if none.
- "modality": data modalities, from {cfg["modalities"]}.
- "application": application domain in 1-3 lowercase words if the paper targets one (e.g. "medical imaging", "autonomous driving", "e-commerce"), else "".
- "contribution_type": one of {cfg["contribution_types"]}.

The venue is only a weak hint. Output JSON only, no prose."""


AREA_ONLY_PROMPT_VERSION = "label-v2-area"


def build_area_prompt(cfg: dict) -> str:
    """Area-only relabel: other fields stay as already labeled, so task vectors remain valid."""
    areas = "\n".join(f"- {a['id']}: {a['en']}. {a['scope']}" for a in cfg["areas"])
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(cfg["decision_rules"], 1))
    return f"""You re-check the research AREA of AI papers for a bibliometric database. Read the title and abstract (the previously extracted task phrase is given for context) and return ONE JSON object.

Research areas (choose ids exactly from this list):
{areas}

Decision rules (apply them strictly):
{rules}

JSON fields:
- "area": primary area id.
- "area_secondary": usually "". Fill it ONLY for a genuine cross-area contribution that the other community would also count as its own core work. Using a tool from another area is NOT a reason.
- "area_confidence": "high" | "medium" | "low".

The venue is only a weak hint. Output JSON only, no prose."""


def connect_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_entities(sample: int | None, seed: int) -> list[dict]:
    con = connect_ro()
    try:
        rows = [dict(r) for r in con.execute(
            """SELECT e.entity_id, e.canonical_title AS title, r.abstract, r.venue, r.year
               FROM paper_entities e JOIN paper_records r ON r.record_id = e.canonical_record_id
               ORDER BY e.entity_id"""
        )]
    finally:
        con.close()
    if sample is None:
        return rows
    # Stratify by venue: proportional allocation with a floor of 3 per venue.
    by_venue: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_venue[row["venue"]].append(row)
    rng = random.Random(seed)
    total = len(rows)
    chosen: list[dict] = []
    for venue in sorted(by_venue):
        pool = by_venue[venue]
        k = min(len(pool), max(3, round(sample * len(pool) / total)))
        chosen += rng.sample(pool, k)
    rng.shuffle(chosen)
    return chosen[:sample] if len(chosen) > sample else chosen


def call_llm(gen, system: str, paper: dict, retries: int = 3, context: str = "") -> dict:
    abstract = (paper["abstract"] or "").strip()[:MAX_ABSTRACT_CHARS] or "(no abstract available)"
    user = f"Venue: {paper['venue']} {paper['year']}\nTitle: {paper['title']}\nAbstract: {abstract}{context}"
    payload = {
        "model": gen.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 400,
        "temperature": 0.0,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    last_error = ""
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(f"{gen.base_url.rstrip('/')}/chat/completions",
                                   headers={"Authorization": f"Bearer {gen.api_key}"}, json=payload)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            return {"content": content, "model": data.get("model") or gen.model,
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "cached_tokens": int(usage.get("prompt_cache_hit_tokens")
                                         or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0)}
        except Exception as exc:  # network / HTTP / malformed response: back off and retry
            last_error = str(exc)[:200]
            time.sleep(2 ** attempt)
    raise RuntimeError(last_error)


def normalize(obj: dict, area_ids: set[str], cfg: dict) -> tuple[dict, str]:
    def as_list(value, limit):
        if isinstance(value, str):
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:limit]

    area = str(obj.get("area", "")).strip()
    secondary = str(obj.get("area_secondary", "")).strip()
    status = "ok" if area in area_ids else "invalid_area"
    if secondary not in area_ids or secondary == area:
        secondary = ""
    modality = [m for m in as_list(obj.get("modality"), 4) if m in cfg["modalities"]]
    contribution = str(obj.get("contribution_type", "")).strip()
    return {
        "area": area if status == "ok" else "",
        "area_secondary": secondary,
        "area_confidence": str(obj.get("area_confidence", "")).strip().lower(),
        "task": str(obj.get("task", "")).strip().lower(),
        "method_family": as_list(obj.get("method_family"), 4),
        "datasets": as_list(obj.get("datasets"), 6),
        "modality": modality,
        "application": str(obj.get("application", "")).strip().lower(),
        "contribution_type": contribution if contribution in cfg["contribution_types"] else "",
    }, status


def label_one(gen, system: str, paper: dict, area_ids: set[str], cfg: dict, base: dict | None = None) -> dict:
    """With `base` (a row of an earlier label CSV) only the area fields are relabeled."""
    row = {"entity_id": paper["entity_id"], "prompt_version": AREA_ONLY_PROMPT_VERSION if base else PROMPT_VERSION,
           "areas_version": cfg["version"], "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        result = call_llm(gen, system, paper, context=f"\nExtracted task (context only): {base['task']}" if base else "")
    except RuntimeError as exc:
        return {**row, "status": f"error: {exc}"}
    row.update({k: result[k] for k in ("model", "prompt_tokens", "cached_tokens", "completion_tokens")})
    try:
        obj = json.loads(result["content"][result["content"].find("{"): result["content"].rfind("}") + 1])
    except (ValueError, TypeError):
        return {**row, "status": "invalid_json"}
    fields, status = normalize(obj, area_ids, cfg)
    for key in LIST_FIELDS:
        fields[key] = "|".join(fields[key])
    if base:
        keep = {k: base[k] for k in ("task", "method_family", "datasets", "modality", "application", "contribution_type")}
        fields = {**fields, **keep}
    return {**row, **fields, "status": status}


def export(directory: Path, papers: list[dict], system: str, shard_size: int = 0) -> None:
    """Write papers.jsonl (or shard_NNNN/papers.jsonl when shard_size > 0) plus the prompt."""
    directory.mkdir(parents=True, exist_ok=True)
    size = shard_size or len(papers) or 1
    shards = [papers[i:i + size] for i in range(0, len(papers), size)]
    for n, shard in enumerate(shards, 1):
        target = directory / f"shard_{n:04d}" if shard_size else directory
        target.mkdir(parents=True, exist_ok=True)
        with (target / "papers.jsonl").open("w", encoding="utf-8") as f:
            for p in shard:
                abstract = (p["abstract"] or "").strip()[:MAX_ABSTRACT_CHARS] or "(no abstract available)"
                f.write(json.dumps({"entity_id": p["entity_id"], "venue": p["venue"], "year": p["year"],
                                    "title": p["title"], "abstract": abstract}, ensure_ascii=False) + "\n")
    (directory / "labeling_prompt.md").write_text(system + "\n", encoding="utf-8")
    where = f"{len(shards)} shards of <= {shard_size}" if shard_size else "one file"
    print(f"exported {len(papers)} papers ({where}) and the {PROMPT_VERSION} prompt to {directory}")


def ingest(sources: list[Path], out: Path, model: str, area_ids: set[str], cfg: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    labeled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [line for source in sources for line in source.read_text(encoding="utf-8").splitlines()]
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        base = {"model": model, "prompt_version": PROMPT_VERSION, "areas_version": cfg["version"],
                "labeled_at": labeled_at}
        try:
            obj = json.loads(line)
        except ValueError:
            print(f"  line {n}: invalid JSON, skipped")
            continue
        entity_id = str(obj.get("entity_id", "")).strip()
        if not entity_id or entity_id in seen:
            print(f"  line {n}: missing or duplicate entity_id, skipped")
            continue
        seen.add(entity_id)
        fields, status = normalize(obj, area_ids, cfg)
        for key in LIST_FIELDS:
            fields[key] = "|".join(fields[key])
        rows.append({**base, "entity_id": entity_id, **fields, "status": status})
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"ingested {len(rows)} rows ({Counter(r['status'] for r in rows)}) -> {out}")


def select(args, wanted: set[str] | None) -> list[dict]:
    if wanted is not None:
        return [p for p in load_entities(None, args.seed) if p["entity_id"] in wanted]
    return load_entities(None if args.all else args.sample, args.seed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="stratified sample size")
    group.add_argument("--all", action="store_true", help="label every entity")
    group.add_argument("--ids", metavar="FILE", help="label only the entity ids listed in FILE (one per line)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", help="output CSV (label and --ingest modes)")
    ap.add_argument("--limit", type=int, help="stop after N new papers (for smoke tests)")
    ap.add_argument("--export", metavar="DIR",
                    help="no LLM calls: write the selected papers (papers.jsonl) and the labeling prompt "
                         "(labeling_prompt.md) to DIR, for an external labeler such as a coding agent")
    ap.add_argument("--shard-size", type=int, default=0, help="with --export: split into shard_NNNN/ directories")
    ap.add_argument("--ingest", metavar="JSONL", nargs="+",
                    help="no LLM calls: validate labels produced by an external labeler (one or more JSONL "
                         "files) and write them to --out")
    ap.add_argument("--model", default="", help="model name recorded for --ingest rows")
    ap.add_argument("--area-only", metavar="BASE_CSV", type=Path,
                    help="relabel only area/area_secondary/area_confidence; copy every other field from BASE_CSV")
    args = ap.parse_args()

    cfg = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    area_ids = {a["id"] for a in cfg["areas"]}
    system = build_area_prompt(cfg) if args.area_only else build_system_prompt(cfg)
    base_rows = {}
    if args.area_only:
        with args.area_only.open(encoding="utf-8", newline="") as f:
            base_rows = {r["entity_id"]: r for r in csv.DictReader(f)}

    if args.ingest:
        if not (args.out and args.model):
            sys.exit("--ingest needs --out and --model")
        ingest([Path(p) for p in args.ingest], Path(args.out), args.model, area_ids, cfg)
        return
    if not (args.sample or args.all or args.ids):
        sys.exit("choose --sample N, --all or --ids FILE")
    wanted = set(Path(args.ids).read_text(encoding="utf-8").split()) if args.ids else None
    if args.export:
        export(Path(args.export), select(args, wanted), system,
               args.shard_size)
        return
    if not args.out:
        sys.exit("--out is required")

    load_dotenv()
    gen = load_config().generator
    if not (gen.base_url and gen.api_key and gen.model):
        sys.exit("GENERATOR_BASE_URL / GENERATOR_API_KEY / GENERATOR_MODEL are not configured.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8", newline="") as f:
            done = {r["entity_id"] for r in csv.DictReader(f) if r["status"] == "ok"}
    papers = [p for p in select(args, wanted) if p["entity_id"] not in done]
    if args.limit:
        papers = papers[: args.limit]
    if args.area_only:
        missing = [p["entity_id"] for p in papers if p["entity_id"] not in base_rows]
        if missing:
            sys.exit(f"{len(missing)} papers are not in {args.area_only}")
    print(f"model={gen.model} prompt={AREA_ONLY_PROMPT_VERSION if args.area_only else PROMPT_VERSION} areas={cfg['version']} | to label: {len(papers)} (already ok: {len(done)})")

    lock = threading.Lock()
    write_header = not out.exists() or out.stat().st_size == 0
    stats: Counter = Counter()
    started = time.time()
    with out.open("a", encoding="utf-8", newline="") as f, ThreadPoolExecutor(args.concurrency) as pool:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        futures = [pool.submit(label_one, gen, system, p, area_ids, cfg, base_rows.get(p["entity_id"]) if args.area_only else None)
                   for p in papers]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            with lock:
                writer.writerow(row)
                f.flush()
            stats[row["status"].split(":")[0]] += 1
            for key in ("prompt_tokens", "cached_tokens", "completion_tokens"):
                stats[key] += int(row.get(key) or 0)
            if i % 50 == 0 or i == len(papers):
                print(f"  {i}/{len(papers)}  {dict((k, v) for k, v in stats.items() if not k.endswith('tokens'))}  "
                      f"{time.time() - started:.0f}s", flush=True)

    n = max(1, sum(v for k, v in stats.items() if not k.endswith("tokens")))
    print(f"\ntokens: prompt={stats['prompt_tokens']:,} (cache hit {stats['cached_tokens']:,}) "
          f"completion={stats['completion_tokens']:,} | per paper: prompt {stats['prompt_tokens'] / n:.0f}, "
          f"completion {stats['completion_tokens'] / n:.0f}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
