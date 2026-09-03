from __future__ import annotations

"""Full-text tools: search_evidence (full-text evidence retrieval), get_evidence (deep dive into one paper), list_collections (collections).

Implementation wraps the raglib retriever (embed → Qdrant filtered retrieval → rerank). ctx must contain pipeline (lazily loaded).
"""

from backend.agent.tools import schemas
from backend.agent.tools.base import ToolDef, ToolResult


def _resolve_entity_ids(ctx: dict, entity_ids: list[str] | None, collection_id: str | None) -> list[str]:
    ids = list(dict.fromkeys(entity_ids or []))
    if collection_id:
        collections_conn = ctx.get("collections_conn")
        if collections_conn is not None:
            rows = collections_conn.execute(
                "SELECT entity_id FROM collection_members WHERE collection_id = ?", (collection_id,)
            ).fetchall()
            ids.extend(row["entity_id"] for row in rows)
    return list(dict.fromkeys(ids))


def _ensure_pipeline(ctx: dict):
    """Lazily load the raglib pipeline: only connect to Qdrant when actually calling full-text tools;
    catalog tools (search_papers/list_facets/get_paper) don't depend on the full-text index, so Qdrant being unavailable doesn't affect them."""
    pipeline = ctx.get("pipeline")
    if pipeline is not None:
        return pipeline
    from backend.rag import service as rag_service

    pipeline = rag_service.get_pipeline()
    ctx["pipeline"] = pipeline
    return pipeline


def _search_evidence(ctx: dict, query: str, entity_ids: list[str] | None = None,
                     collection_id: str | None = None, top_k: int = 10) -> ToolResult:
    try:
        pipeline = _ensure_pipeline(ctx)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"全文库暂不可用：{exc}"}, summary="全文库暂不可用")
    filters = None
    resolved = _resolve_entity_ids(ctx, entity_ids, collection_id)
    if resolved:
        filters = {"document_id": resolved}
    try:
        hits = pipeline.retriever.retrieve_scored(query, top_k, filters)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"全文检索失败：{exc}"}, summary="全文检索失败")
    if not hits:
        scope = "限定论文内" if resolved else "全文库"
        return ToolResult(
            ok=True, data={"items": [], "total": 0},
            summary=f"{scope}未检索到相关内容",
        )
    compact = []
    provenance = []
    for chunk, score in hits:
        metadata = chunk.metadata
        compact.append({
            "chunk_id": chunk.id,
            "entity_id": metadata.get("entity_id", chunk.document_id),
            "title": metadata.get("title", ""),
            "section": metadata.get("section", ""),
            "text": chunk.text[:400],
            "score": round(float(score), 3),
        })
        provenance.append({
            "chunk_id": chunk.id,
            "entity_id": metadata.get("entity_id", chunk.document_id),
            "title": metadata.get("title", ""),
            "section": metadata.get("section", ""),
            "text": chunk.text[:600],
            "score": round(float(score), 3),
            "source": "fulltext",
        })
    return ToolResult(
        ok=True,
        data={"items": compact, "total": len(compact)},
        provenance=provenance,
        summary=f"检索到 {len(compact)} 条证据片段",
    )


def _get_evidence(ctx: dict, entity_id: str) -> ToolResult:
    """Section structure map: list all ingested sections of this paper + the first snippet of each section (to survey the structure before deep diving)."""
    try:
        pipeline = _ensure_pipeline(ctx)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"全文库暂不可用：{exc}", "hint": "请先启动 Qdrant"}, summary="全文库暂不可用")
    try:
        chunks = pipeline.store.list_by_document(entity_id)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"章节读取失败：{exc}"}, summary="章节读取失败")
    if not chunks:
        return ToolResult(
            ok=False,
            data={"error": "该论文未入库全文", "hint": "可先用 ingest_papers 把它下载解析入库后再追问"},
            summary="未入库",
        )
    # Group by section, keeping the first snippet of each
    groups: dict[str, Chunk] = {}
    for chunk in chunks:
        section = chunk.metadata.get("section") or ""
        if section not in groups:
            groups[section] = chunk
    sections = [
        {
            "section": section or "（未分段）",
            "text": chunk.text[:220],
            "chunk_count": sum(1 for c in chunks if (c.metadata.get("section") or "") == section),
            "first_chunk_id": chunk.id,
        }
        for section, chunk in groups.items()
    ]
    title = chunks[0].metadata.get("title") or entity_id
    return ToolResult(
        ok=True,
        data={"entity_id": entity_id, "title": title, "section_count": len(sections), "sections": sections},
        summary=f"论文 {entity_id} 有 {len(sections)} 个章节",
    )


def _list_collections(ctx: dict) -> ToolResult:
    conn = ctx.get("collections_conn")
    if conn is None:
        return ToolResult(ok=False, data={"error": "集合库不可用"}, summary="集合库不可用")
    rows = conn.execute(
        """SELECT c.collection_id, c.name, c.description,
                  (SELECT COUNT(*) FROM collection_members m WHERE m.collection_id = c.collection_id) AS member_count
           FROM collections c ORDER BY c.updated_at DESC"""
    ).fetchall()
    items = [dict(row) for row in rows]
    return ToolResult(
        ok=True, data={"items": items},
        summary=f"共 {len(items)} 个集合",
    )


def fulltext_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="search_evidence",
            description=(
                "在已入库论文全文（Qdrant 向量 + 重排）中检索证据片段，返回带相关度分数的引用。"
                "语义：证据预算（top_k 1-50，默认 10），受上下文窗口约束。"
                "适合：问具体方法/数据集/结论出自哪篇论文的哪一节；可限定在某几篇论文或某个集合。"
            ),
            parameters=schemas.SEARCH_EVIDENCE,
            handler=_search_evidence,
            category="fulltext",
        ),
        ToolDef(
            name="get_evidence",
            description=(
                "单篇论文的**章节结构地图**：列出该论文所有章节及每节首片段（章节名+首段+该节块数）。"
                "用法：先看章节地图决定深挖方向，再对具体章节调用 search_evidence(entity_ids=[x])。"
                "若论文未入库会返回提示，可先用 ingest_papers 入库。"
            ),
            parameters=schemas.GET_EVIDENCE,
            handler=_get_evidence,
            category="fulltext",
        ),
        ToolDef(
            name="list_collections",
            description="列出本地论文集合（名称/描述/成员数），可用于限定检索范围。",
            parameters=schemas.LIST_COLLECTIONS,
            handler=_list_collections,
            category="meta",
        ),
    ]
