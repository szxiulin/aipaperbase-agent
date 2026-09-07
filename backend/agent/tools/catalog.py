from __future__ import annotations

"""Catalog tools: search_papers (unified search: filter+sort+pagination+aggregation), get_paper (per-paper detail), list_facets (enumerate the catalog).

Implementation depends on backend.catalog.queries. ctx must contain catalog_conn (read-only connection, opened/closed uniformly by the loop).
"""

from backend.agent.tools import schemas
from backend.agent.tools.base import ToolDef, ToolResult

_LOCAL_STATUS_SQL = {
    "downloaded": "SELECT 1 FROM downloads WHERE entity_id = ? AND status = 'success'",
    "parsed": "SELECT 1 FROM parsed_documents WHERE entity_id = ? AND status = 'success'",
}


def _local_status(ctx: dict, entity_id: str) -> dict[str, bool]:
    status: dict[str, bool] = {"downloaded": False, "parsed": False, "ingested": False}
    try:
        from backend.library import local_status
        data = local_status.snapshot(
            ctx["catalog_conn"], downloads_conn=ctx.get("downloads_conn"), parsed_conn=ctx.get("parsed_conn"),
            document_chunk_counts=ctx.get("document_chunk_counts"),
            pipeline_fingerprint=ctx.get("pipeline_fingerprint", ""),
        )
        item = next((row for row in data["items"] if row["entity_id"] == entity_id), {})
        status = {
            "downloaded": item.get("pdf_status") in {"success", "duplicate"},
            "parsed": item.get("parse_status") == "success" and bool(item.get("markdown_exists")),
            "ingested": item.get("index_status") == "indexed_current",
        }
    except Exception:
        # Status is observational. A broken local manifest must not make a catalog query fail.
        status = {"downloaded": False, "parsed": False, "ingested": False}
    return status


def _search_papers(ctx: dict, query: str = "", filters: dict | None = None, sort: str = "relevance",
                   page: int = 1, page_size: int = 10, aggregate: list[str] | None = None) -> ToolResult:
    from backend.catalog import queries

    filters = filters or {}
    conn = ctx["catalog_conn"]
    result = queries.search_papers(
        conn,
        query=query,
        venues=filters.get("venues"),
        years=filters.get("years"),
        topics=filters.get("topics"),
        venue_type=filters.get("venue_type", ""),
        sort=sort,
        page=page,
        page_size=page_size,
        aggregate=aggregate,
    )
    compact = [
        {
            "entity_id": item["entity_id"],
            "title": item["title"],
            "venue": item["venue"],
            "year": item["year"],
        }
        for item in result["items"]
    ]
    provenance = [
        {
            "entity_id": item["entity_id"],
            "title": item["title"],
            "venue": item["venue"],
            "year": item["year"],
            "text": f"{item['title']}（{item['venue']} {item['year']}）",
            "section": f"{item['venue']} {item['year']}",
            "source": "catalog",
        }
        for item in result["items"]
    ]
    data: dict = {
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "items": compact,
    }
    if result["facets"]:
        data["facets"] = result["facets"]
    summary = f"命中 {result['total']} 篇"
    if result["facets"]:
        first_key = next(iter(result["facets"]))
        top = list(result["facets"][first_key].items())[:3]
        summary += f"，按{first_key}分布前3: " + ", ".join(f"{k}={v}" for k, v in top)
    return ToolResult(ok=True, data=data, provenance=provenance, summary=summary)


