"""
Tool → callable adapter for the agent's subprocess executor.

This module converts ``RegisteredTool`` records into plain callables
that the subprocess executor can invoke via its tool-bridge RPC.

Tools are plain callables. The executor's tool bridge calls them as
``tool(*args, **kwargs)``.

The public symbol is ``build_tool_callables``.
"""

from __future__ import annotations

import logging
from typing import Callable
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import RegisteredTool

logger = logging.getLogger(__name__)

AGENT_FORBIDDEN_TOOL_NAMES = {
    # Basemap choice is treated as user/UI state. Agent runs may hide/show the
    # basemap when explicitly asked, but must not switch the user's selected
    # basemap style.
    "set_basemap",
}


def filter_agent_tools(registered: list[RegisteredTool]) -> list[RegisteredTool]:
    """Remove tools that should not be exposed to autonomous agent loops."""
    return [rs for rs in registered if rs.schema.name not in AGENT_FORBIDDEN_TOOL_NAMES]


def build_tool_callables(
    registered: list[RegisteredTool],
    ctx_provider: (Callable[[], ToolContext] | None) = None,
) -> dict[str, Callable]:
    """Convert RegisteredTool records into a name → callable dict.

    Args:
        registered: list of RegisteredTool from the ToolRegistry.
        ctx_provider: zero-arg callable returning the ToolContext to inject
                      into context-aware tools. If None, uses the ambient
                      contextvar-based ``get_current_context()``.

    Returns:
        dict mapping tool name → callable. Each callable accepts the
        tool's declared parameters as keyword arguments.
    """
    tools: dict[str, Callable] = {}

    for rs in registered:
        schema = rs.schema
        raw_fn = rs.raw_function
        needs_ctx = rs.needs_context

        # Resolve context provider for context-aware tools.
        if ctx_provider is not None:
            _get_ctx = ctx_provider
        else:
            from opengis_backend.tools.context import (
                get_current_context as _get_ctx,
            )

        if needs_ctx:
            # Capture raw_fn and _get_ctx in closure scope.
            def _make_ctx_wrapper(_fn=raw_fn, _ctx=_get_ctx):
                def wrapper(*args, **kwargs):
                    return _fn(_ctx(), *args, **kwargs)
                wrapper.__name__ = schema.name
                wrapper.__qualname__ = schema.name
                return wrapper
            tools[schema.name] = _make_ctx_wrapper()
        else:
            def _make_wrapper(_fn=raw_fn, _name=schema.name):
                def wrapper(*args, **kwargs):
                    return _fn(*args, **kwargs)
                wrapper.__name__ = _name
                wrapper.__qualname__ = _name
                return wrapper
            tools[schema.name] = _make_wrapper()

    return tools


__all__ = ["AGENT_FORBIDDEN_TOOL_NAMES", "filter_agent_tools", "build_tool_callables"]
