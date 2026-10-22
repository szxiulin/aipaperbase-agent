"""Persistent identity compatibility without moving or rewriting personal artifacts."""
from __future__ import annotations
import csv
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parents[2] / 'data/labels/entity_aliases.csv'


@lru_cache(maxsize=8)
def _load(path: str, stamp: int, size: int) -> dict[str, str]:
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    result = {}
    for row in rows:
        old, new = row['alias_entity_id'], row['entity_id']
        if not old or not new or old == new or old in result:
            raise ValueError('Invalid entity alias manifest')
        result[old] = new
    for old in result:
        seen = {old}; target = result[old]
        while target in result:
            if target in seen:
                raise ValueError('Entity alias cycle')
            seen.add(target); target = result[target]
        result[old] = target
    return result


def load_aliases(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    stat = path.stat()
    return _load(str(path), stat.st_mtime_ns, stat.st_size).copy()


def aliases() -> dict[str, str]:
    return load_aliases(ALIASES_PATH)


def canonical(entity_id: str) -> str:
    return aliases().get(entity_id, entity_id)


def variants(entity_id: str) -> list[str]:
    mapping = aliases(); target = mapping.get(entity_id, entity_id)
    return list(dict.fromkeys([entity_id, target] + sorted(k for k, v in mapping.items() if v == target)))


def expand(entity_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(v for eid in entity_ids for v in variants(eid)))


def storage_id(connection, table: str, entity_id: str) -> str:
    if table not in {'downloads', 'parsed_documents'}:
        raise ValueError('Unsupported artifact table')
    found = []
    for candidate in variants(entity_id):
        row = connection.execute(f'SELECT status FROM {table} WHERE entity_id=?', (candidate,)).fetchone()
        if row:
            found.append((candidate, row[0]))
    if canonical(entity_id) != entity_id and found and found[0][0] == entity_id:
        return entity_id
    for candidate, status in found:
        if status in {'success', 'duplicate'}:
            return candidate
    return found[0][0] if found else canonical(entity_id)


def references(value):
    """Normalize structured references only; leave text and storage keys intact."""
    if isinstance(value, list):
        return [references(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {'entity_id', 'canonical_entity_id'} and isinstance(item, str):
                result[key] = canonical(item)
            elif key in {'entity_ids', 'baseline', 'allowed_entity_ids'} and isinstance(item, list) and all(isinstance(x, str) for x in item):
                result[key] = list(dict.fromkeys(canonical(x) for x in item))
            else:
                result[key] = references(item)
        return result
    return value
