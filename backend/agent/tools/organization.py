"""Read/propose-only tools for multi-label collection organization."""
from __future__ import annotations

import re

from backend.agent.tools.base import ToolDef, ToolResult
from backend.collections import organization
from backend.library import local_status


def _check_scope(ctx, allow_all):
    if allow_all and ctx.get("query") and (re.search(r"不要|别|不必|仅|只|not|except|only", ctx["query"], re.I) or not re.search(r"(?:所有|全部|全库|all)", ctx["query"], re.I)):
        raise ValueError("用户未明确指定全部论文，请保留明确范围")


def _reconcile(ctx: dict) -> dict:
    return local_status.snapshot(
        ctx["catalog_conn"], downloads_conn=ctx.get("downloads_conn"), parsed_conn=ctx.get("parsed_conn"),
        document_chunk_counts=ctx.get("document_chunk_counts"), pipeline_fingerprint=ctx.get("pipeline_fingerprint", ""),
    )


def _list(ctx: dict) -> ToolResult:
    conn = ctx.get("collections_conn")
    if conn is None:
        return ToolResult(False, {"error": "集合库不可用"}, summary="集合库不可用")
    # Listing may explicitly request all current papers; generating a draft may not.
    items, _ = organization.list_organizable_papers(ctx["catalog_conn"], _reconcile(ctx), conn, allow_all=True)
    return ToolResult(True, {"items": items, "total": len(items)}, summary=f"可整理的当前有效索引论文 {len(items)} 篇")


def _propose(ctx: dict, collection_ids: list[str], entity_ids: list[str] | None = None,
             allow_all: bool = False, rules: dict | None = None, decisions: list[dict] | None = None) -> ToolResult:
    _check_scope(ctx, allow_all)
    if ctx.get("allowed_entity_ids") is not None:
        allowed = ctx["allowed_entity_ids"]
        if entity_ids is not None and set(entity_ids)-set(allowed): raise ValueError("论文超出当前研究范围")
        entity_ids = entity_ids if entity_ids is not None else allowed
        allow_all = False
    # The chat read connection remains read-only.  This narrowly scoped write
    # connection is used solely to persist the confirmation draft; the tool has
    # no route to collection-member writes.
    conn = ctx.get("organization_draft_conn")
    if conn is None and ctx.get("organization_draft_factory"):
        conn = ctx["organization_draft_factory"]()
        ctx["organization_draft_conn"] = conn
    if conn is None:
        return ToolResult(False, {"error": "集合库不可用"}, summary="集合库不可用")
    # Evidence supplied by a model is accepted only when its chunk ID exists in
    # that exact paper's local vector document.  This prevents cross-paper or
    # fabricated citations from becoming an automatic inclusion rationale.
    verified: dict[str, list[dict]] = {}
    store = getattr(ctx.get("pipeline"), "store", None)
    for decision in decisions or []:
        entity_id = str(decision.get("entity_id", ""))
        if not entity_id:
            continue
        known = {chunk.id: chunk for chunk in store.list_by_document(entity_id)} if store is not None else {}
        evidence = [{"entity_id": entity_id, "chunk_id": item["chunk_id"],
                     "text_preview": known[item["chunk_id"]].text[:500]}
                    for item in decision.get("evidence", [])
                    if item.get("entity_id") == entity_id and item.get("chunk_id") in known]
        if evidence:
            verified[entity_id] = evidence
    result = organization.propose(conn, ctx["catalog_conn"], _reconcile(ctx), collection_ids=collection_ids,
                                  entity_ids=entity_ids, allow_all=allow_all,
                                  conversation_id=ctx.get("conversation_id", ""), rules=rules,
                                  evidence_by_entity=verified, decisions=decisions)
    return ToolResult(True, result, summary=f"已生成待确认分类计划 {result['organization_run_id']}；未写入集合成员")


def _assign(ctx, collection_ids=None, entity_ids=None, allow_all=False, target_name=""):
    _check_scope(ctx, allow_all)
    if ctx.get("allowed_entity_ids") is not None:
        allowed = ctx["allowed_entity_ids"]
        if entity_ids is not None and set(entity_ids)-set(allowed): raise ValueError("论文超出当前研究范围")
        entity_ids = entity_ids if entity_ids is not None else allowed
        allow_all = False
    conn = ctx.get("organization_draft_conn")
    if conn is None and ctx.get("organization_draft_factory"):
        conn = ctx["organization_draft_factory"]()
        ctx["organization_draft_conn"] = conn
    if conn is None:
        return ToolResult(False, {"error": "集合库不可用"})
    result = organization.propose(conn, ctx["catalog_conn"], _reconcile(ctx),
        collection_ids=collection_ids or [], entity_ids=entity_ids, allow_all=allow_all,
        target_name=target_name, operation="assign", conversation_id=ctx.get("conversation_id", ""))
    return ToolResult(True, result, summary="指定加入草稿已保存，等待用户确认；未写入集合成员")


def organization_tools() -> list[ToolDef]:
    return [
        ToolDef("propose_collection_membership", "用户指定加入集合：不分类、不下载、不解析、不调用 embedding。先用 local_fulltext_status 确定范围。仅明确全部时 allow_all=true；目标不存在用 target_name 生成创建并加入草稿。",
                {"type": "object", "properties": {"collection_ids": {"type": "array", "items": {"type": "string"}},
                 "entity_ids": {"type": "array", "items": {"type": "string"}}, "allow_all": {"type": "boolean"},
                 "target_name": {"type": "string"}}}, _assign, "organization"),
        ToolDef("list_organizable_papers", "只读列出已确认当前有效索引、可整理到集合的论文；不返回整篇全文。",
                {"type": "object", "properties": {}, "required": []}, _list, "organization"),
        ToolDef("propose_collection_assignments", "智能分类：先读取集合 description 规则与 get_evidence/search_evidence 内容。decisions 每项必须含 entity_id、collection_id、decision(include/exclude/review)、confidence、reason、rule(实际使用的完整规则)、evidence[{entity_id,chunk_id}]。无证据待复核。仅保存草稿，用户确认后应用。",
                {"type": "object", "properties": {"collection_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                                                     "entity_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                                                     "allow_all": {"type": "boolean"}, "rules": {"type": "object"},
                                                     "decisions": {"type": "array", "items": {"type": "object"}}}, "required": ["collection_ids"]}, _propose, "organization"),
    ]
