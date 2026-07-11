"""Executable function-call tool registry."""

import asyncio
import importlib
import pkgutil
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from opengis_backend.tools.context import (
    ToolContext,
    get_current_context,
    set_current_context,
    reset_current_context,
)
from opengis_backend.tools.schema import ToolParam, ToolSchema


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    data: Any = None
    error: str | None = None
    geojson: dict | None = None
    chart_config: dict | None = None


@dataclass
class RegisteredTool:
    """A tool registered in the registry."""
    schema: ToolSchema
    function: Callable
    needs_context: bool = False
    raw_function: Callable | None = None  # Original undecorated function for the tool bridge.


# Global tool registry
_registry: dict[str, RegisteredTool] = {}


def tool(
    name: str,
    display_name: str,
    description: str,
    category: str,
    params: list[dict],
    returns: str,
    examples: list[str] | None = None,
    tags: list[str] | None = None,
    needs_context: bool = False,
    group: str = "core",
):
    """
    Decorator to register a function as an executable GIS tool.

    If `needs_context=True`, the tool function MUST accept a `ctx: ToolContext`
    as its first positional argument. The agent runtime will inject the active
    ToolContext automatically — user code never passes it manually.

    Usage:
        @tool(
            name="add_layer",
            display_name="Add Map Layer",
            description="Push a GeoJSON layer onto the map.",
            category="visualization",
            params=[...],
            returns="Layer id",
            needs_context=True,
        )
        def add_layer(ctx, geojson_path, layer_id, style=None):
            ctx.notify("map.addLayer", {...})
            return layer_id
    """
    def decorator(func: Callable):
        schema = ToolSchema(
            name=name,
            display_name=display_name,
            description=description,
            category=category,
            params=[ToolParam(**p) for p in params],
            returns=returns,
            examples=examples or [],
            tags=tags or [],
            group=group,
        )

        @wraps(func)
        async def wrapper(**kwargs):
            try:
                if needs_context:
                    ctx = get_current_context()
                    call = lambda: func(ctx, **kwargs)
                else:
                    call = lambda: func(**kwargs)

                if asyncio.iscoroutinefunction(func):
                    if needs_context:
                        result = await func(get_current_context(), **kwargs)
                    else:
                        result = await func(**kwargs)
                else:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, call)
                return ToolResult(success=True, data=result)
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        # Store the async wrapper plus the raw function for direct tool calls.
        _registry[name] = RegisteredTool(
            schema=schema,
            function=wrapper,
            needs_context=needs_context,
            raw_function=func,
        )

        return wrapper

    return decorator


class ToolRegistry:
    """Manages executable tool discovery, registration, and execution."""

    async def discover_and_load(self) -> None:
        """Auto-discover tools from builtin/ directory."""
        builtin_path = Path(__file__).parent / "builtin"
        if builtin_path.exists():
            self._load_package("opengis_backend.tools.builtin")

    def _load_package(self, package_name: str) -> None:
        """Load all modules in a package to trigger @tool decorators."""
        try:
            package = importlib.import_module(package_name)
            if hasattr(package, "__path__"):
                for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
                    try:
                        importlib.import_module(f"{package_name}.{modname}")
                    except Exception as e:
                        print(f"[ToolRegistry] Failed to load {package_name}.{modname}: {e}")
        except ImportError as e:
            print(f"[ToolRegistry] Failed to import {package_name}: {e}")

    def list_all(self) -> list[ToolSchema]:
        """List all registered tool schemas."""
        return [s.schema for s in _registry.values()]

    def list_registered(self) -> list[RegisteredTool]:
        """List all RegisteredTool records for agent/runtime adapters."""
        return list(_registry.values())

    def get(self, name: str) -> RegisteredTool | None:
        """Look up a registered tool by name."""
        return _registry.get(name)

    def list_by_category(self, category: str) -> list[ToolSchema]:
        """List tools filtered by category."""
        return [s.schema for s in _registry.values() if s.schema.category == category]

    def list_by_groups(self, groups: list[str]) -> list[RegisteredTool]:
        """List tools filtered by group membership."""
        return [s for s in _registry.values() if s.schema.group in groups]

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in _registry

    async def execute(
        self,
        name: str,
        args: dict,
        context: ToolContext | None = None,
    ) -> dict:
        """
        Execute a tool by name with given arguments.

        If a ToolContext is provided, it is installed before the call so
        context-aware tools can call ctx.notify(...).
        """
        if name not in _registry:
            return {"success": False, "error": f"Tool not found: {name}"}

        registered = _registry[name]

        token = None
        if context is not None:
            token = set_current_context(context)
        try:
            result: ToolResult = await registered.function(**args)
        finally:
            if token is not None:
                reset_current_context(token)

        return {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "geojson": result.geojson,
            "chart_config": result.chart_config,
        }
