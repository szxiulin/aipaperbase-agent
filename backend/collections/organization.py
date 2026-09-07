"""Persisted, user-confirmed assignment and evidence-based classification drafts."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from typing import Any

from backend.collections import service


CLASSIFIER_VERSION = "organization-v2-model"
MAX_PAPERS = 100
MAX_COLLECTIONS = 12
MAX_EVIDENCE_CHARS = 500


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rule_hash(rules: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_json(rules).encode("utf-8")).hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _metadata(catalog_conn: sqlite3.Connection, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not entity_ids:
        return {}
    placeholders = ",".join("?" for _ in entity_ids)
    record_cols = _columns(catalog_conn, "paper_records")
    abstract = "p.abstract" if "abstract" in record_cols else "''"
    rows = catalog_conn.execute(
        f"""SELECT e.entity_id, e.canonical_title AS title, {abstract} AS abstract
            FROM paper_entities e JOIN paper_records p ON p.record_id=e.canonical_record_id
            WHERE e.entity_id IN ({placeholders})""", entity_ids).fetchall()
    result = {row["entity_id"]: dict(row) for row in rows}
    if "entity_topic_assignments" in {row["name"] for row in catalog_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for row in catalog_conn.execute(
            f"SELECT entity_id, topic_id FROM entity_topic_assignments WHERE entity_id IN ({placeholders})", entity_ids
        ):
            result.setdefault(row["entity_id"], {}).setdefault("topics", []).append(row["topic_id"])
    if "entity_tech_tags" in {row["name"] for row in catalog_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for row in catalog_conn.execute(
            f"SELECT entity_id, tag FROM entity_tech_tags WHERE entity_id IN ({placeholders})", entity_ids
        ):
            result.setdefault(row["entity_id"], {}).setdefault("tech_tags", []).append(row["tag"])
    return result


def list_organizable_papers(catalog_conn: sqlite3.Connection, reconcile: dict[str, Any], user_conn: sqlite3.Connection,
                            entity_ids: list[str] | None = None, *, allow_all: bool = False, require_current: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return only an explicit scope, plus visible reasons for skipped papers."""
    if entity_ids is None and not allow_all:
        raise ValueError("请明确指定要整理的论文范围")
    requested = list(dict.fromkeys(entity_ids or []))
    if len(requested) > MAX_PAPERS:
        raise ValueError("一次最多整理 100 篇论文，请分批选择")
    items = {item["entity_id"]: item for item in reconcile.get("items", [])}
    if allow_all and entity_ids is None:
        requested = list(items)
    if len(requested) > MAX_PAPERS:
        raise ValueError("一次最多整理 100 篇论文，请分批选择")
    resolution = service.resolve_entity_ids(catalog_conn, requested)
    canonical_ids = list(dict.fromkeys(info["entity_id"] for info in resolution.values() if info["status"] != "missing"))
    current = [items[entity_id] for entity_id in canonical_ids
               if entity_id in items and (not require_current or (items[entity_id].get("index_status") == "indexed_current"
               and items[entity_id].get("actual_chunk_count", 0) > 0))]
    details = _metadata(catalog_conn, [item["entity_id"] for item in current])
    memberships: dict[str, list[str]] = {}
    stored = user_conn.execute("SELECT entity_id, collection_id FROM collection_members").fetchall()
    stored_resolution = service.resolve_entity_ids(catalog_conn, [row["entity_id"] for row in stored])
    for row in stored:
        info = stored_resolution[row["entity_id"]]
        if info["status"] != "missing":
            memberships.setdefault(info["entity_id"], []).append(row["collection_id"])
    papers = [{
        "entity_id": item["entity_id"], "title": details.get(item["entity_id"], {}).get("title", item["title"]),
        "abstract": details.get(item["entity_id"], {}).get("abstract", "")[:1000],
        "topics": details.get(item["entity_id"], {}).get("topics", []),
        "tech_tags": details.get(item["entity_id"], {}).get("tech_tags", []),
        "index_status": item.get("index_status", ""), "chunk_count": item.get("actual_chunk_count", 0), "parsed_sha256": item.get("actual_parsed_sha256", ""),
        "pipeline_fingerprint": item.get("pipeline_fingerprint", ""), "current_collections": memberships.get(item["entity_id"], []),
    } for item in current]
    current_ids = {item["entity_id"] for item in current}
    skipped = []
    for original in requested:
        info = resolution[original]
        if info["status"] == "missing":
            skipped.append({"entity_id": original, "reason": "论文实体已不存在，无法整理。"})
        elif info["entity_id"] not in current_ids:
            state = items.get(info["entity_id"], {}).get("index_status", "未找到本地论文记录")
            skipped.append({"entity_id": info["entity_id"], "reason": f"索引当前不可用于全文分类（{state}）。"})
    return papers, skipped


