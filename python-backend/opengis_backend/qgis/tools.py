"""QGIS tools — single unified entry point for all QGIS MCP commands."""

import json
import logging
from typing import Any

from opengis_backend.qgis.client import get_client, QgisConnectionError
from opengis_backend.tools.registry import tool

logger = logging.getLogger("opengis.qgis.tools")

# ── Command reference for the LLM ──────────────────────────────────

QGIS_COMMANDS = {
    # system
    "ping":                          "Check connectivity to the running QGIS instance.",
    "get_qgis_info":                 "Get QGIS version, profile folder, installed plugin count.",
    "diagnose":                      "Run health checks on the QGIS MCP connection.",
    "transform_coordinates":         "Transform coordinates between CRS. Params: source_crs, target_crs, point/points/bbox (JSON).",
    # project
    "load_project":                  "Load a .qgz or .qgs project file. Params: path.",
    "create_new_project":            "Create a new QGIS project. Params: path.",
    "save_project":                  "Save the current project. Params: path (optional).",
    "get_project_info":              "Get project metadata: title, CRS, layer count.",
    "set_project_crs":               "Set project CRS. Params: crs (e.g. 'EPSG:4326').",
    # layers
    "add_vector_layer":              "Add a vector layer. Params: path, name? (display name), provider? (default 'ogr').",
    "add_raster_layer":              "Add a raster layer. Params: path, name?, provider? (default 'gdal').",
    "get_layers":                    "List all layers. Params: limit?, offset?.",
    "remove_layer":                  "Remove a layer. Params: layer_id.",
    "find_layer":                    "Find layers by name glob. Params: name_pattern.",
    "get_layer_info":                "Layer details: CRS, extent, source, fields. Params: layer_id.",
    "get_layer_schema":              "Field schema of a vector layer. Params: layer_id.",
    "get_layer_extent":              "Bounding box of a layer. Params: layer_id.",
    "set_active_layer":              "Set the active layer. Params: layer_id.",
    "get_active_layer":              "Get the currently active layer.",
    "set_layer_visibility":          "Show/hide a layer. Params: layer_id, visible (bool).",
    "zoom_to_layer":                 "Zoom canvas to layer extent. Params: layer_id.",
    # features
    "get_layer_features":            "Query features. Params: layer_id, limit?, offset?, expression?, include_geometry?.",
    "add_features":                  "Add features. Params: layer_id, features (JSON array).",
    "update_features":               "Update features by FID. Params: layer_id, updates (JSON array of {fid, attributes}).",
    "delete_features":               "Delete features. Params: layer_id, fids? (JSON array), expression?.",
    "get_field_statistics":           "Field stats: count, sum, mean, min, max. Params: layer_id, field_name.",
    "select_features":               "Select features. Params: layer_id, expression?, fids? (JSON).",
    "get_selection":                 "Get selected feature IDs. Params: layer_id.",
    "clear_selection":               "Clear selection. Params: layer_id.",
    # processing
    "execute_code":                  "Run PyQGIS code. Params: code.",
    "execute_processing":            "Run a Processing algorithm. Params: algorithm, parameters (JSON).",
    "list_processing_algorithms":    "List available algorithms. Params: search?, provider?.",
    "get_algorithm_help":            "Algorithm help. Params: algorithm_id.",
    # rendering
    "render_map_base64":             "Render map to PNG (base64). Params: width?, height?, path?.",
    "get_canvas_extent":             "Get canvas extent + CRS.",
    "set_canvas_extent":             "Set canvas extent. Params: xmin, ymin, xmax, ymax, crs?.",
    "get_canvas_screenshot":         "Capture canvas as base64 PNG.",
    # style
    "set_layer_style":               "Set renderer style. Params: layer_id, style_type ('single'|'categorized'|'graduated'), field?, classes?, color_ramp?.",
    "get_layer_crs":                 "Get layer CRS. Params: layer_id.",
    "set_layer_crs":                 "Set layer CRS. Params: layer_id, crs.",
    "get_layer_labeling":            "Get labeling config. Params: layer_id.",
    "set_layer_labeling":            "Configure labels. Params: layer_id, enabled?, field_name?, font_size?, color?.",
}

_COMMAND_LIST_STR = "\n".join(f"  {cmd}: {desc}" for cmd, desc in QGIS_COMMANDS.items())


@tool(
    name="qgis_call",
    display_name="QGIS Call",
    description=(
        "Execute a QGIS MCP command. Pass the command name and optional JSON params.\n"
        f"Available commands:\n{_COMMAND_LIST_STR}"
    ),
    category="qgis-system",
    group="qgis",
    params=[
        {"name": "command", "type": "string", "description": "Command name, e.g. 'ping', 'add_vector_layer'"},
        {"name": "params", "type": "string", "required": False, "default": "{}",
         "description": "JSON string of command parameters, e.g. '{\"path\": \"/data/roads.shp\"}'"},
    ],
    returns="dict with the command result",
    tags=["qgis", "mcp"],
    needs_context=False,
)
def qgis_call(command: str, params: str = "{}") -> dict[str, Any]:
    if command not in QGIS_COMMANDS:
        raise ValueError(
            f"Unknown QGIS command: '{command}'. "
            f"Available: {', '.join(QGIS_COMMANDS.keys())}"
        )

    parsed = json.loads(params) if isinstance(params, str) else params
    client = get_client()
    response = client.send_command(command, parsed)

    if response is None:
        raise QgisConnectionError(f"No response from QGIS for command: {command}")
    if isinstance(response, dict) and response.get("status") == "error":
        raise RuntimeError(response.get("message", "Unknown QGIS error"))
    return response.get("result", response) if isinstance(response, dict) else response
