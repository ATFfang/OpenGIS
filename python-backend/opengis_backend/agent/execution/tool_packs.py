"""Agent-facing tool pack classification.

Tool packs are the coarse capability boundary used by the agent runtime.  The
registry may still expose every built-in tool for RPC/manual use, but autonomous
agent loops should only see packs that are appropriate for their profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from opengis_backend.tools.registry import RegisteredTool


AGENT_EXCLUDED_PACKS = {"layout", "report"}
DEFAULT_AGENT_PACKS = {"skill", "file", "code", "map"}
# Code execution is a normal capability pack. It is intentionally not forced
# into every turn: exposing it globally makes simple map/style requests pay for
# a large schema and encourages one-off Python detours.
ALWAYS_AVAILABLE_SCHEMA_NAMES: set[str] = set()

_PREFIX_PACKS: tuple[tuple[str, str], ...] = (
    ("layout_", "layout"),
    ("academic_", "report"),
)

_NAME_PACKS: dict[str, str] = {
    # Report / writing capabilities are now skill-led, not always-on tools.
    "interactive_snapshot": "report",
    "write_report_section": "report",
    "export_report_pdf": "report",
    "format_references": "report",
    "generate_abstract": "report",
    # Basemap style is user/UI state, not an autonomous agent default.
    "set_basemap": "manual_ui",
    # Skill and preference control.
    "load_skill": "skill",
    "update_user_instructions": "skill",
    # File and code.
    "read_file": "file",
    "write_file": "file",
    "edit_file": "file",
    "file_exists": "file",
    "list_directory": "file",
    "glob": "file",
    "grep": "file",
    "create_directory": "file",
    "delete_file": "file",
    "move_file": "file",
    "copy_file": "file",
    "list_scripts": "code",
    "read_script": "code",
    "execute_code": "code",
    "run_script_file": "code",
    "bash": "system",
    # Map basics.
    "list_layers": "map",
    "get_layer": "map",
    "query_features": "map",
    "get_map_state": "map",
    "add_layer": "map",
    "remove_layer": "map",
    "fly_to": "map",
    "zoom_to_layer": "map",
    "set_basemap_visibility": "map",
    # 3D camera/view.
    "enter_3d_view": "map_3d",
    "exit_3d_view": "map_3d",
    "set_map_camera": "map_3d",
    "set_extrusion_style": "map_3d",
    # Styling.
    "update_layer_style": "style",
    "set_categorized_style": "style",
    "set_graduated_style": "style",
    "set_layer_visual_variables": "style",
    "set_layer_filter": "style",
    "set_layer_label": "style",
    "set_layer_order": "style",
    "highlight_features": "style",
    "update_legend_spec": "style",
    # Raster.
    "add_raster": "raster",
    "get_raster_info": "raster",
    "set_raster_style": "raster",
    # Operations.
    "list_operations": "operation",
    "get_operation": "operation",
    "validate_operation": "operation",
    "run_operation": "operation",
    "create_operation": "operation",
    "copy_operation_to_workspace": "operation",
    "edit_operation": "operation",
    "promote_script_to_operation": "operation",
    # Data / extension / external integrations.
    "csv_to_geojson": "data",
    "datasource_call": "datasource",
    "osm_call": "osm",
    "qgis_call": "qgis",
    "webfetch": "web",
    "websearch": "web",
    # Orchestration.
    "update_plan": "orchestration",
    "create_workflow": "workflow",
    "start_subagent": "subagent",
    "run_subagent": "subagent",
    "run_subagents": "subagent",
    # Workers.
    "start_worker": "worker",
    "start_dynamic_map_worker": "worker",
    "get_worker": "worker",
    "wait_worker_update": "worker",
    "restart_worker": "worker",
    "list_workers": "worker",
    "pause_worker": "worker",
    "delete_worker": "worker",
    # Debug.
    "debug_agent_context": "debug",
}

_PACK_TRIGGERS: dict[str, tuple[str, ...]] = {
    "style": (
        "style", "symbol", "color", "颜色", "设色", "分层", "分类", "透明", "大小",
        "标注", "标签", "过滤", "筛选", "虚线", "排序", "图例", "高亮",
    ),
    "raster": (
        "raster", "tif", "tiff", "栅格", "影像", "色带", "dem", "ndvi", "热力",
        "瓦片", "金字塔",
    ),
    "map_3d": (
        "3d", "三维", "拉伸", "高度", "挤出", "camera", "相机", "俯仰", "3dtiles",
    ),
    "worker": (
        "worker", "后台", "驻守", "持续", "动态", "实时", "刷新", "流式", "移动",
    ),
    "operation": (
        "operation", "操作", "原子", "复用", "沉淀", "run_operation", "修复operation",
    ),
    "workflow": (
        "workflow", "工作流", "流程", ".flow", "dag",
    ),
    "subagent": (
        "subagent", "子智能体", "子任务", "并行",
    ),
    "osm": (
        "osm", "openstreetmap", "路网", "poi", "nominatim", "overpass",
    ),
    "datasource": (
        "datasource", "数据源", "数据库", "连接", "api",
    ),
    "web": (
        "http", "https", "网页", "搜索", "联网", "下载",
    ),
    "qgis": (
        "qgis", "qgis_call",
    ),
    "debug": (
        "debug", "调试上下文", "token", "cache", "缓存", "上下文",
    ),
}


@dataclass(frozen=True)
class ToolPackDecision:
    name: str
    pack: str
    exposed_to_agent: bool
    reason: str


def tool_pack_for_name(name: str, fallback_group: str = "core") -> str:
    for prefix, pack in _PREFIX_PACKS:
        if name.startswith(prefix):
            return pack
    return _NAME_PACKS.get(name, fallback_group or "core")


def infer_tool_packs_for_text(
    text: str,
    *,
    base_packs: set[str] | None = None,
    allow_3d: bool = False,
) -> set[str]:
    """Infer required tool packs from a turn objective.

    This is a conservative deterministic router. It is intentionally pack-level
    rather than tool-level: if confidence is imperfect, choosing a slightly
    larger pack is safer than exposing the full platform schema every turn.
    """
    selected = set(base_packs or DEFAULT_AGENT_PACKS)
    if not allow_3d:
        selected.discard("map_3d")
    haystack = str(text or "").lower()
    if not haystack:
        return selected
    for pack, triggers in _PACK_TRIGGERS.items():
        if pack == "map_3d" and not allow_3d:
            continue
        if any(trigger.lower() in haystack for trigger in triggers):
            selected.add(pack)
    return selected


def tool_pack_for_registered(tool: RegisteredTool) -> str:
    schema = getattr(tool, "schema", None)
    name = str(getattr(schema, "name", "") or "")
    group = str(getattr(schema, "group", "") or "core")
    return tool_pack_for_name(name, group)


def agent_tool_decision(tool: RegisteredTool) -> ToolPackDecision:
    pack = tool_pack_for_registered(tool)
    if pack in AGENT_EXCLUDED_PACKS:
        return ToolPackDecision(
            name=tool.schema.name,
            pack=pack,
            exposed_to_agent=False,
            reason=f"{pack} is manual/skill-led and not exposed to autonomous loops",
        )
    if pack == "manual_ui":
        return ToolPackDecision(
            name=tool.schema.name,
            pack=pack,
            exposed_to_agent=False,
            reason="manual UI state must not be changed autonomously",
        )
    return ToolPackDecision(
        name=tool.schema.name,
        pack=pack,
        exposed_to_agent=True,
        reason="allowed",
    )


def visible_agent_tools(
    registered: list[RegisteredTool],
    *,
    allow_3d: bool = False,
) -> list[RegisteredTool]:
    out: list[RegisteredTool] = []
    for tool in registered:
        decision = agent_tool_decision(tool)
        if not decision.exposed_to_agent:
            continue
        if not allow_3d and decision.pack == "map_3d":
            continue
        out.append(tool)
    return out


def select_registered_tools_by_packs(
    registered: list[RegisteredTool],
    packs: set[str] | list[str] | tuple[str, ...] | None,
    *,
    allow_3d: bool = False,
) -> list[RegisteredTool]:
    if not packs:
        return [
            tool
            for tool in registered
            if allow_3d or tool_pack_for_registered(tool) != "map_3d"
        ]
    selected = set(packs)
    return [
        tool
        for tool in registered
        if tool_pack_for_registered(tool) in selected and (allow_3d or tool_pack_for_registered(tool) != "map_3d")
    ]


def tool_pack_summary(registered: list[RegisteredTool]) -> dict[str, list[str]]:
    packs: dict[str, list[str]] = {}
    for tool in registered:
        pack = tool_pack_for_registered(tool)
        packs.setdefault(pack, []).append(tool.schema.name)
    return {pack: sorted(names) for pack, names in sorted(packs.items())}


__all__ = [
    "AGENT_EXCLUDED_PACKS",
    "ALWAYS_AVAILABLE_SCHEMA_NAMES",
    "DEFAULT_AGENT_PACKS",
    "ToolPackDecision",
    "agent_tool_decision",
    "infer_tool_packs_for_text",
    "select_registered_tools_by_packs",
    "tool_pack_for_name",
    "tool_pack_for_registered",
    "tool_pack_summary",
    "visible_agent_tools",
]
