from __future__ import annotations

"""Tool execution context: AgentContext + default construction.

AgentContext is a dict subclass—all tool handlers keep the dict-style access (ctx["catalog_conn"],
ctx.get("web_budget"), ctx.setdefault(...)) with zero changes, while also providing keyword construction
and field defaults, consolidating "what the context holds" into one place (the first step toward typing;
attribute access can be migrated gradually).

Field convention (implicit contract between tools and the runner):
- catalog_conn     : backend.catalog SQLite connection (read-only), used by catalog tools
- collections_conn : backend.collections SQLite connection (read-only), may be None
- pipeline         : raglib Pipeline (full-text retrieval), lazily loaded by full-text tools, may be None
- budget keys such as web_budget / openalex_budget are created by the corresponding tool via setdefault; no need to predefine
"""

from typing import Any


class AgentContext(dict):
    """Tool context. Constructor args are the default keys; extra goes to free keys beyond setdefault."""

    _DEFAULT_KEYS = ("catalog_conn", "collections_conn", "pipeline")

    def __init__(
        self,
        *,
        catalog_conn: Any = None,
        collections_conn: Any = None,
        pipeline: Any = None,
        **extra: Any,
    ) -> None:
        super().__init__(catalog_conn=catalog_conn, collections_conn=collections_conn, pipeline=pipeline, **extra)

    def close(self) -> None:
        """Release connections held by the default construction (only needed for contexts created by build_default_context())."""
        for key in ("catalog_conn", "collections_conn"):
            conn = self.get(key)
            if conn is not None and hasattr(conn, "close"):
                try:
                    conn.close()
                except Exception:  # idempotent cases such as the connection already being closed
                    pass


def build_default_context() -> AgentContext:
    """Default context when no ctx_builder: open a read-only catalog connection (pipeline is lazily loaded by full-text tools).

    The connection lifecycle is the caller's responsibility: runner calls ctx.close() in finally when own_ctx.
    """
    from backend.catalog.database import DEFAULT_DATABASE, connect

    return AgentContext(catalog_conn=connect(DEFAULT_DATABASE, read_only=True))


__all__ = ["AgentContext", "build_default_context"]
