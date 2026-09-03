from __future__ import annotations

"""Execution tools: ingest_papers (two-phase: the agent only produces a plan; execution happens after user confirmation).

Safety contract: this tool only returns an "execution plan", never actually downloads/parses/ingests.
Execution is done by the user confirming on the front end, which then calls POST /api/chats/{id}/ingest.
"""

from backend.agent.tools.base import ToolDef, ToolResult


def _ingest_papers(ctx: dict, entity_ids: list[str]) -> ToolResult:
    if not entity_ids:
        return ToolResult(ok=False, data={"error": "entity_ids 不能为空"}, summary="缺少论文")
    ids = list(dict.fromkeys(entity_ids))[:50]
    catalog_conn = ctx["catalog_conn"]
    titles: dict[str, str] = {}
    if catalog_conn is not None:
        rows = catalog_conn.execute(
            "SELECT e.entity_id, e.canonical_title AS title FROM paper_entities e WHERE e.entity_id IN (%s)"
            % ",".join("?" * len(ids)),
            ids,
        ).fetchall()
        titles = {row["entity_id"]: row["title"] for row in rows}
    papers = [
        {
            "entity_id": entity_id,
            "title": titles.get(entity_id, entity_id),
            "steps": ["download", "parse", "embed"],
        }
        for entity_id in ids
    ]
    plan = {
        "papers": papers,
        "total": len(papers),
        "steps": ["download", "parse", "embed"],
        "estimated_minutes": max(1, len(papers) * 2),
        "confirm_required": True,
    }
    return ToolResult(
        ok=True,
        data={"plan": plan, "hint": "执行需要用户确认：回答里会出现确认卡片，点确认后才会真正下载/解析/入库"},
        summary=f"计划入库 {len(papers)} 篇（下载+解析+嵌入），等待用户确认",
    )


def exec_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="ingest_papers",
            description=(
                "把论文批量下载/解析/入库到本地全文库（之后才能被 search_evidence/get_evidence 检索）。"
                "两阶段：本工具只返回执行计划，不真正执行；用户在前端确认卡片点确认后才会执行。"
                "用法：用户要求'入库/下载这篇论文/把这几篇加入本地库'时调用，entity_ids 来自 search_papers。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "要入库的论文实体 ID 列表（来自 search_papers 结果）",
                    },
                },
                "required": ["entity_ids"],
            },
            handler=_ingest_papers,
            category="exec",
        ),
    ]
