from __future__ import annotations

"""Conversation history protocol: assemble stored history into messages that can be sent to the LLM.

Responsibilities this phase (extracted from runner so run_agent stays agnostic to where history comes from):
- sanitize_history: keep only user/assistant content, stripping internal fields like reasoning_content;
- assemble_messages: system + recent N rounds of history + current query, producing the full messages.

If long-term memory / a persistent session provider is introduced later, the load/save protocol extension points live here.
"""

RECENT_TURNS = 4


def sanitize_history(history: list[dict] | None) -> list[dict]:
    """For history across user turns, keep only role (user/assistant) + content (strip reasoning)."""
    cleaned: list[dict] = []
    for turn in (history or [])[-RECENT_TURNS:]:
        role = turn.get("role") if turn.get("role") in ("user", "assistant") else "user"
        content = (turn.get("content") or "").strip()
        if content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def assemble_messages(instructions: str, history: list[dict] | None, query: str) -> list[dict]:
    """Assemble the full message sequence for the LLM: system instructions + sanitized history + current query."""
    messages: list[dict] = [{"role": "system", "content": instructions}]
    messages.extend(sanitize_history(history))
    messages.append({"role": "user", "content": query})
    return messages


__all__ = ["RECENT_TURNS", "sanitize_history", "assemble_messages"]
