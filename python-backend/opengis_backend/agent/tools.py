"""
Skill → callable adapter for the agent's subprocess executor.

This module converts ``RegisteredSkill`` records into plain callables
that the subprocess executor can invoke via its tool-bridge RPC.

v3.1 (2026-04): Removed smolagents dependency. Tools are now plain
callables (not smolagents.Tool subclasses). The executor's tool bridge
calls them as ``tool(*args, **kwargs)`` — same interface, no framework.

The public symbol is ``build_tool_callables``. The legacy name
``build_smolagents_tools`` is kept as an alias for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Callable
from opengis_backend.skills.registry import RegisteredSkill

logger = logging.getLogger(__name__)


def build_tool_callables(
    registered: list[RegisteredSkill],
    ctx_provider: (Callable[[], SkillContext] | None) = None,
) -> dict[str, Callable]:
    """Convert RegisteredSkill records into a name → callable dict.

    Args:
        registered: list of RegisteredSkill from the SkillRegistry.
        ctx_provider: zero-arg callable returning the SkillContext to inject
                      into context-aware skills. If None, falls back to the
                      contextvar-based ``get_current_context()`` (legacy path).

    Returns:
        dict mapping skill name → callable. Each callable accepts the
        skill's declared parameters as keyword arguments.
    """
    tools: dict[str, Callable] = {}

    for rs in registered:
        schema = rs.schema
        raw_fn = rs.raw_function
        needs_ctx = rs.needs_context

        # Resolve context provider for needs_ctx skills.
        if ctx_provider is not None:
            _get_ctx = ctx_provider
        else:
            from opengis_backend.skills.context import (
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


# Legacy aliases for backward compatibility.
build_smolagents_tools = build_tool_callables
_build_smolagents_tools = build_tool_callables


__all__ = ["build_tool_callables", "build_smolagents_tools", "_build_smolagents_tools"]
