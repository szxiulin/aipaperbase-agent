"""Evaluate the gold set against the system labels and write metrics JSON.

Gold = the author's review. The system label is the imported assignment; the LLM prelabel is reported
separately as a reference and never called accuracy of the system. Direction macro-F1 is reported only
for directions with at least two gold examples, because 400 samples cannot support 383 classes.
"""
import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


def wilson(k: int, n: int) -> list[float]:
    if not n:
        return [0.0, 1.0]
    z, p = 1.96, k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def prf(gold: list[str], pred: list[str]) -> dict:
    classes = sorted(set(gold) | set(pred))
    tp = fp = fn = 0
    per = {}
    for c in classes:
        g = sum(1 for x in gold if x == c)
        p = sum(1 for x in pred if x == c)
        hit = sum(1 for a, b in zip(gold, pred) if a == c and b == c)
        tp, fp, fn = tp + hit, fp + p - hit, fn + g - hit
        precision = hit / p if p else 0.0
        recall = hit / g if g else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[c] = {"gold": g, "pred": p, "precision": precision, "recall": recall, "f1": f1}
    micro = {"precision": tp / (tp + fp) if tp + fp else 0.0,
             "recall": tp / (tp + fn) if tp + fn else 0.0}
    micro["f1"] = 2 * micro["precision"] * micro["recall"] / (micro["precision"] + micro["recall"]) \
        if micro["precision"] + micro["recall"] else 0.0
    supported = [c for c, v in per.items() if v["gold"] >= 2]
    macro = {k: (sum(per[c][k] for c in supported) / len(supported) if supported else 0.0)
             for k in ("precision", "recall", "f1")}
    return {"micro": {k: round(v, 4) for k, v in micro.items()},
            "macro_support2": {k: round(v, 4) for k, v in macro.items()},
            "macro_classes": len(supported)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", type=Path, default=Path("data/labels/golden_v1.csv"))
    ap.add_argument("--sample", type=Path, default=Path("data/analyses/golden_20261018/sample.csv"))
    ap.add_argument("--out", type=Path, default=Path("data/labels/golden_v1.metrics.json"))
    ap.add_argument("--reviewer", default="author", help="who produced the gold labels (provenance)")
    ap.add_argument("--supplement", type=Path, default=Path("data/analyses/golden_20261018/abstract_supplement.csv"),
                    help="abstracts fetched for review; these papers are not counted as title-only")
    a = ap.parse_args()

    with a.golden.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with a.sample.open(encoding="utf-8", newline="") as f:
        sample = {r["entity_id"]: r for r in csv.DictReader(f)}
    supplemented = set()
    if a.supplement.exists():
        with a.supplement.open(encoding="utf-8", newline="") as f:
            supplemented = {r["entity_id"] for r in csv.DictReader(f) if r.get("abstract")}
    n = len(rows)
    gold_a = [r["gold_area"] for r in rows]
    sys_a = [r["system_area"] for r in rows]
    gold_d = [r["gold_direction"] for r in rows]
    sys_d = [r["system_direction"] for r in rows]
    pred_a = [r["pred_area"] for r in rows]
    pred_d = [r["pred_direction"] for r in rows]

    area_correct = sum(g == p for g, p in zip(gold_a, sys_a))
    strict_correct = sum(g == p and gd == pd for g, p, gd, pd in zip(gold_a, sys_a, gold_d, sys_d))
    area_ok = [(r, gd == pd) for r, g, p, gd, pd in zip(rows, gold_a, sys_a, gold_d, sys_d) if g == p]
    cond_correct = sum(ok for _, ok in area_ok)

    def confusions(gold, pred, key):
        c = Counter((g, p) for g, p in zip(gold, pred) if g != p)
        out = []
        for (g, p), count in c.most_common(10):
            examples = [r["entity_id"] for r, gg, pp in zip(rows, gold, pred) if gg == g and pp == p][:3]
            out.append({key: {"gold": g, "pred": p}, "count": count, "examples": examples})
        return out

    missing = [r for r in rows if sample.get(r["entity_id"], {}).get("has_abstract") == "0"
               and r["entity_id"] not in supplemented]
    reviewed_with_supplement = [r for r in rows if r["entity_id"] in supplemented]
    missing_ok = sum(r["gold_area"] == r["system_area"] for r in missing)
    metrics = {
        "n": n,
        "area": {"correct": area_correct, "accuracy": round(area_correct / n, 4), "ci95": wilson(area_correct, n)},
        "direction": {
            "strict_correct": strict_correct, "strict_accuracy": round(strict_correct / n, 4),
            "conditional_correct": cond_correct, "conditional_denominator": len(area_ok),
            "conditional_accuracy": round(cond_correct / len(area_ok), 4) if area_ok else None,
            **prf(gold_d, sys_d),
        },
        "prelabel": {
            "area_correct": sum(g == p for g, p in zip(gold_a, pred_a)),
            "area_accuracy": round(sum(g == p for g, p in zip(gold_a, pred_a)) / n, 4),
            "direction_strict_correct": sum(g == p and gd == pd for g, p, gd, pd in zip(gold_a, pred_a, gold_d, pred_d)),
            "direction_strict_accuracy": round(sum(g == p and gd == pd for g, p, gd, pd in zip(gold_a, pred_a, gold_d, pred_d)) / n, 4),
        },
        "other_share": {"gold": round(sum(x == "other" for x in gold_a) / n, 4),
                         "system": round(sum(x == "other" for x in sys_a) / n, 4)},
        "missing_abstract": {"n": len(missing), "area_accuracy": round(missing_ok / len(missing), 4) if missing else None},
        "supplemented_abstracts": {"n": len(reviewed_with_supplement),
                                   "area_accuracy": round(sum(r["gold_area"] == r["system_area"] for r in reviewed_with_supplement) / len(reviewed_with_supplement), 4) if reviewed_with_supplement else None},
        "verdicts": dict(Counter(r["verdict"] for r in rows)),
        "confusions": {"area": confusions(gold_a, sys_a, "pair"), "direction": confusions(gold_d, sys_d, "pair")},
        "method": "gold = gold_v1.csv; system = imported areas-v1.5/directions-v1.1; prelabel = DeepSeek reference only",
        "reviewer": a.reviewer,
    }
    a.out.write_text(json.dumps(metrics, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {a.out}: area {metrics['area']['accuracy']:.1%} "
          f"({metrics['area']['ci95']}), direction strict {metrics['direction']['strict_accuracy']:.1%}, "
          f"other {metrics['other_share']['gold']:.1%}")


if __name__ == "__main__":
    main()
