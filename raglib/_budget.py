"""Process-wide LLM token budget shared by every generator (raglib + agent).

GENERATOR_TOKEN_BUDGET is read once at import time (0 = unlimited). charge()
raises once accumulated prompt+completion tokens exceed the budget, so a runaway
loop cannot silently drain an API balance. The counter resets when the process
restarts; two independent subsystems (RAG pipeline vs agent) now share it.
"""

from __future__ import annotations

import os
import threading

_BUDGET = int(os.environ.get("GENERATOR_TOKEN_BUDGET", "0"))  # 0 = unlimited
_SPENT = 0
_LOCK = threading.Lock()


def charge(prompt_tokens: int, completion_tokens: int) -> None:
    """Account one LLM call's token usage; raise RuntimeError once past the budget."""
    global _SPENT
    if _BUDGET <= 0:
        return
    with _LOCK:
        _SPENT += prompt_tokens + completion_tokens
        if _SPENT > _BUDGET:
            raise RuntimeError(
                f"LLM token 预算已耗尽（本次进程已用 {_SPENT} tokens，上限 {_BUDGET}）。"
                "重启服务或调大 GENERATOR_TOKEN_BUDGET 后继续。"
            )


def spent() -> int:
    with _LOCK:
        return _SPENT
