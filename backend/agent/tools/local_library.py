from __future__ import annotations

"""Read-only full-text library status tool for deterministic status questions."""

from backend.agent.tools.base import ToolDef, ToolResult
from backend.library import local_status


def _local_fulltext_status(ctx: dict) -> ToolResult:
    data = local_status.snapshot(
        ctx["catalog_conn"], downloads_conn=ctx.get("downloads_conn"),
        parsed_conn=ctx.get("parsed_conn"), document_chunk_counts=ctx.get("document_chunk_counts"),
        pipeline_fingerprint=ctx.get("pipeline_fingerprint", ""),
    )
    summary = data["summary"]
    return ToolResult(
        ok=True, data=data,
        summary=(f"本地全文库：当前有效索引 {summary['indexed_current'] if summary['qdrant_available'] else '未知（Qdrant 不可用）'} 篇，"
                 f"合法 PDF {summary['valid_pdfs']} 篇，已解析 {summary['parsed']} 篇，"
                 f"总 chunk {summary['chunks']}。"),
    )


def local_library_tools() -> list[ToolDef]:
    return [ToolDef(
        name="local_fulltext_status",
        description=("查询本地全文库的权威只读状态：合法 PDF、解析、当前有效向量索引、chunk 总数和逐篇状态。"
                     "用户问‘有几篇可全文问答’、‘哪篇没有入库’或‘入库状态’时必须优先调用；"
                     "不得根据 DOI、摘要或下载链接猜测入库状态。"),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_local_fulltext_status,
        category="local_library",
    )]