def _get_paper(ctx: dict, entity_id: str) -> ToolResult:
    conn = ctx["catalog_conn"]
    row = conn.execute(
        """SELECT e.entity_id, e.canonical_title AS title, e.first_year, e.last_year,
                  e.record_count,
                  p.authors, p.venue, p.venue_type, p.paper_url, p.doi, p.arxiv_id,
                  substr(p.abstract, 1, 600) AS abstract
           FROM paper_entities e
           JOIN paper_records p ON p.record_id = e.canonical_record_id
           WHERE e.entity_id = ?""",
        (entity_id,),
    ).fetchone()
    if row is None:
        return ToolResult(ok=False, data={"error": f"未找到论文 {entity_id}"}, summary="未找到")
    item = dict(row)
    item["local_status"] = _local_status(ctx, entity_id)
    provenance = [{
        "entity_id": entity_id,
        "title": item["title"],
        "venue": item["venue"],
        "year": item.get("first_year") or item.get("last_year"),
        "text": f"{item['title']}（{item['venue']}）" + (f"；摘要：{(item.get('abstract') or '')[:120]}" if item.get("abstract") else ""),
        "section": "catalog",
        "source": "catalog",
    }]
    return ToolResult(ok=True, data=item, provenance=provenance, summary=f"论文：{item['title'][:40]}")


def _list_facets(ctx: dict, type: str) -> ToolResult:
    conn = ctx["catalog_conn"]
    if type == "venues":
        rows = conn.execute("SELECT DISTINCT venue FROM paper_records ORDER BY venue").fetchall()
        return ToolResult(ok=True, data={"items": [r["venue"] for r in rows]}, summary=f"共 {len(rows)} 个 venue")
    if type == "years":
        rows = conn.execute("SELECT DISTINCT year FROM paper_records ORDER BY year DESC").fetchall()
        return ToolResult(ok=True, data={"items": [r["year"] for r in rows]}, summary=f"共 {len(rows)} 个年份")
    if type == "topics":
        rows = conn.execute("SELECT name FROM topic_definitions ORDER BY name").fetchall()
        return ToolResult(ok=True, data={"items": [r["name"] for r in rows]}, summary=f"共 {len(rows)} 个主题")
    if type == "collections":
        collections_conn = ctx.get("collections_conn")
        if collections_conn is None:
            return ToolResult(ok=False, data={"error": "集合库不可用"}, summary="集合库不可用")
        rows = collections_conn.execute(
            """SELECT c.collection_id, c.name, c.description,
                      (SELECT COUNT(*) FROM collection_members m WHERE m.collection_id = c.collection_id) AS member_count
               FROM collections c ORDER BY c.updated_at DESC"""
        ).fetchall()
        return ToolResult(ok=True, data={"items": [dict(r) for r in rows]}, summary=f"共 {len(rows)} 个集合")
    return ToolResult(ok=False, data={"error": f"未知枚举类型: {type}"}, summary="未知类型")


def catalog_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="search_papers",
            description=(
                "在本地 AI 论文元数据库（20 会议 + 10 期刊，2023-2026，约 11.8 万条）中统一检索论文。"
                "支持关键词+多值过滤（venues/years/topics/venue_type）+ 排序 + 分页 + 聚合分布。"
                "用法提示：问'有多少/哪些期刊/趋势'时用 page_size=0 只看 total 与 aggregate 分布（最省 token）；"
                "问'类似的论文'用 sort=relevance；列清单用 sort=year_desc 翻页。"
            ),
            parameters=schemas.SEARCH_PAPERS,
            handler=_search_papers,
            category="catalog",
        ),
        ToolDef(
            name="get_paper",
            description="获取单篇论文的完整元数据（标题/作者/venue/年份/DOI/摘要）与本地状态（是否已下载/解析/入库）。参数 entity_id 来自 search_papers。",
            parameters=schemas.GET_PAPER,
            handler=_get_paper,
            category="catalog",
        ),
        ToolDef(
            name="list_facets",
            description=(
                "枚举本地论文库的可筛选项：venues（会议/期刊）、topics（主题）、years（年份）、collections（集合）。"
                "构造 search_papers 的 filters 之前先调用本工具确认候选值。"
            ),
            parameters=schemas.LIST_FACETS,
            handler=_list_facets,
            category="catalog",
        ),
    ]
