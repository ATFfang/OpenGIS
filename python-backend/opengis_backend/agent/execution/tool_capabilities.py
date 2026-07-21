"""Capability metadata for function-call tools.

This is runner-facing metadata, not provider-specific JSON schema.  It lets
the loop reason about domains, side effects, and active objects without
hard-wiring every decision into prompts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCapability:
    domain: str
    side_effect: str = "none"  # none | file | map | worker | operation | workflow | external
    object_type: str = ""
    repair_tool: bool = False


CAPABILITIES: dict[str, ToolCapability] = {
    # Operation domain
    "list_operations": ToolCapability("operation", object_type="operation"),
    "get_operation": ToolCapability("operation", object_type="operation", repair_tool=True),
    "validate_operation": ToolCapability("operation", object_type="operation", repair_tool=True),
    "run_operation": ToolCapability("operation", side_effect="operation", object_type="operation"),
    "create_operation": ToolCapability("operation", side_effect="operation", object_type="operation"),
    "copy_operation_to_workspace": ToolCapability("operation", side_effect="operation", object_type="operation", repair_tool=True),
    "edit_operation": ToolCapability("operation", side_effect="operation", object_type="operation", repair_tool=True),
    "promote_script_to_operation": ToolCapability("operation", side_effect="operation", object_type="operation"),

    # File/code domain
    "read_file": ToolCapability("file", object_type="file", repair_tool=True),
    "write_file": ToolCapability("file", side_effect="file", object_type="file"),
    "edit_file": ToolCapability("file", side_effect="file", object_type="file", repair_tool=True),
    "file_exists": ToolCapability("file", object_type="file"),
    "list_directory": ToolCapability("file", object_type="directory"),
    "glob": ToolCapability("file", object_type="file"),
    "grep": ToolCapability("file", object_type="file"),
    "bash": ToolCapability("system", side_effect="external"),
    "execute_code": ToolCapability("code", side_effect="file", object_type="script"),
    "run_script_file": ToolCapability("code", side_effect="file", object_type="script"),

    # Map/GIS domain
    "list_layers": ToolCapability("map", object_type="layer"),
    "get_layer": ToolCapability("map", object_type="layer"),
    "query_features": ToolCapability("map", object_type="layer"),
    "add_layer": ToolCapability("map", side_effect="map", object_type="layer"),
    "remove_layer": ToolCapability("map", side_effect="map", object_type="layer"),
    "zoom_to_layer": ToolCapability("map", side_effect="map", object_type="view"),
    "fly_to": ToolCapability("map", side_effect="map", object_type="view"),
    "set_map_camera": ToolCapability("map", side_effect="map", object_type="view"),
    "enter_3d_view": ToolCapability("map", side_effect="map", object_type="view"),
    "exit_3d_view": ToolCapability("map", side_effect="map", object_type="view"),
    "update_layer_style": ToolCapability("map", side_effect="map", object_type="style"),
    "set_categorized_style": ToolCapability("map", side_effect="map", object_type="style"),
    "set_graduated_style": ToolCapability("map", side_effect="map", object_type="style"),
    "set_extrusion_style": ToolCapability("map", side_effect="map", object_type="style"),
    "set_layer_visual_variables": ToolCapability("map", side_effect="map", object_type="style"),
    "set_layer_filter": ToolCapability("map", side_effect="map", object_type="style"),
    "set_layer_label": ToolCapability("map", side_effect="map", object_type="style"),
    "set_layer_order": ToolCapability("map", side_effect="map", object_type="style"),
    "highlight_features": ToolCapability("map", side_effect="map", object_type="layer"),
    "update_legend_spec": ToolCapability("map", side_effect="map", object_type="legend"),
    "add_raster": ToolCapability("map", side_effect="map", object_type="raster"),
    "get_raster_info": ToolCapability("map", object_type="raster"),
    "set_raster_style": ToolCapability("map", side_effect="map", object_type="style"),
    "csv_to_geojson": ToolCapability("data", side_effect="file", object_type="dataset"),
    "load_raster": ToolCapability("map", side_effect="map", object_type="raster"),

    # Long-running domains
    "start_worker": ToolCapability("worker", side_effect="worker", object_type="worker"),
    "start_dynamic_map_worker": ToolCapability("worker", side_effect="worker", object_type="worker"),
    "pause_worker": ToolCapability("worker", side_effect="worker", object_type="worker"),
    "delete_worker": ToolCapability("worker", side_effect="worker", object_type="worker"),
    "restart_worker": ToolCapability("worker", side_effect="worker", object_type="worker"),
    "get_worker": ToolCapability("worker", object_type="worker"),
    "wait_worker_update": ToolCapability("worker", object_type="worker"),
    "list_workers": ToolCapability("worker", object_type="worker"),

    # Workflow domain
    "create_workflow": ToolCapability("workflow", side_effect="workflow", object_type="workflow"),

    # Debug/observability
    "debug_agent_context": ToolCapability("debug", object_type="context"),
}


def capability_for(tool_name: str) -> ToolCapability:
    return CAPABILITIES.get(tool_name, ToolCapability("general"))


def tools_with_side_effect(side_effect: str) -> set[str]:
    return {name for name, capability in CAPABILITIES.items() if capability.side_effect == side_effect}


# Human-readable, domain-level description of what the platform can do. Keyed by
# ``ToolCapability.domain`` so the manifest stays in sync with the capability
# table. Ordering below is irrelevant: the manifest renderer sorts domains for
# byte-stable output.
_DOMAIN_MANIFEST_LABELS: dict[str, str] = {
    "map": "Map & GIS — add/query/remove layers, categorized/graduated/extrusion styling, labels, filters, legends, raster layers, 2D/3D view and camera control",
    "operation": "Operations — list, inspect, validate, create, copy, edit, run and promote reusable geoprocessing operations",
    "file": "Files — read, write, edit, glob and grep files and directories in the workspace",
    "code": "Code — execute Python through execute_code and run persisted script files",
    "system": "System — run shell commands",
    "worker": "Resident workers — start, pause, restart, delete, inspect and stream long-running / dynamic map workers",
    "workflow": "Workflows — orchestrate multi-step DAG workflows",
    "data": "Data — convert and inspect datasets (e.g. CSV to GeoJSON)",
    "debug": "Debug — inspect agent context and observability state",
    "general": "General-purpose tools",
}


def format_capability_manifest(tool_names: list[str]) -> str:
    """Render a STABLE, domain-level statement of platform capabilities.

    This is deterministic given the profile's tool set (domains are sorted, no
    per-turn state), so it rides the cacheable stable prefix untouched. Its job
    is to tell the model which capability *domains* exist so it never claims a
    supported capability is missing merely because a specific function is not
    in the current turn's tool list.
    """
    domains: dict[str, bool] = {}
    for name in tool_names:
        domains[capability_for(name).domain] = True
    if not domains:
        return ""
    lines = [
        "## Platform Capabilities",
        "This platform natively supports the capability domains listed below. "
        "If a task fits one of these domains, the capability EXISTS — locate and "
        "call the matching function tool (the per-turn function list is the "
        "source of truth for exact names and parameters). Never tell the user a "
        "supported capability is unavailable; if you cannot see a specific tool, "
        "state precisely which capability you need.",
    ]
    for domain in sorted(domains):
        lines.append(f"- {_DOMAIN_MANIFEST_LABELS.get(domain, domain)}")
    return "\n".join(lines)


__all__ = [
    "CAPABILITIES",
    "ToolCapability",
    "capability_for",
    "tools_with_side_effect",
    "format_capability_manifest",
]
