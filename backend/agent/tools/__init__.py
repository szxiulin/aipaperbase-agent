from __future__ import annotations

"""tools/ subpackage: self-contained tool layer (protocol + contract surface + atomic tools grouped by data domain).

The only external entry point: build_tools() → list[ToolDef]. Adding a tool category = creating a new domain
module (or subdirectory) + adding a line in build_tools() here, keeping it decoupled from the runner.
"""

from backend.agent.tools.comparison import comparison_tools
from backend.agent.tools.catalog import catalog_tools
from backend.agent.tools.exec import exec_tools
from backend.agent.tools.fulltext import fulltext_tools
from backend.agent.tools.local_library import local_library_tools
from backend.agent.tools.organization import organization_tools
from backend.agent.tools.research import research_tools
from backend.agent.tools.web import web_tools


def build_tools() -> list:
    """Aggregate all built-in tools (grouped by data domain; order is registration order)."""
    return comparison_tools() + catalog_tools() + local_library_tools() + fulltext_tools() + organization_tools() + exec_tools() + web_tools() + research_tools()


__all__ = ["build_tools", "catalog_tools", "local_library_tools", "fulltext_tools", "organization_tools", "exec_tools", "web_tools", "research_tools"]
