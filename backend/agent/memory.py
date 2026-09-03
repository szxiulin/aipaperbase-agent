from __future__ import annotations

"""Long-term memory protocol (placeholder; not implemented this phase).

Layering convention:
- Working memory (4 turns of in-session context) → implemented in session.py;
- Long-term memory (cross-session: user preferences, research context, last topic) → this module only defines
  the protocol, with a default NullMemory no-op implementation, keeping the runner skeleton runnable without
  introducing storage dependencies.

When to build: after the research Q&A + console product rework (UX). The value of memory lies in "where the last
research left off", so first clarify when to write (on conversation convergence?) and how to recall (inject into
instructions?) before implementing, to avoid a placeholder abstraction leaking into business logic.
"""

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    """Long-term memory protocol: load_context injects before each Q&A round; remember writes after the conversation converges."""

    @abstractmethod
    def load_context(self, *, conversation_id: str | None = None, **scope: Any) -> str:
        """Return the long-term context text to inject into the system prompt / user message (empty string if none)."""
        raise NotImplementedError

    @abstractmethod
    def remember(
        self,
        *,
        content: str,
        conversation_id: str | None = None,
        **scope: Any,
    ) -> None:
        """Persist a piece of content worth remembering across sessions (who and when to write is up to the implementation)."""
        raise NotImplementedError


class NullMemory(MemoryProvider):
    """No-op memory: the runner injects it by default; the protocol runs but does nothing."""

    def load_context(self, *, conversation_id: str | None = None, **scope: Any) -> str:
        return ""

    def remember(self, *, content: str, conversation_id: str | None = None, **scope: Any) -> None:
        return None


__all__ = ["MemoryProvider", "NullMemory"]
