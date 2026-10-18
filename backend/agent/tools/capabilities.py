from __future__ import annotations

"""Capability-backed catalog tools.

These wrap backend.capabilities.REGISTRY so the web agent, HTTP and MCP answer statistical questions
from the same implementation. The LLM only sees the JSON Schema; the handler is private.
"""

from backend.agent.tools.base import ToolDef, ToolResult
from backend.capabilities import REGISTRY


def _call(ctx: dict, name: str, arguments: dict) -> ToolResult:
    result = REGISTRY.call(ctx["catalog_conn"], name, arguments)
    if not result.ok:
        return ToolResult(ok=False, data={"error": result.error}, summary=f"{name} 失败")
    data = dict(result.data or {})
    data["meta"] = result.meta
    return ToolResult(ok=True, data=data, summary=f"{name} 完成")


def _schema(name: str) -> dict:
    cap = REGISTRY.get(name)
    if cap is None:
        raise KeyError(name)
    return cap.parameters


def capability_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="count_papers",
            description=("按领域/方向/venue/年份/关键词统计论文数，可按 year/venue/area/direction 分组。"
                         "口径：每篇论文取 canonical 记录年份与 venue；非论文记录不计入。返回 meta 里的数据版本。"),
            parameters=_schema("count_papers"),
            handler=lambda ctx, **kw: _call(ctx, "count_papers", kw),
            category="catalog",
        ),
        ToolDef(
            name="share_papers",
            description=("计算筛选结果在全部论文中的占比，按 year/venue/area/direction 分组。"
                         "返回 count / denominator / share_rate，避免把占比和绝对数混为一谈。"),
            parameters=_schema("share"),
            handler=lambda ctx, **kw: _call(ctx, "share", kw),
            category="catalog",
        ),
        ToolDef(
            name="trend_papers",
            description=("按年份统计筛选结果的论文数与占比，并给出首年到末年的变化率。"
                         "注意 2026 是滚动清单，绝对数不要与完整年份直接比较。"),
            parameters=_schema("trend"),
            handler=lambda ctx, **kw: _call(ctx, "trend", kw),
            category="catalog",
        ),
        ToolDef(
            name="top_values",
            description=("在筛选结果内统计 top venue / 领域 / 方向。用于回答“哪些方向最多/哪些会最热”。"),
            parameters=_schema("top_values"),
            handler=lambda ctx, **kw: _call(ctx, "top_values", kw),
            category="catalog",
        ),
    ]
