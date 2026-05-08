"""Skill registry — discovers, registers, and executes GIS skills."""

import asyncio
import importlib
import pkgutil
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from opengis_backend.skills.context import (
    SkillContext,
    get_current_context,
    set_current_context,
    reset_current_context,
)
from opengis_backend.skills.schema import SkillParam, SkillSchema


@dataclass
class SkillResult:
    """Result of a skill execution."""
    success: bool
    data: Any = None
    error: str | None = None
    geojson: dict | None = None
    chart_config: dict | None = None


@dataclass
class RegisteredSkill:
    """A skill registered in the registry."""
    schema: SkillSchema
    function: Callable
    needs_context: bool = False
    raw_function: Callable | None = None  # Original undecorated function (for CodeAgent direct call)


# Global skill registry
_registry: dict[str, RegisteredSkill] = {}


def skill(
    name: str,
    display_name: str,
    description: str,
    category: str,
    params: list[dict],
    returns: str,
    examples: list[str] | None = None,
    tags: list[str] | None = None,
    needs_context: bool = False,
):
    """
    Decorator to register a function as a GIS Skill.

    If `needs_context=True`, the skill function MUST accept a `ctx: SkillContext`
    as its first positional argument. The CodeAgent will inject the active
    SkillContext automatically — user code never passes it manually.

    Usage:
        @skill(
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
        schema = SkillSchema(
            name=name,
            display_name=display_name,
            description=description,
            category=category,
            params=[SkillParam(**p) for p in params],
            returns=returns,
            examples=examples or [],
            tags=tags or [],
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
                return SkillResult(success=True, data=result)
            except Exception as e:
                return SkillResult(success=False, error=str(e))

        # Store the async wrapper plus the raw function for CodeAgent's direct calls.
        _registry[name] = RegisteredSkill(
            schema=schema,
            function=wrapper,
            needs_context=needs_context,
            raw_function=func,
        )

        return wrapper

    return decorator


class SkillRegistry:
    """Manages skill discovery, registration, and execution."""

    async def discover_and_load(self) -> None:
        """Auto-discover skills from builtin/ directory."""
        builtin_path = Path(__file__).parent / "builtin"
        if builtin_path.exists():
            self._load_package("opengis_backend.skills.builtin")

    def _load_package(self, package_name: str) -> None:
        """Load all modules in a package to trigger @skill decorators."""
        try:
            package = importlib.import_module(package_name)
            if hasattr(package, "__path__"):
                for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
                    try:
                        importlib.import_module(f"{package_name}.{modname}")
                    except Exception as e:
                        print(f"[SkillRegistry] Failed to load {package_name}.{modname}: {e}")
        except ImportError as e:
            print(f"[SkillRegistry] Failed to import {package_name}: {e}")

    def list_all(self) -> list[SkillSchema]:
        """List all registered skill schemas."""
        return [s.schema for s in _registry.values()]

    def list_registered(self) -> list[RegisteredSkill]:
        """List all RegisteredSkill records (for CodeAgent tool wrapping)."""
        return list(_registry.values())

    def get(self, name: str) -> RegisteredSkill | None:
        """Look up a registered skill by name."""
        return _registry.get(name)

    def list_by_category(self, category: str) -> list[SkillSchema]:
        """List skills filtered by category."""
        return [s.schema for s in _registry.values() if s.schema.category == category]

    def has(self, name: str) -> bool:
        """Check if a skill is registered."""
        return name in _registry

    async def execute(
        self,
        name: str,
        args: dict,
        context: SkillContext | None = None,
    ) -> dict:
        """
        Execute a skill by name with given arguments.

        If a SkillContext is provided, it is installed before the call so
        context-aware skills can call ctx.notify(...).
        """
        if name not in _registry:
            return {"success": False, "error": f"Skill not found: {name}"}

        registered = _registry[name]

        token = None
        if context is not None:
            token = set_current_context(context)
        try:
            result: SkillResult = await registered.function(**args)
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
