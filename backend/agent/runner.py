from __future__ import annotations

"""Runner execution layer: run_agent pure orchestration + AgentRun output object.

Aligned with the modern Runner/Agent separation: Agent is config (config.py), Runner is a stateless executor;
everything it needs (connections, pipeline, cancellation signal) is injected by the caller via ctx_builder.

Orchestration responsibilities (excluding model-protocol details—those live in llm.py; excluding history
storage—that's in session.py):
1. Assemble messages (instructions + sanitized history + query)
2. ReAct loop: LLM call (with tools) → dispatch tool_calls → backfill role=tool
3. Three runtime controls: cancellable (is_cancelled), round fallback (max_rounds), evidence convergence (soft constraint in instructions)
4. Converge into AgentRun: answer / finish_reason / reasoning / ledger (evidence) / trace (trajectory)

Echo-back rule (assistant messages carrying tool_calls must carry reasoning_content and echo it back on subsequent
requests, otherwise cross-model/version 400s can occur; the final synthesis round with tools=[] needs no echo—see the llm.py header note).
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from backend.agent.config import AgentConfig, DEFAULT_MAX_ROUNDS, DEFAULT_TOP_K
from backend.agent.context import AgentContext, build_default_context
from backend.agent.llm import chat as llm_chat
from backend.agent import session
from backend.agent.tools import build_tools
from backend.agent.tools.base import ToolRegistry

TOOL_RESULT_LIMIT = 3000


@dataclass
class AgentRun:
    """Auditable full output of a single Q&A. ledger = evidence (sources for the [n] citations in the answer), trace = per-step trajectory."""

    answer: str
    finish_reason: str
    reasoning: str
    ledger: list[dict]
    trace: list[dict]
    model: str = ""


def _trim(data: Any) -> Any:
    """Tool result truncation: if it exceeds TOOL_RESULT_LIMIT, return only a preview to avoid context explosion."""
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= TOOL_RESULT_LIMIT:
        return data
    return {"truncated": True, "total_chars": len(text), "preview": text[: TOOL_RESULT_LIMIT // 2] + "…"}


def _final_synthesis(client, cfg: AgentConfig, messages: list[dict]) -> dict:
    """Tool-free final synthesis after rounds are exhausted: converge the collected evidence into an answer."""
    data = llm_chat(
        client,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        messages=messages
        + [{"role": "user", "content": "基于以上所有检索到的证据，用 [n] 引用给出最终中文回答；若证据不足，明确说明缺口。"}],
        tools=[],
        thinking=cfg.thinking,
        reasoning_effort=cfg.reasoning_effort,
        max_tokens=cfg.max_tokens,
    )
    return data


def run_agent(
    *,
    query: str,
    history: list[dict] | None = None,
    top_k: int = DEFAULT_TOP_K,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    ctx_builder: Callable[[], dict | AgentContext] | None = None,
    config: AgentConfig | Any | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> AgentRun:
    """Run one agent Q&A round.

    config may be None (defaults read .env) or any legacy config containing .generator (normalized by AgentConfig.coerce).
    ctx_builder returns a context (dict or AgentContext) with catalog_conn/collections_conn/pipeline; its lifecycle is
    managed by the caller. When None, use the default construction (open a read-only catalog connection + lazy pipeline)
    and auto-close it at the end.
    """
    cfg = AgentConfig.coerce(config, max_rounds=max_rounds, top_k=top_k)
    own_ctx = ctx_builder is None
    ctx: dict | AgentContext = build_default_context() if own_ctx else ctx_builder()

    try:
        registry = ToolRegistry(build_tools())
        messages = session.assemble_messages(cfg.instructions, history, query)
        ledger: list[dict] = []
        trace: list[dict] = []
        reasoning_all: list[str] = []
        client = httpx.Client()

        for round_index in range(cfg.max_rounds):
            if is_cancelled is not None and is_cancelled():
                return AgentRun(
                    answer="",
                    finish_reason="agent_cancelled",
                    reasoning="\n".join(reasoning_all),
                    ledger=ledger,
                    trace=trace,
                    model=cfg.model,
                )
            t0 = time.time()
            data = llm_chat(
                client,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                model=cfg.model,
                messages=messages,
                tools=registry.schemas(),
                thinking=cfg.thinking,
                reasoning_effort=cfg.reasoning_effort,
                max_tokens=cfg.max_tokens,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            reasoning = msg.get("reasoning_content") or ""
            if reasoning:
                reasoning_all.append(reasoning)
            tool_calls = msg.get("tool_calls") or []
            trace.append({
                "round": round_index + 1,
                "reasoning": reasoning,
                "tool_calls": [
                    {"name": tc.get("function", {}).get("name"), "args": tc.get("function", {}).get("arguments")}
                    for tc in tool_calls
                ],
                "finish_reason": choice.get("finish_reason"),
                "duration_ms": elapsed_ms,
            })

            if not tool_calls:
                return AgentRun(
                    answer=msg.get("content") or "",
                    finish_reason=choice.get("finish_reason") or "stop",
                    reasoning="\n".join(reasoning_all),
                    ledger=ledger,
                    trace=trace,
                    model=data.get("model") or cfg.model,
                )

            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "reasoning_content": reasoning,  # must be echoed back on requests carrying tools
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                name = call.get("function", {}).get("name", "")
                try:
                    arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                t1 = time.time()
                result = registry.dispatch(name, arguments, ctx)
                tool_ms = int((time.time() - t1) * 1000)
                ledger.extend(result.provenance)
                trace.append({
                    "tool": name,
                    "args": arguments,
                    "ok": result.ok,
                    "summary": result.summary,
                    "n_results": len(result.provenance),
                    "duration_ms": tool_ms,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(_trim(result.data), ensure_ascii=False),
                })

        # Rounds exhausted: tool-free final synthesis, ensuring the user always gets an answer
        final_reasoning: list[str] = []
        try:
            data = _final_synthesis(client, cfg, messages)
            choice = (data.get("choices") or [{}])[0]
            final_msg = choice.get("message") or {}
            if final_msg.get("reasoning_content"):
                final_reasoning.append(final_msg["reasoning_content"])
            return AgentRun(
                answer=final_msg.get("content") or "",
                finish_reason="agent_final_synthesis",
                reasoning="\n".join(reasoning_all + final_reasoning),
                ledger=ledger,
                trace=trace,
                model=data.get("model") or cfg.model,
            )
        except Exception:
            return AgentRun(
                answer="",
                finish_reason="agent_max_rounds",
                reasoning="\n".join(reasoning_all),
                ledger=ledger,
                trace=trace,
                model=cfg.model,
            )
    finally:
        if own_ctx:
            ctx.close()


__all__ = ["run_agent", "AgentRun"]
