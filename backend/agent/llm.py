from __future__ import annotations

"""Model integration layer: OpenAI-compatible chat calls decoupled from orchestration + process-level token budget.

Extracted from loop.py; single responsibility: build the payload (thinking/reasoning_content rules), perform the
HTTP call with backoff retry, and account for tokens. The runner only cares about "getting choices[0].message".

Protocol notes for thinking + tools (verified with scripts/agent_probe.py, 2026-08):
- Requests carrying tools must echo back reasoning_content (carried in the assistant message) on subsequent calls,
  otherwise cross-model/cross-version 400s can occur;
- No echo is needed when tools=[]; the runner follows this when assembling messages.
"""

import json
import time
from typing import Any, Callable

import httpx

from raglib._budget import charge as _charge_tokens, check_budget as _check_budget, spent as _token_spent

TIMEOUT = 120.0
MAX_RETRIES = 3


def token_spent() -> int:
    """Accumulated process-wide LLM tokens this run (0 until the first charge)."""
    return _token_spent()


def chat(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    thinking: str,
    reasoning_effort: str,
    max_tokens: int,
    on_delta: Callable[[str, str], None] | None = None,
) -> dict:
    """Single OpenAI-compatible /chat/completions call (with backoff retry + token accounting).

    With on_delta the call streams: on_delta("reasoning" | "answer", text) is called as tokens arrive,
    and the assembled response has the same shape as a non-streaming one.
    """
    if not base_url or not model:
        raise RuntimeError("未配置聊天模型：请设置 GENERATOR_BASE_URL 和 GENERATOR_MODEL。指定加入可使用本地库勾选操作。")
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    # Some OpenAI-compatible providers emit their private tool markup when
    # tool_choice=auto is paired with an empty tools list. A final synthesis is
    # text-only, so omit both fields altogether.
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if thinking == "enabled":
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["thinking"] = {"type": "disabled"}
    if on_delta is not None:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    attempt = 0
    while True:
        _check_budget()
        if on_delta is not None:
            try:
                with client.stream("POST", f"{base_url}/chat/completions",
                                   headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=TIMEOUT) as resp:
                    if resp.status_code == 200:
                        data = _collect_stream(resp, on_delta)
                        usage = data.get("usage") or {}
                        _charge_tokens(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
                        return data
                    resp.read()
                    if resp.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                        attempt += 1
                        time.sleep(2**attempt)
                        continue
                    raise RuntimeError(f"生成模型调用失败 HTTP {resp.status_code}: {resp.text[:200]}")
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES - 1:
                    attempt += 1
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"生成模型调用超时: {exc}") from exc
        try:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage") or {}
                _charge_tokens(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
                return data
            if resp.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                attempt += 1
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            if attempt < MAX_RETRIES - 1:
                attempt += 1
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"生成模型调用超时: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"生成模型调用失败 HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
    # Unreachable in theory (200 returns / raised after retries are exhausted); keeps type completeness


def _collect_stream(resp: httpx.Response, on_delta: Callable[[str, str], None]) -> dict:
    """Assemble an SSE chat-completions stream into the non-streaming response shape."""
    content: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict] = {}
    finish_reason = None
    model = ""
    usage: dict = {}
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            event = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        model = event.get("model") or model
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
                on_delta("reasoning", delta["reasoning_content"])
            if delta.get("content"):
                content.append(delta["content"])
                on_delta("answer", delta["content"])
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(call.get("index", 0), {"id": "", "type": "function",
                                                                "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content),
                               "reasoning_content": "".join(reasoning)}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    return {"choices": [{"message": message, "finish_reason": finish_reason}], "model": model, "usage": usage}


__all__ = ["chat", "token_spent", "TIMEOUT", "MAX_RETRIES"]