def _decision(collection, paper, evidence, rule="", model_decision=None):
    decision = model_decision or {}
    owned = [x for x in evidence if x.get("entity_id") == paper["entity_id"] and x.get("text_preview")]
    if (paper.get("index_status") != "indexed_current" or not rule.strip() or decision.get("rule") != rule or not owned
            or not str(decision.get("reason", "")).strip()
            or decision.get("decision") not in {"include", "exclude", "review"}):
        return "review", 0.0, "缺少规则对应的模型判断或可核实内容证据，请复核。", owned
    return decision["decision"], max(0, min(1, float(decision.get("confidence", 0)))), str(decision["reason"]), owned


def propose(
    user_conn: sqlite3.Connection, catalog_conn: sqlite3.Connection, reconcile: dict[str, Any], *,
    collection_ids: list[str], entity_ids: list[str] | None = None, allow_all: bool = False,
    conversation_id: str = "", rules: dict[str, str] | None = None,
    evidence_by_entity: dict[str, list[dict[str, Any]]] | None = None,
    decisions: list[dict] | None = None, operation: str = "classify", target_name: str = "",
) -> dict[str, Any]:
    if operation not in {"assign", "classify"}:
        raise ValueError("未知整理操作")
    collection_ids = list(dict.fromkeys(collection_ids))
    pending = None
    if target_name.strip():
        if operation != "assign" or collection_ids:
            raise ValueError("新集合仅用于指定加入，不能同时指定集合 ID")
        matches = user_conn.execute("SELECT collection_id FROM collections WHERE name=?", (target_name.strip(),)).fetchall()
        if len(matches) > 1:
            raise ValueError("存在同名集合，请指定集合 ID")
        if matches:
            collection_ids = [matches[0]["collection_id"]]
        else:
            pending = {"collection_id": "col_" + secrets.token_hex(8), "name": target_name.strip(), "description": ""}
            collection_ids = [pending["collection_id"]]
    if not collection_ids or len(collection_ids) > MAX_COLLECTIONS:
        raise ValueError("目标集合数量必须为 1 至 12 个")
    collections = [pending] if pending else [service.collection_summary(user_conn, collection_id) for collection_id in collection_ids]
    papers, skipped = list_organizable_papers(catalog_conn, reconcile, user_conn, entity_ids, allow_all=allow_all, require_current=False)
    evidence_by_entity = evidence_by_entity or {}
    rules = rules or {}
    snapshot_rules = [{"collection_id": c["collection_id"], "name": c["name"], "description": c["description"],
                       "rule": rules.get(c["collection_id"], c["description"])} for c in collections]
    model_pairs = {(d.get("entity_id"), d.get("collection_id")): d for d in decisions or []}
    suggestions = []
    for paper in papers:
        for collection in collections:
            decision, confidence, reason, evidence = _decision(
                collection, paper, evidence_by_entity.get(paper["entity_id"], []),
                rules.get(collection["collection_id"], collection.get("description", "")),
                model_pairs.get((paper["entity_id"], collection["collection_id"])),
            )
            if operation == "assign":
                decision, confidence, reason, evidence = "include", 1.0, "用户指定集合归属；索引状态不限制关联。", []
            suggestions.append({
                "entity_id": paper["entity_id"], "title": paper["title"], "collection_id": collection["collection_id"],
                "collection_name": collection["name"], "decision": decision, "confidence": confidence,
                "reason": reason, "evidence": [{**item, "text_preview": str(item.get("text_preview") or item.get("text") or "")[:MAX_EVIDENCE_CHARS]}
                                               for item in evidence],
                "already_present": collection["collection_id"] in paper["current_collections"],
                "parsed_sha256": paper["parsed_sha256"], "pipeline_fingerprint": paper["pipeline_fingerprint"],
                "actual_chunk_count": paper["chunk_count"],
            })
    run_id = "org_" + secrets.token_hex(8)
    now = service.utc_now()
    with user_conn:
        user_conn.execute(
            """INSERT INTO collection_organization_runs
               (run_id, conversation_id, target_collection_ids_json, rule_snapshot_json, suggestions_json, status, classifier_version, created_at)
               VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
            (run_id, conversation_id, _json(collection_ids), _json({"rules": snapshot_rules, "hash": _rule_hash(snapshot_rules), "operation": operation, "pending_collection": pending, "scope": [p["entity_id"] for p in papers], "skipped": skipped}),
             _json(suggestions), CLASSIFIER_VERSION, now),
        )
    return get_run(user_conn, run_id)


def get_run(user_conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = user_conn.execute("SELECT * FROM collection_organization_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    result = dict(row)
    result["target_collection_ids"] = json.loads(result.pop("target_collection_ids_json"))
    result["rule_snapshot"] = json.loads(result.pop("rule_snapshot_json"))
    result["suggestions"] = json.loads(result.pop("suggestions_json"))
    result["result"] = json.loads(result.pop("result_json") or "{}")
    result["organization_run_id"] = run_id
    result["operation"] = result["rule_snapshot"].get("operation", "classify")
    result["rules"] = result["rule_snapshot"]["rules"]
    result["scope"] = result["rule_snapshot"].get("scope", [])
    result["skipped"] = result["rule_snapshot"].get("skipped", [])
    result["pending_collection"] = result["rule_snapshot"].get("pending_collection")
    suggestions = result["suggestions"]
    result["summary"] = {"suggested_add": sum(x["decision"] == "include" and not x["already_present"] for x in suggestions),
                         "already_present": sum(x["already_present"] for x in suggestions),
                         "review": sum(x["decision"] == "review" for x in suggestions)}
    return result


def apply(user_conn: sqlite3.Connection, catalog_conn: sqlite3.Connection, reconcile: dict[str, Any], *, run_id: str,
          selected: list[dict[str, str]]) -> dict[str, Any]:
    current = {item["entity_id"]: item for item in reconcile.get("items", [])}
    pairs = list(dict.fromkeys((str(x.get("entity_id", "")), str(x.get("collection_id", ""))) for x in selected))
    expired = False
    with user_conn:
        user_conn.execute("BEGIN IMMEDIATE")
        run = get_run(user_conn, run_id)
        if run["status"] != "draft":
            # The persisted result remains available through get_run; a repeat
            # request itself did not add any new relationship.
            return {"run_id": run_id, "status": run["status"], "added": 0, "already_present": 0,
                    "result": run.get("result", {})}
        allowed = {(x["entity_id"], x["collection_id"]): x for x in run["suggestions"] if x["decision"] in {"include", "review"}}
        if any(pair not in allowed for pair in pairs):
            raise ValueError("确认项不属于已保存的分类计划")
        pending = run.get("pending_collection")
        for rule in run["rules"]:
            if pending and rule["collection_id"] == pending["collection_id"]:
                if user_conn.execute("SELECT 1 FROM collections WHERE name=?", (pending["name"],)).fetchone():
                    raise ValueError("目标同名集合已创建，请重新生成草稿")
            else:
                live = service.collection_summary(user_conn, rule["collection_id"])
                if live["name"] != rule["name"] or live["description"] != rule["description"]:
                    raise ValueError("集合规则已变化，请重新生成草稿")
        for entity_id, collection_id in pairs:
            identity = service.resolve_entity_ids(catalog_conn, [entity_id])[entity_id]
            if identity["status"] == "missing" or identity["entity_id"] != entity_id:
                raise ValueError("论文标识已变化，请重新生成草稿")
            if run["operation"] == "assign":
                continue
            suggestion, item = allowed[(entity_id, collection_id)], current.get(entity_id)
            changed = (item is None or item.get("actual_parsed_sha256") != suggestion["parsed_sha256"]
                       or item.get("pipeline_fingerprint") != suggestion["pipeline_fingerprint"]
                       or item.get("actual_chunk_count") != suggestion["actual_chunk_count"])
            if changed:
                result = {"added": 0, "already_present": 0,
                          "not_applied": [{"entity_id": entity_id, "reason": "索引状态已变化"}]}
                user_conn.execute("UPDATE collection_organization_runs SET status='expired', result_json=? WHERE run_id=?",
                                  (_json(result), run_id))
                expired = True
                break
            service._require_collection(user_conn, collection_id)
        if expired:
            pass
        else:
            if pending and pairs:
                now = service.utc_now()
                user_conn.execute("""INSERT INTO collections
                    (collection_id,name,description,source_type,catalog_release_id,created_at,updated_at)
                    VALUES (?,?,?,'manual',?,?,?)""",
                    (pending["collection_id"],pending["name"],pending["description"],service.current_catalog_release(catalog_conn),now,now))
            added = already = 0; details = []
            for entity_id, collection_id in pairs:
                stored = user_conn.execute("SELECT entity_id FROM collection_members WHERE collection_id=?", (collection_id,)).fetchall()
                known = service.resolve_entity_ids(catalog_conn, [row["entity_id"] for row in stored])
                if entity_id in {info["entity_id"] for info in known.values() if info["status"] != "missing"}:
                    already += 1; details.append({"entity_id": entity_id, "collection_id": collection_id, "status": "already_present"})
                else:
                    user_conn.execute("INSERT INTO collection_members (collection_id, entity_id, added_by, added_at) VALUES (?, ?, 'manual', ?)",
                                      (collection_id, entity_id, service.utc_now()))
                    user_conn.execute("UPDATE collections SET updated_at=? WHERE collection_id=?", (service.utc_now(), collection_id))
                    added += 1; details.append({"entity_id": entity_id, "collection_id": collection_id, "status": "added"})
            result = {"added": added, "already_present": already, "details": details, "not_applied": []}
            user_conn.execute("UPDATE collection_organization_runs SET status='applied', applied_at=?, result_json=? WHERE run_id=?",
                              (service.utc_now(), _json(result), run_id))
    if expired:
        raise ValueError("论文索引状态已变化，分类计划已过期，请重新生成")
    return {"run_id": run_id, "status": "applied", **result}
