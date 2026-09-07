"""Process-wide LLM token budget shared by every generator (raglib + agent).

GENERATOR_TOKEN_BUDGET is read once at the first check/charge, after callers have
loaded their environment (0 = unlimited). Check before every provider attempt;
charge actual reported usage afterwards, including in unlimited mode.

This is a soft process budget, not a reservation system: calls already in flight
can overshoot. charge() records that usage before raising; subsequent checks
block at or above the limit. State is shared by RAG and agent, not across processes,
and resets on restart. Changing the configured limit also requires a restart.
"""

from __future__ import annotations

import os
import threading

_BUDGET: int | None = None
_SPENT = 0
_LOCK = threading.Lock()


def _budget_locked() -> int:
    """Initialize lazily; the caller must hold _LOCK."""
    global _BUDGET
    if _BUDGET is None:
        _BUDGET = int(os.environ.get("GENERATOR_TOKEN_BUDGET", "0"))
    return _BUDGET


def _exhausted() -> RuntimeError:
    return RuntimeError(
        f"LLM token 预算已耗尽（本次进程已用 {_SPENT} tokens，上限 {_BUDGET}）。"
        "请重启服务，或调大 GENERATOR_TOKEN_BUDGET 后重启服务继续。"
    )


def check_budget() -> None:
    """Reject exhausted budgets before a provider attempt; do not reserve tokens."""
    with _LOCK:
        budget = _budget_locked()
        if budget > 0 and _SPENT >= budget:
            raise _exhausted()


def charge(prompt_tokens: int, completion_tokens: int) -> None:
    """Record actual usage even when unlimited/already exhausted; raise on overshoot."""
    global _SPENT
    with _LOCK:
        budget = _budget_locked()
        _SPENT += prompt_tokens + completion_tokens
        if budget > 0 and _SPENT > budget:
            raise _exhausted()


def spent() -> int:
    with _LOCK:
        return _SPENT
