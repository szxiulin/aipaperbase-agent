"""LLM routing benchmark: let the model choose one capability per question, execute it, compare with SQL.

This measures the end-to-end analysis path the product uses: natural language -> tool choice + arguments
-> capability result. It is separate from run_analysis_benchmark.py (capability = SQL correctness).

Run: .venv/bin/python scripts/benchmark/run_llm_routing_benchmark.py [--limit N]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.capabilities import REGISTRY  # noqa: E402
from raglib.config import load_config, load_dotenv  # noqa: E402

_spec = importlib.util.spec_from_file_location("analysis_bench", Path(__file__).with_name("run_analysis_benchmark.py"))
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

SYSTEM = """You are a research-data analyst for a local paper library. Choose EXACTLY ONE tool call that answers the question.
Rules:
- Use the tool schema literally; area ids, direction ids, venue names and years must come from the question.
- Do not answer in prose; call one tool.
- Counts are canonical: one year/venue per paper, non-paper records excluded."""


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": c["name"], "description": c["description"],
                                              "parameters": c["parameters"]}}
            for c in REGISTRY.list()]


def call_model(gen, question: str) -> dict:
    payload = {"model": gen.model,
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
               "tools": tool_schemas(), "tool_choice": "auto", "temperature": 0.0,
               "max_tokens": 500, "thinking": {"type": "disabled"}}
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
            return {"message": data["choices"][0]["message"], "usage": usage,
                    "model": data.get("model") or gen.model}
        except Exception as exc:  # noqa: BLE001 - retry then record
            last = str(exc)[:200]
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--questions", type=Path, default=Path("data/benchmarks/analysis_questions_v1.json"))
    ap.add_argument("--out", type=Path, default=Path("output/benchmarks/analysis_llm_routing_v1.json"))
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    spec = json.loads(a.questions.read_text(encoding="utf-8"))
    questions = spec["questions"][:a.limit] if a.limit else spec["questions"]
    load_dotenv()
    gen = load_config().generator
    if not (gen.base_url and gen.api_key and gen.model):
        raise SystemExit("generator is not configured")
    conn = sqlite3.connect(f"file:{a.database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    results = []
    totals = {"prompt_tokens": 0, "completion_tokens": 0}
    for q in questions:
        entry = {"id": q["id"], "question": q["question"], "expected_capability": q["capability"],
                 "allowed_capabilities": q.get("allowed_capabilities") or [q["capability"]]}
        started = time.time()
        try:
            reply = call_model(gen, q["question"])
            message = reply["message"]
            calls = message.get("tool_calls") or []
            usage = reply["usage"]
            totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            entry.update({"model": reply["model"], "latency_s": round(time.time() - started, 2),
                          "tool_calls": len(calls),
                          "tokens": {"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens")}})
            if not calls:
                entry.update({"pass": False, "error": "model returned no tool call", "content": (message.get("content") or "")[:200]})
                results.append(entry)
                continue
            call = calls[0]
            name = call["function"]["name"]
            raw_args = call["function"].get("arguments") or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            entry.update({"called_capability": name, "called_arguments": args})
            allowed = q.get("allowed_capabilities") or [q["capability"]]
            capability_ok = name in allowed
            result = REGISTRY.call(conn, name, args)
            if not result.ok:
                entry.update({"pass": False, "capability_match": capability_ok, "error": result.error})
                results.append(entry)
                continue
            try:
                actual = bench.actual_value(result, q["check"], q)
                expected = bench.expected_value(conn, q)
                result_ok = actual == expected
            except Exception as exc:  # noqa: BLE001
                entry.update({"pass": False, "capability_match": capability_ok, "error": str(exc)})
                results.append(entry)
                continue
            entry.update({"pass": bool(capability_ok and result_ok), "capability_match": capability_ok,
                          "result_match": result_ok, "actual": actual, "expected": expected})
            results.append(entry)
            print(f"  {q['id']} {'PASS' if entry['pass'] else 'FAIL'} {name} {'match' if capability_ok else 'wrong-tool'}")
        except Exception as exc:  # noqa: BLE001
            entry.update({"pass": False, "error": str(exc)[:300]})
            results.append(entry)

    passed = sum(1 for r in results if r["pass"])
    tool_match = sum(1 for r in results if r.get("capability_match"))
    scorecard = {"version": spec["version"], "mode": "llm-routing", "model": gen.model,
                 "total": len(results), "passed": passed, "score": round(passed / len(results), 4) if results else 0,
                 "tool_match": tool_match, "tool_match_rate": round(tool_match / len(results), 4) if results else 0,
                 "tokens": totals,
                 "estimated_cost_usd": round(totals["prompt_tokens"] / 1e6 * 0.14
                                             + totals["completion_tokens"] / 1e6 * 0.28, 4),
                 "results": results}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(scorecard, ensure_ascii=False, indent=1) + "\n")
    conn.close()
    print(f"llm routing: {passed}/{len(results)} pass; tool match {tool_match}/{len(results)}; "
          f"tokens {totals}; est ${scorecard['estimated_cost_usd']} -> {a.out}")


if __name__ == "__main__":
    main()
