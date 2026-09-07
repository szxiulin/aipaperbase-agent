from __future__ import annotations

"""Model integration layer: OpenAI-compatible chat calls decoupled from orchestration + process-level token budget.

Extracted from loop.py; single responsibility: build the payload (thinking/reasoning_content rules), perform the
HTTP call with backoff retry, and account for tokens. The runner only cares about "getting choices[0].message".

Protocol notes for thinking + tools (verified with scripts/agent_probe.py, 2026-08):
- Requests carrying tools must echo back reasoning_content (carried in the assistant message) on subsequent calls,
  otherwise cross-model/cross-version 400s can occur;
- No echo is needed when tools=[]; the runner follows this when assembling messages.
"""

import time
from typing import Any

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
) -> dict:
    """Single OpenAI-compatible /chat/completions call (with backoff retry + token accounting)."""
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
    attempt = 0
    while True:
        _check_budget()
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


__all__ = ["chat", "token_spent", "TIMEOUT", "MAX_RETRIES"]
