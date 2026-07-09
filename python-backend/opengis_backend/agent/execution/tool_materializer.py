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
    "update_plan",
    "load_skill",
}

DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "map": (
        "map", "layer", "basemap", "geojson", "shp", "tiff", "raster", "vector",
        "feature", "地图", "图层", "底图", "要素", "栅格", "矢量", "制图", "画布",
    ),
    "layout": ("layout", "canvas", "legend", "north", "scale", "export", "画布", "图例", "比例尺", "指北针", "导出"),
    "worker": ("worker", "resident", "dynamic", "stream", "实时", "动态", "驻守", "后台", "轨迹"),
    "workflow": ("workflow", "flow", "dag", "node", "工作流", "节点"),
    "web": ("http", "https", "url", "web", "search", "fetch", "网页", "搜索", "最新"),
    "osm": ("osm", "openstreetmap", "overpass", "nominatim", "道路", "poi"),
    "datasource": ("datasource", "api", "数据源", "接口"),
    "report": ("report", "paper", "academic", "pdf", "报告", "论文", "摘要", "引用"),
    "subagent": ("subagent", "parallel", "子智能体", "并行"),
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

    def materialize(self, messages: list[dict], *, force_all: bool = False) -> ToolMaterialization:
        if force_all or len(self.schemas) <= self.max_tools:
            return ToolMaterialization(
                schemas=self.schemas,
                selected_names=[self._name(schema) for schema in self.schemas],
                total_count=len(self.schemas),
                reason="all",
            )

        text = self._messages_text(messages)
        lowered = text.lower()
        active_domains = {
            domain
            for domain, keywords in DOMAIN_KEYWORDS.items()
            if any(keyword.lower() in lowered for keyword in keywords)
        }

        always_selected: list[dict] = []
        domain_selected_schemas: list[dict] = []
        default_selected: list[dict] = []
        domain_selected = 0
        for schema in self.schemas:
            name = self._name(schema)
            if name in ALWAYS_VISIBLE_TOOLS:
                always_selected.append(schema)
                continue
            haystack = self._schema_text(schema)
            if active_domains and self._matches_domains(haystack, active_domains):
                domain_selected_schemas.append(schema)
                domain_selected += 1
                continue
            if not active_domains:
                default_selected.append(schema)

        if active_domains and domain_selected == 0:
            logger.debug("Tool materializer selected too few tools; falling back to all schemas.")
            return ToolMaterialization(
                schemas=self.schemas,
                selected_names=[self._name(schema) for schema in self.schemas],
                total_count=len(self.schemas),
                reason="fallback_all",
            )

        selected = always_selected + (
            domain_selected_schemas if active_domains else default_selected
        )
        if len(selected) > self.max_tools:
            always_names = {self._name(schema) for schema in always_selected}
            remaining_slots = max(0, self.max_tools - len(always_selected))
            tail = [schema for schema in selected if self._name(schema) not in always_names]
            selected = always_selected + tail[:remaining_slots]

        return ToolMaterialization(
            schemas=selected,
            selected_names=[self._name(schema) for schema in selected],
            total_count=len(self.schemas),
            reason="domains:" + ",".join(sorted(active_domains)) if active_domains else "bounded_default",
        )

    @staticmethod
    def _name(schema: dict) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            return str(fn.get("name") or "")
        return ""

    @staticmethod
    def _messages_text(messages: list[dict]) -> str:
        parts: list[str] = []
        for message in messages[-8:]:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                parts.append(content)
        return "\n".join(parts)

    @classmethod
    def _schema_text(cls, schema: dict) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if not isinstance(fn, dict):
            return ""
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        return " ".join(
            str(part)
            for part in [
                fn.get("name", ""),
                fn.get("description", ""),
                " ".join(str(key) for key in props.keys()),
            ]
        ).lower()

    @staticmethod
    def _matches_domains(haystack: str, domains: set[str]) -> bool:
        for domain in domains:
            if domain in haystack:
                return True
            for keyword in DOMAIN_KEYWORDS.get(domain, ()):
                if keyword.lower() in haystack:
                    return True
        return False


def format_active_tool_prompt(materialization: ToolMaterialization | None) -> str:
    """Build a concise per-turn system hint for active function tools."""
    if materialization is None:
        return ""
    names = [name for name in materialization.selected_names if name]
    if not names:
        return ""
    return (
        "## Active Function Tools For This Turn\n"
        f"Materialization: {materialization.reason}; exposed "
        f"{len(names)}/{materialization.total_count} tools.\n"
        "Only these function tools are currently exposed by the provider:\n"
        + ", ".join(names)
        + "\nIf a required capability is missing from this active list, explain the "
        "missing capability briefly or continue with execute_code/read_file/edit_file "
        "when appropriate; do not claim the platform has no such tool permanently."
    )


def is_tool_visibility_miss(text: str) -> bool:
    """Return True when text likely reflects dynamic tool visibility confusion."""
    return bool(text and _TOOL_VISIBILITY_MISS_RE.search(text))


__all__ = [
    "ALWAYS_VISIBLE_TOOLS",
    "DOMAIN_KEYWORDS",
    "ToolMaterialization",
    "ToolMaterializer",
    "format_active_tool_prompt",
    "is_tool_visibility_miss",
]
