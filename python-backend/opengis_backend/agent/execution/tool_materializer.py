"""Dynamic tool schema materialization for LLM turns.

The executor still knows every registered tool. This module only chooses
which JSON schemas to expose to the provider for a given turn so long chats do
not pay the full tool-schema token cost on every request.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_VISIBILITY_MISS_RE = re.compile(
    r"(没有|不存在|无法|不能).{0,12}(工具|tool|接口)|"
    r"(no|do not|don't|cannot|can't).{0,24}(tool|function|interface)",
    re.IGNORECASE,
)


ALWAYS_VISIBLE_TOOLS = {
    "execute_code",
    "run_script_file",
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "glob",
    "grep",
    "list_directory",
    "file_exists",
    "list_layers",
    "get_layer",
    "query_features",
    "list_scripts",
    "read_script",
    "list_operations",
    "get_operation",
    "run_operation",
    "create_operation",
    "edit_operation",
    "promote_script_to_operation",
    "update_plan",
    "load_skill",
}

@dataclass
class ToolMaterialization:
    schemas: list[dict]
    selected_names: list[str]
    total_count: int
    reason: str


class ToolMaterializer:
    """Select a bounded tool-schema set for one provider turn."""

    def __init__(self, schemas: list[dict] | None, *, max_tools: int = 44) -> None:
        self.schemas = list(schemas or [])
        self.max_tools = max(8, int(max_tools))

    def materialize(
        self,
        messages: list[dict],
        *,
        force_all: bool = False,
        max_tools: int | None = None,
    ) -> ToolMaterialization:
        effective_max_tools = max(8, int(max_tools or self.max_tools))
        if force_all:
            return ToolMaterialization(
                schemas=self.schemas,
                selected_names=[self._name(schema) for schema in self.schemas],
                total_count=len(self.schemas),
                reason="all",
            )
        if len(self.schemas) <= effective_max_tools:
            return ToolMaterialization(
                schemas=self.schemas,
                selected_names=[self._name(schema) for schema in self.schemas],
                total_count=len(self.schemas),
                reason="all",
            )

        always_selected: list[dict] = []
        default_selected: list[dict] = []
        for schema in self.schemas:
            name = self._name(schema)
            if name in ALWAYS_VISIBLE_TOOLS:
                always_selected.append(schema)
                continue
            default_selected.append(schema)

        selected = always_selected + default_selected
        if len(selected) > effective_max_tools:
            always_names = {self._name(schema) for schema in always_selected}
            remaining_slots = max(0, effective_max_tools - len(always_selected))
            tail = [schema for schema in selected if self._name(schema) not in always_names]
            selected = always_selected + tail[:remaining_slots]

        return ToolMaterialization(
            schemas=selected,
            selected_names=[self._name(schema) for schema in selected],
            total_count=len(self.schemas),
            reason="profile_bounded",
        )

    @staticmethod
    def _name(schema: dict) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            return str(fn.get("name") or "")
        return ""


def format_active_tool_prompt(materialization: ToolMaterialization | None) -> str:
    """Build a concise per-turn system hint for active function tools."""
    if materialization is None:
        return ""
    names = [name for name in materialization.selected_names if name]
    if not names:
        return ""
    return (
        "## Active Function Tools For This Agent Profile\n"
        f"Materialization: {materialization.reason}; exposed "
        f"{len(names)}/{materialization.total_count} tools.\n"
        "Only these function tools are available to the selected agent profile:\n"
        + ", ".join(names)
        + "\nIf a required capability is missing from this list, it may be outside "
        "the current agent profile or not materialized for this turn. Explain the "
        "missing capability briefly instead of inventing another route."
    )


def is_tool_visibility_miss(text: str) -> bool:
    """Return True when text likely reflects dynamic tool visibility confusion."""
    return bool(text and _TOOL_VISIBILITY_MISS_RE.search(text))


__all__ = [
    "ALWAYS_VISIBLE_TOOLS",
    "ToolMaterialization",
    "ToolMaterializer",
    "format_active_tool_prompt",
    "is_tool_visibility_miss",
]
