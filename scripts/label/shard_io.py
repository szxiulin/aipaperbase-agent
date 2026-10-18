#!/usr/bin/env python3
"""Mechanical I/O helper for an agent labeling one exported shard.

    python3 scripts/label/shard_io.py next   SHARD_DIR [N]   # print the next N unlabeled papers, compactly
    python3 scripts/label/shard_io.py status SHARD_DIR       # "<labeled>/<total>" plus any problems

`next` prints one block per paper: `## <entity_id> | <venue> <year>`, then the title and
abstract, so a batch of 20 papers costs one tool call. It never assigns labels.
`status` exits 0 only when labels.jsonl covers every paper exactly once with valid JSON.
"""
from __future__ import annotations

import json
import sys
import ast
import fcntl
import re
from pathlib import Path

FIELDS = ('entity_id', 'area', 'area_secondary', 'area_confidence', 'task',
          'method_family', 'datasets', 'modality', 'application', 'contribution_type')
REVIEW_REMINDER = (
    'Review this batch against the frozen guideline before writing: primary area follows the research object '
    'and priority rules. area_secondary defaults to ""; require a distinct claimed cross-area contribution, '
    'not merely a technique, data modality, related topic, or subtask of the primary area. '
    'Techniques belong in method_family. The expected low secondary frequency is not a quota. '
    'Missing abstracts require low confidence.'
)


def vocabulary(shard: Path) -> tuple[set[str], set[str], set[str]]:
    prompt = shard.parent / 'labeling_prompt.md'
    if not prompt.exists():
        config = json.loads((Path(__file__).resolve().parents[2] / 'config/areas.json').read_text())
        return {a['id'] for a in config['areas']}, set(config['modalities']), set(config['contribution_types'])
    text = prompt.read_text(encoding='utf-8')
    areas = set(re.findall(r'^- ([a-z_0-9]+):', text, re.M))
    modalities = set(ast.literal_eval(re.search(r'from (\[[^\n]+\])', text).group(1)))
    kinds = set(ast.literal_eval(re.search(r'one of (\[[^\n]+\])', text).group(1)))
    return areas, modalities, kinds


def validate(row: dict, paper: dict, vocab) -> list[str]:
    areas, modalities, kinds = vocab
    errors = []
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        return ['expected all ten output fields']
    if any(not isinstance(row[k], str) for k in FIELDS if k not in {'method_family', 'datasets', 'modality'}):
        return ['scalar fields must be strings']
    if row['entity_id'] != paper['entity_id']:
        errors.append('entity_id does not match input')
    if row['area'] not in areas:
        errors.append('unknown area')
    if row['area_secondary'] and (row['area_secondary'] not in areas or row['area_secondary'] == row['area']):
        errors.append('invalid secondary area')
    if row['area_confidence'] not in {'high', 'medium', 'low'}:
        errors.append('invalid confidence')
    if paper['abstract'].strip() in {'', '(no abstract available)'} and row['area_confidence'] != 'low':
        errors.append('missing abstract requires low confidence')
    for name, limit in [('method_family', 4), ('datasets', 6), ('modality', 4)]:
        value = row[name]
        if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or not v.strip() for v in value):
            errors.append(f'invalid {name} list')
    if isinstance(row['modality'], list) and (not row['modality'] or any(not isinstance(v, str) or v not in modalities for v in row['modality'])):
        errors.append('invalid modality')
    if isinstance(row['method_family'], list) and any(isinstance(v, str) and v != v.lower() for v in row['method_family']):
        errors.append('method families must be lowercase')
    for name, lower, upper in [('task', 2, 6), ('application', 0, 3)]:
        value = row[name]
        if not isinstance(value, str) or value != value.lower() or not lower <= len(value.split()) <= upper:
            errors.append(f'invalid {name} phrase')
    if row['contribution_type'] not in kinds:
        errors.append('unknown contribution type')
    return errors


def load(shard: Path) -> tuple[list[dict], dict[str, int], list[str]]:
    papers = [json.loads(line) for line in (shard / "papers.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    counts: dict[str, int] = {}
    problems: list[str] = []
    labels = shard / "labels.jsonl"
    by_id = {p['entity_id']: p for p in papers}
    vocab = vocabulary(shard)
    if labels.exists():
        for n, line in enumerate(labels.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                entity_id = row["entity_id"]
                if not isinstance(entity_id, str):
                    raise TypeError('entity_id must be a string')
            except (ValueError, KeyError, TypeError):
                problems.append(f"labels.jsonl line {n}: invalid JSON or missing entity_id")
                continue
            counts[entity_id] = counts.get(entity_id, 0) + 1
            if entity_id in by_id:
                problems.extend(f'line {n}: {message}' for message in validate(row, by_id[entity_id], vocab))
    return papers, counts, problems


def append(shard: Path, payload: str) -> int:
    """Expand human/model-supplied arrays; never infer any label from paper text."""
    batch = json.loads(payload)
    if not isinstance(batch, list) or not batch:
        raise ValueError('expected a nonempty JSON array of label rows')
    with (shard / 'labels.jsonl').open('a+', encoding='utf-8') as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        papers, counts, problems = load(shard)
        if problems:
            raise ValueError('; '.join(problems[:5]))
        vocab = vocabulary(shard)
        lines = []
        seen = set(counts)
        for values in batch:
            if not isinstance(values, list) or len(values) != len(FIELDS):
                raise ValueError('each row needs an ordinal followed by nine explicit label fields')
            ordinal = values[0]
            if type(ordinal) is not int or not 1 <= ordinal <= len(papers):
                raise ValueError('invalid input ordinal')
            paper = papers[ordinal - 1]
            row = dict(zip(FIELDS, [paper['entity_id'], *values[1:]]))
            if row['entity_id'] in seen:
                raise ValueError(f'duplicate entity_id: {row["entity_id"]}')
            errors = validate(row, paper, vocab)
            if errors:
                raise ValueError(f'row {ordinal}: {"; ".join(errors)}')
            seen.add(row['entity_id'])
            lines.append(json.dumps(row, ensure_ascii=False))
        output.write('\n'.join(lines) + '\n')
        output.flush()
    print(f'appended {len(lines)}; total {len(seen)}/{len(papers)}')
    return len(lines)


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in {"next", "status", "append"}:
        sys.exit(__doc__)
    shard = Path(sys.argv[2])
    if sys.argv[1] == 'append':
        try:
            append(shard, sys.stdin.read())
        except (ValueError, TypeError, KeyError) as error:
            sys.exit(str(error))
        return
    papers, counts, problems = load(shard)
    if sys.argv[1] == "next":
        size = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        todo = [(i, p) for i, p in enumerate(papers, 1) if p["entity_id"] not in counts][:size]
        for i, p in todo:
            print(f"## {p['entity_id']} | {p['venue']} {p['year']} | row={i}\nTITLE: {p['title']}\nABSTRACT: {p['abstract']}\n")
        print(f"[{len(todo)} shown; {len(papers) - len(counts.keys() & {q['entity_id'] for q in papers}) - len(todo)} more after these]")
        if todo:
            print(REVIEW_REMINDER)
        return
    wanted = {p["entity_id"] for p in papers}
    problems += [f"duplicate entity_id {k} ({v}x)" for k, v in counts.items() if v > 1]
    problems += [f"unknown entity_id {k}" for k in counts if k not in wanted]
    done = len(wanted & counts.keys())
    print(f"{done}/{len(wanted)}")
    for problem in problems[:20]:
        print(problem)
    sys.exit(0 if done == len(wanted) and not problems else 1)


if __name__ == "__main__":
    main()
