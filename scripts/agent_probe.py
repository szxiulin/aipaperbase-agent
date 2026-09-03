#!/usr/bin/env python3
"""Probe: verify the real behavior of DeepSeek V4 thinking + tools.

Tests three things:
1. The response structure of a thinking request with tools (coexistence of
   reasoning_content / tool_calls / content / finish_reason)
2. Continuing after echoing back reasoning_content → should return 200 (official rule)
3. Not echoing back reasoning_content in the same turn → whether it returns 400 (control)
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from raglib.config import load_config, load_dotenv

load_dotenv()
config = load_config()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "获取当前日期",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_venues",
            "description": "列出本地论文库收录的会议与期刊",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

BASE = config.generator.base_url.rstrip("/")
KEY = config.generator.api_key
MODEL = config.generator.model


def chat(messages, *, echo_reasoning: bool):
    # When echo_reasoning=False, strip reasoning_content from assistant messages in history (simulates a buggy implementation)
    payload_messages = []
    for m in messages:
        m = dict(m)
        if not echo_reasoning:
            m.pop("reasoning_content", None)
        payload_messages.append(m)
    payload = {
        "model": MODEL,
        "messages": payload_messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    t0 = time.time()
    resp = httpx.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json=payload,
        timeout=120,
    )
    ms = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:300]} ({ms}ms)"
    return resp.json(), f"HTTP 200 ({ms}ms)"


def brief(msg: dict) -> str:
    parts = []
    if msg.get("role"):
        parts.append(f"role={msg['role']}")
    if msg.get("content"):
        parts.append(f"content={msg['content'][:60]!r}")
    if msg.get("reasoning_content"):
        parts.append(f"reasoning={len(msg['reasoning_content'])}字")
    if msg.get("tool_calls"):
        parts.append(f"tool_calls={[tc['function']['name'] for tc in msg['tool_calls']]}")
    return " ".join(parts)


def main() -> int:
    print(f"模型: {MODEL}  base: {BASE}")
    messages = [{"role": "user", "content": "今天是几号？顺便列出本地论文库收录的会议数量。可调用工具获取。"}]

    # 1. First call: inspect the structure
    data, note = chat(messages, echo_reasoning=True)
    print(f"\n[1] 首次带 tools 调用: {note}")
    if data is None:
        print("  FAIL", note)
        return 1
    choice = data["choices"][0]
    msg = choice["message"]
    print(f"  finish_reason={choice.get('finish_reason')}")
    print(f"  message: {brief(msg)}")
    print(f"  usage: {data.get('usage')}")

    messages.append(msg)  # Echo back in full (including reasoning_content + tool_calls)

    # 2. Correct loop: add the tool result message + echo back reasoning_content → should return 200
    loop_messages = list(messages)
    for call in msg.get("tool_calls", []):
        loop_messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": json.dumps({"ok": True, "result": "2026-08-30"}, ensure_ascii=False),
        })
    data2, note2 = chat(loop_messages, echo_reasoning=True)
    print(f"\n[2] 补 tool 结果 + 回传 reasoning_content: {note2}")
    if data2 is None:
        print("  FAIL", note2)
        return 1
    choice2 = data2["choices"][0]
    msg2 = choice2["message"]
    print(f"  finish_reason={choice2.get('finish_reason')}")
    print(f"  message: {brief(msg2)}")
    if msg2.get("tool_calls"):
        print("  注意：模型又要求工具，属于正常多轮；探针到此确认结构即可")

    # 3. Strip reasoning_content in the same turn (buggy-implementation control) → observe whether it returns 400
    loop_messages2 = [dict(m) for m in loop_messages]
    data3, note3 = chat(loop_messages2, echo_reasoning=False)
    print(f"\n[3] 剥离 reasoning_content 后再调（对照）: {note3}")
    if data3 is None:
        print("  => 确认：带 tools 时必须回传 reasoning_content，否则报错")
    else:
        print(f"  => 未报错: finish_reason={data3['choices'][0].get('finish_reason')}")
        print(f"  message: {brief(data3['choices'][0]['message'])}")

    print("\n探针完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
