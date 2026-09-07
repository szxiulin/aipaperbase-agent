from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.collections.database import initialize


SOURCE_TYPES = {"manual", "filter", "topic", "mixed"}
ADDED_BY = {"manual", "filter", "topic"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_collection_id() -> str:
    return "col_" + secrets.token_hex(6)


def ensure_schema(connection: sqlite3.Connection) -> None:
    initialize(connection)


def current_catalog_release(catalog_conn: sqlite3.Connection) -> str:
    row = catalog_conn.execute(
        "SELECT release_id FROM data_releases WHERE status = 'ready' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("目录数据库没有可用的数据版本")
    return row["release_id"]


def _known_entities(
    catalog_conn: sqlite3.Connection, entity_ids: list[str]
) -> tuple[set[str], dict[str, str]]:
    """Return (current entity ids, alias -> current entity id)."""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return set(), {}
    current: set[str] = set()
    aliases: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        current |= {
            row[0] for row in catalog_conn.execute(
                f"SELECT entity_id FROM paper_entities WHERE entity_id IN ({placeholders})", chunk
            )
        }
        for row in catalog_conn.execute(
            f"SELECT alias_entity_id, entity_id FROM entity_aliases WHERE alias_entity_id IN ({placeholders})", chunk
        ):
            aliases[row["alias_entity_id"]] = row["entity_id"]
    return current, aliases


def resolve_entity_ids(
    catalog_conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, dict[str, str]]:
    """Resolve stored entity ids to their current id and a status.

    Status is ``current`` (still in the catalog), ``alias`` (migrated to a new id
    via ``entity_aliases``), or ``missing`` (no longer resolvable).
    """
    current, aliases = _known_entities(catalog_conn, entity_ids)
    resolved: dict[str, dict[str, str]] = {}
    for entity_id in entity_ids:
        if entity_id in current:
            resolved[entity_id] = {"entity_id": entity_id, "status": "current"}
        elif entity_id in aliases:
            resolved[entity_id] = {"entity_id": aliases[entity_id], "status": "alias"}
        else:
            resolved[entity_id] = {"entity_id": entity_id, "status": "missing"}
    return resolved


def _entity_details(catalog_conn: sqlite3.Connection, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = sorted(set(entity_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = catalog_conn.execute(
        f"""SELECT e.entity_id, e.canonical_title AS title, e.first_year, e.last_year,
                   e.record_count, e.venue_count,
                   p.authors, p.venue, p.venue_type, p.paper_url, p.doi, p.arxiv_id
            FROM paper_entities e
            JOIN paper_records p ON p.record_id = e.canonical_record_id
            WHERE e.entity_id IN ({placeholders})""",
        ids,
    ).fetchall()
    return {row["entity_id"]: dict(row) for row in rows}


def _entity_appearances(
    catalog_conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    ids = sorted(set(entity_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = catalog_conn.execute(
        f"""SELECT m.entity_id, p.venue, p.year, p.list_status, p.paper_url
            FROM entity_memberships m
            JOIN paper_records p ON p.record_id = m.record_id
            WHERE m.entity_id IN ({placeholders})
            ORDER BY m.entity_id, p.year, p.venue""",
        ids,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        entity_id = item.pop("entity_id")
        result.setdefault(entity_id, []).append(item)
    return result


def collection_summary(user_conn: sqlite3.Connection, collection_id: str) -> dict[str, Any]:
    row = user_conn.execute(
        """SELECT c.collection_id, c.name, c.description, c.source_type,
                  c.source_snapshot, c.catalog_release_id, c.created_at, c.updated_at,
                  COUNT(m.entity_id) AS member_count
           FROM collections c
           LEFT JOIN collection_members m ON m.collection_id = c.collection_id
           WHERE c.collection_id = ?
           GROUP BY c.collection_id""",
        (collection_id,),
    ).fetchone()
    if row is None:
        raise KeyError(collection_id)
    return dict(row)


def _require_collection(user_conn: sqlite3.Connection, collection_id: str) -> None:
    row = user_conn.execute(
        "SELECT 1 FROM collections WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    if row is None:
        raise KeyError(collection_id)


def list_collections(user_conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = user_conn.execute(
        """SELECT c.collection_id, c.name, c.description, c.source_type,
                  c.catalog_release_id, c.created_at, c.updated_at,
                  COUNT(m.entity_id) AS member_count
           FROM collections c
           LEFT JOIN collection_members m ON m.collection_id = c.collection_id
           GROUP BY c.collection_id
           ORDER BY c.updated_at DESC, c.created_at DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def collection_entity_ids(user_conn: sqlite3.Connection, collection_id: str) -> list[str]:
    rows = user_conn.execute(
        "SELECT entity_id FROM collection_members WHERE collection_id = ? ORDER BY entity_id",
        (collection_id,),
    ).fetchall()
    return [row["entity_id"] for row in rows]


def create_collection(
    user_conn: sqlite3.Connection,
    *,
    name: str,
    description: str = "",
    source_type: str = "manual",
    source_snapshot: str = "",
    catalog_release_id: str = "",
    entity_ids: list[str] | None = None,
    added_by: str = "manual",
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("集合名称不能为空")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"非法来源类型: {source_type}")
    if added_by not in ADDED_BY:
        raise ValueError(f"非法加入方式: {added_by}")
    collection_id = new_collection_id()
    now = utc_now()
    with user_conn:
        user_conn.execute(
            """INSERT INTO collections
               (collection_id, name, description, source_type, source_snapshot,
                catalog_release_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (collection_id, name, description, source_type, source_snapshot,
             catalog_release_id, now, now),
        )
        for entity_id in dict.fromkeys(entity_ids or []):
            user_conn.execute(
                """INSERT OR IGNORE INTO collection_members
                   (collection_id, entity_id, added_by, added_at)
                   VALUES (?, ?, ?, ?)""",
                (collection_id, entity_id, added_by, now),
            )
    return collection_summary(user_conn, collection_id)


def get_collection(
    user_conn: sqlite3.Connection, catalog_conn: sqlite3.Connection, collection_id: str
) -> dict[str, Any]:
    collection = collection_summary(user_conn, collection_id)
    members = user_conn.execute(
        """SELECT entity_id, added_by, note, added_at
           FROM collection_members WHERE collection_id = ?
           ORDER BY added_at, entity_id""",
        (collection_id,),
    ).fetchall()
    stored_ids = [member["entity_id"] for member in members]
    resolution = resolve_entity_ids(catalog_conn, stored_ids)
    final_ids = [
        info["entity_id"] for info in resolution.values() if info["status"] != "missing"
    ]
    details = _entity_details(catalog_conn, final_ids)
    appearances = _entity_appearances(catalog_conn, final_ids)

    counts = {"current": 0, "alias": 0, "missing": 0}
    items: list[dict[str, Any]] = []
    for member in members:
        entity_id = member["entity_id"]
        info = resolution[entity_id]
        status = info["status"]
        counts[status] += 1
        item: dict[str, Any] = {
            "entity_id": entity_id,
            "resolved_entity_id": info["entity_id"],
            "status": status,
            "added_by": member["added_by"],
            "note": member["note"],
            "added_at": member["added_at"],
        }
        if status != "missing":
            detail = details.get(info["entity_id"], {})
            item.update({
                "title": detail.get("title", ""),
                "authors": detail.get("authors", ""),
                "first_year": detail.get("first_year"),
                "last_year": detail.get("last_year"),
                "venue_count": detail.get("venue_count"),
                "doi": detail.get("doi", ""),
                "arxiv_id": detail.get("arxiv_id", ""),
                "paper_url": detail.get("paper_url", ""),
                "appearances": appearances.get(info["entity_id"], []),
            })
        items.append(item)

    items.sort(key=lambda x: (x["status"] == "missing", -(x.get("last_year") or 0), x.get("title", "")))
    return {"collection": collection, "items": items, "counts": counts, "total": len(items)}


def add_members(
    user_conn: sqlite3.Connection,
    catalog_conn: sqlite3.Connection,
    collection_id: str,
    entity_ids: list[str],
    *,
    added_by: str = "manual",
) -> dict[str, Any]:
    _require_collection(user_conn, collection_id)
    if added_by not in ADDED_BY:
        raise ValueError(f"非法加入方式: {added_by}")
    requested = list(dict.fromkeys(entity_ids))
    if not requested:
        return {"added": 0, "already_present": 0, "unknown": [], "historical_alias_members": []}
    current, aliases = _known_entities(catalog_conn, requested)
    known = current | set(aliases)
    unknown = [entity_id for entity_id in requested if entity_id not in known]
    if unknown:
        preview = ", ".join(unknown[:5])
        raise ValueError(f"存在未知论文实体: {preview}{' 等' if len(unknown) > 5 else ''}")
    # New rows always use the catalog's current canonical ID.  Resolving before
    # deduplication also makes alias + canonical in one request a single intent.
    canonical_ids = list(dict.fromkeys(aliases.get(entity_id, entity_id) for entity_id in requested))
    now = utc_now()
    existing_rows = [row["entity_id"] for row in user_conn.execute(
        "SELECT entity_id FROM collection_members WHERE collection_id = ?", (collection_id,)
    )]
    existing_resolution = resolve_entity_ids(catalog_conn, existing_rows)
    existing_canonical = {
        info["entity_id"] for info in existing_resolution.values() if info["status"] != "missing"
    }
    historical_alias_members = [
        {"stored_entity_id": stored_id, "canonical_entity_id": info["entity_id"]}
        for stored_id, info in existing_resolution.items() if info["status"] == "alias"
        and info["entity_id"] in canonical_ids
    ]
    to_add = [entity_id for entity_id in canonical_ids if entity_id not in existing_canonical]
    with user_conn:
        if to_add:
            user_conn.executemany(
                """INSERT OR IGNORE INTO collection_members
                   (collection_id, entity_id, added_by, added_at)
                   VALUES (?, ?, ?, ?)""",
                [(collection_id, entity_id, added_by, now) for entity_id in to_add],
            )
        user_conn.execute(
            "UPDATE collections SET updated_at = ? WHERE collection_id = ?",
            (now, collection_id),
        )
    return {
        "added": len(to_add), "already_present": len(canonical_ids) - len(to_add), "unknown": [],
        "historical_alias_members": historical_alias_members,
    }


def remove_members(
    user_conn: sqlite3.Connection, collection_id: str, entity_ids: list[str]
) -> dict[str, int]:
    _require_collection(user_conn, collection_id)
    ids = list(dict.fromkeys(entity_ids))
    removed = 0
    if ids:
        placeholders = ",".join("?" for _ in ids)
        now = utc_now()
        with user_conn:
            cursor = user_conn.execute(
                f"""DELETE FROM collection_members
                    WHERE collection_id = ? AND entity_id IN ({placeholders})""",
                [collection_id, *ids],
            )
            removed = cursor.rowcount
            user_conn.execute(
                "UPDATE collections SET updated_at = ? WHERE collection_id = ?",
                (now, collection_id),
            )
    return {"removed": removed}


def update_collection(
    user_conn: sqlite3.Connection,
    collection_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    _require_collection(user_conn, collection_id)
    now = utc_now()
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("集合名称不能为空")
    with user_conn:
        if name is not None:
            user_conn.execute(
                "UPDATE collections SET name = ?, updated_at = ? WHERE collection_id = ?",
                (name, now, collection_id),
            )
        if description is not None:
            user_conn.execute(
                "UPDATE collections SET description = ?, updated_at = ? WHERE collection_id = ?",
                (description, now, collection_id),
            )
    return collection_summary(user_conn, collection_id)


def delete_collection(user_conn: sqlite3.Connection, collection_id: str) -> None:
    _require_collection(user_conn, collection_id)
    with user_conn:
        user_conn.execute("DELETE FROM collections WHERE collection_id = ?", (collection_id,))


def export_rows(
    user_conn: sqlite3.Connection, catalog_conn: sqlite3.Connection, collection_id: str
) -> list[dict[str, Any]]:
    """Resolved members deduplicated by final entity id, missing entries skipped."""
    members = user_conn.execute(
        "SELECT entity_id FROM collection_members WHERE collection_id = ? ORDER BY entity_id",
        (collection_id,),
    ).fetchall()
    resolution = resolve_entity_ids(catalog_conn, [member["entity_id"] for member in members])
    seen: dict[str, dict[str, str]] = {}
    for entity_id, info in resolution.items():
        if info["status"] == "missing":
            continue
        final = info["entity_id"]
        if final not in seen or (seen[final]["status"] == "alias" and info["status"] == "current"):
            seen[final] = info
    final_ids = list(seen)
    details = _entity_details(catalog_conn, final_ids)
    appearances = _entity_appearances(catalog_conn, final_ids)
    rows = [{**details.get(entity_id, {}), "appearances": appearances.get(entity_id, [])}
            for entity_id in final_ids]
    rows.sort(key=lambda row: (row.get("last_year") or 0, row.get("title") or ""), reverse=True)
    return rows
