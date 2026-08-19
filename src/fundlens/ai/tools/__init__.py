"""The read-only compute tools Claude may call (docs/architecture/ai-layer.md).

Registered here in one place so the read-only invariant is checkable by
looking at a single list -- see `registry.py` for why that matters.
"""

from fundlens.ai.tools.funds import FUND_TOOLS
from fundlens.ai.tools.planning import PLANNING_TOOLS
from fundlens.ai.tools.registry import Tool, ToolError, call_tool, registry

TOOLS: dict[str, Tool] = registry([*PLANNING_TOOLS, *FUND_TOOLS])

__all__ = ["TOOLS", "Tool", "ToolError", "call_tool", "registry"]
