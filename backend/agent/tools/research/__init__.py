from __future__ import annotations

"""Research-domain tool aggregation: external authoritative scholarly sources (OpenAlex metadata verification + arXiv preprint pipeline).

Each atomic tool is defined in openalex.py / arxiv.py; this module only aggregates,
exposing research_tools() for tools/__init__'s build_tools() to collect.
"""

from backend.agent.tools.research.arxiv import arxiv_tools
from backend.agent.tools.research.openalex import openalex_tools


def research_tools() -> list:
    return openalex_tools() + arxiv_tools()


__all__ = ["research_tools", "openalex_tools", "arxiv_tools"]
