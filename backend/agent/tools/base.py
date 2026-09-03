from __future__ import annotations

"""Tool protocol layer: ToolDef (contract) / ToolResult (three-channel return value) / ToolRegistry (registration & dispatch).

This is the "protocol boundary" of the tools/ subpackage—the LLM only ever sees the JSON Schema serialized by
ToolDef.schema(), never the handler implementation. If MCP or third-party tool extensions are added later, this
file is the protocol adapter point.

Key design:
- dispatch's four-layer fault tolerance: unknown tool / non-dict args / TypeError / any exception → always return
  an ok=False ToolResult injected into the message, never raise to the orchestration layer (LLM tool_calls are untrusted input).
- ToolResult's three channels: data goes to the LLM context, provenance to the evidence Ledger, summary to the front-end trace.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """Return value of one tool call: data goes to the LLM context; provenance is evidence entries; summary for trace display."""

    ok: bool
    data: Any
    provenance: list[dict] = field(default_factory=list)
    summary: str = ""


@dataclass
class ToolDef:
    """Tool contract object: name/description/parameters are exposed to the LLM; handler is the private implementation."""

    name: str
    description: str
    parameters: dict
    handler: Callable
    category: str = "meta"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Tool registration and dispatch. dispatch returns ok=False without raising when a tool isn't found (the LLM may hallucinate a tool name)."""

    def __init__(self, tools: list[ToolDef]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def dispatch(self, name: str, arguments: dict, ctx: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, data={"error": f"未知工具: {name}"}, summary="未知工具")
        if not isinstance(arguments, dict):
            return ToolResult(ok=False, data={"error": "工具参数必须是 JSON 对象"}, summary="参数非法")
        try:
            return tool.handler(ctx, **arguments)
        except TypeError as exc:
            return ToolResult(ok=False, data={"error": f"参数错误: {exc}"}, summary="参数错误")
        except Exception as exc:  # inject internal tool exceptions into the result, don't break the loop
            return ToolResult(ok=False, data={"error": str(exc)}, summary=f"执行异常: {exc}")
