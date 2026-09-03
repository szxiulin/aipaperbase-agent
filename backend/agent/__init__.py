from __future__ import annotations

"""backend.agent: Agent orchestration layer for the research assistant (public API surface).

Layers (aligned with the modern Runner/Agent separation):
- config.py    AgentConfig (read-only config; SYSTEM_PROMPT moved here)
- runner.py    run_agent (stateless executor) + AgentRun (output)
- llm.py       OpenAI-compatible chat client + token budget
- session.py   conversation history assembly protocol (working memory)
- memory.py    long-term memory protocol (placeholder, NullMemory)
- context.py   AgentContext (tool execution context) + default construction
- tools/       standalone tool subpackage: base (protocol) + schemas (contract surface) + grouped by data domain

External callers are recommended to go through this module: from backend.agent import run_agent, AgentRun, AgentConfig, build_tools
"""

from backend.agent.config import AgentConfig
from backend.agent.context import AgentContext
from backend.agent.runner import AgentRun, run_agent
from backend.agent.tools import build_tools

__all__ = ["AgentConfig", "AgentContext", "AgentRun", "run_agent", "build_tools"]
