"""
Display tools — wrap MapEngine operations as Python tools.

These tools do NOT compute anything. They emit JSON-RPC notifications
that the frontend's v3.0 ``rpc.ui.map.*`` handlers translate into
MapEngine / mapStore calls.

This is the bridge that lets the LLM "render to the map" by writing
ordinary Python code, with no awareness of TS/Electron.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union
from uuid import uuid4

from opengis_backend.tools.context import ToolContext, run_async_from_sync
from opengis_backend.tools.registry import tool


def _style_payload(
    *,
    geometry_type: Optional[str] = None,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    fill_color: Optional[str] = None,
    fill_opacity: Optional[float] = None,
    line_color: Optional[str] = None,
    line_width: Optional[float] = None,
    line_opacity: Optional[float] = None,
    border_color: Optional[str] = None,
    border_width: Optional[float] = None,
    border_opacity: Optional[float] = None,
    point_color: Optional[str] = None,
    point_size: Optional[float] = None,
    point_opacity: Optional[float] = None,
    line_dasharray: Optional[Union[str, list[float]]] = None,
) -> Optional[dict]:
    """
    Build a frontend-compatible MapLibre-ish style payload from agent-friendly
    arguments. The TS side maps these paint keys onto OpenGIS LayerStyle.
    """
    paint: dict[str, Any] = {}

    geom = (geometry_type or "").lower()
    if geom in {"point", "multipoint"}:
        style_type = "circle"
    elif geom in {"linestring", "multilinestring"}:
        style_type = "line"
    elif not geom and (point_color is not None or point_size is not None or point_opacity is not None):
        style_type = "circle"
    elif not geom and (line_color is not None or line_width is not None or line_opacity is not None or line_dasharray is not None):
        style_type = "line"
    else:
        style_type = "fill"

    if color is not None:
        if style_type == "circle":
            point_color = point_color or color
        elif style_type == "line":
            line_color = line_color or color
        else:
            fill_color = fill_color or color
    if opacity is not None:
        if style_type == "circle":
            point_opacity = point_opacity if point_opacity is not None else opacity
        elif style_type == "line":
            line_opacity = line_opacity if line_opacity is not None else opacity
        else:
            fill_opacity = fill_opacity if fill_opacity is not None else opacity

    if fill_color is not None:
        paint["fill-color"] = fill_color
    if fill_opacity is not None:
        paint["fill-opacity"] = float(fill_opacity)

    stroke_color = border_color or line_color
    stroke_width = border_width if border_width is not None else line_width
    stroke_opacity = border_opacity if border_opacity is not None else line_opacity
    if line_color is not None:
        paint["line-color"] = line_color
    if line_width is not None:
        paint["line-width"] = float(line_width)
    if line_opacity is not None:
        paint["line-opacity"] = float(line_opacity)
    if line_dasharray is not None:
        paint["line-dasharray"] = _coerce_number_list(line_dasharray)
    if stroke_color is not None:
        paint["stroke-color"] = stroke_color
        paint["circle-stroke-color"] = stroke_color
    if stroke_width is not None:
        paint["stroke-width"] = float(stroke_width)
        paint["circle-stroke-width"] = float(stroke_width)
    if stroke_opacity is not None:
        paint["stroke-opacity"] = float(stroke_opacity)

    if point_color is not None:
        paint["circle-color"] = point_color
    if point_size is not None:
        paint["circle-radius"] = float(point_size)
    if point_opacity is not None:
        paint["circle-opacity"] = float(point_opacity)

    if not paint:
        return None
    return {"type": style_type, "paint": paint}


def _coerce_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def _coerce_string_map(value: Any) -> Optional[dict[str, str]]:
    value = _coerce_jsonish(value)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object / dict")
    return {str(k): str(v) for k, v in value.items()}


def _coerce_string_list(value: Any) -> Optional[list[str]]:
    value = _coerce_jsonish(value)
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError("Expected a JSON array / list")
    return [str(item) for item in value]


def _coerce_number_list(value: Any) -> list[float]:
    value = _coerce_jsonish(value)
    if value is None:
        return []
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError("Expected a JSON array / list of numbers")
    out = [float(item) for item in value]
    if not out:
        raise ValueError("Expected at least one number")
    return out


def _visual_variable_payload(
    *,
    field: Optional[str],
    method: str = "quantile",
    classes: int = 5,
    value_range: Any = None,
    values: Any = None,
    breaks: Any = None,
) -> Optional[dict]:
    if not field:
        return None
    payload: dict[str, Any] = {
        "field": field,
        "method": method.replace("_", "-"),
        "classes": int(classes),
    }
    if value_range is not None:
        parsed_range = _coerce_number_list(value_range)
        if len(parsed_range) != 2:
            raise ValueError("value_range must contain exactly two numbers")
        payload["range"] = parsed_range
    if values is not None:
        payload["values"] = _coerce_number_list(values)
    if breaks is not None:
        payload["breaks"] = _coerce_number_list(breaks)
        if payload["method"] == "quantile":
            payload["method"] = "manual"
    return payload


def _filter_payload(attribute_filter: Any = None) -> Optional[dict]:
    if attribute_filter is None:
        return None
    parsed = _coerce_jsonish(attribute_filter)
    if isinstance(parsed, dict) and "attribute" in parsed:
        return parsed
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("attribute_filter must be a list or {'attribute': [...]}")
    return {"attribute": list(parsed)}


def _load_geojson_from_path(geojson_path: str) -> tuple[dict, str]:
    """
    Load a GeoJSON dict from a file path.
    Supports:
      - .geojson / .json  → read as GeoJSON directly
      - .shp            → use geopandas to read and convert to GeoJSON
    Returns (geojson_dict, display_name_stem).
    """
    p = Path(geojson_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {geojson_path}")

    stem = p.stem

    if p.suffix.lower() == ".shp":
        try:
            import geopandas as gpd  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "geopandas is required to read SHP files. "
                "Install it with: pip install geopandas"
            ) from e
        gdf = gpd.read_file(geojson_path)
        # Handle different CRS — warn but don't fail
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        geojson_str = gdf.to_json()
        geojson_obj: dict = json.loads(geojson_str)
        return geojson_obj, stem

    # Default: treat as GeoJSON
    geojson_obj = json.loads(p.read_text(encoding="utf-8"))
    return geojson_obj, stem

def _notify_map(ctx: ToolContext, canonical: str, payload: dict) -> None:
    """
    Emit a canonical v3.0 map command notification.

    ``map.*`` is fire-and-forget — the LLM does not await a response.
    The map command channel is ``rpc.ui.map.*``.
    """
    run_async_from_sync(ctx.notify(canonical, payload))


def _compute_geojson_bbox(geojson: dict) -> tuple[Optional[list[float]], int, Optional[str]]:
    """
    Walk a GeoJSON FeatureCollection / Feature / Geometry and return
    (bbox [minx, miny, maxx, maxy], feature_count, geometry_type).

    Returns (None, 0, None) when no coordinates are present. Ignores Z/M
    dimensions (only x, y are inspected). Gracefully handles malformed
    entries by skipping them rather than raising.
    """
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    feature_count = 0
    geom_types: dict[str, int] = {}

    def visit_coords(coords: Any) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if not coords:
            return
        # Point: [x, y, ...]
        if isinstance(coords[0], (int, float)):
            x, y = float(coords[0]), float(coords[1])
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            return
        # Nested array (LineString / Polygon / Multi*)
        for c in coords:
            visit_coords(c)

    def visit_geometry(geom: Optional[dict]) -> None:
        if not isinstance(geom, dict):
            return
        gtype = geom.get("type")
        if gtype:
            geom_types[gtype] = geom_types.get(gtype, 0) + 1
        if gtype == "GeometryCollection":
            for sub in geom.get("geometries", []) or []:
                visit_geometry(sub)
            return
        visit_coords(geom.get("coordinates"))

    gtype = geojson.get("type") if isinstance(geojson, dict) else None
    if gtype == "FeatureCollection":
        for feat in geojson.get("features", []) or []:
            feature_count += 1
            visit_geometry(feat.get("geometry"))
    elif gtype == "Feature":
        feature_count = 1
        visit_geometry(geojson.get("geometry"))
    elif gtype:  # bare geometry
        feature_count = 1
        visit_geometry(geojson)

    if min_x == float("inf"):
        return None, feature_count, _dominant_geometry_type(geom_types)
    bbox = [min_x, min_y, max_x, max_y]
    gt = _dominant_geometry_type(geom_types)
    return bbox, feature_count, gt


def _dominant_geometry_type(counts: dict[str, int]) -> Optional[str]:
    if not counts:
        return None
    priority = {
        "MultiPolygon": 60,
        "Polygon": 55,
        "MultiLineString": 50,
        "LineString": 45,
        "MultiPoint": 40,
        "Point": 35,
        "GeometryCollection": 10,
    }
    return max(counts, key=lambda key: (counts[key], priority.get(key, 0)))


def _resolve_workspace_path(ctx: ToolContext, raw_path: str) -> str:
    """Turn a (possibly-relative) path into an absolute one.

    The agent's subprocess sandbox runs with ``cwd=workspace_path``, so
    code like ``gpd.read_file("foo.geojson")`` works there. But display
    tools (add_layer, etc.) run in the **parent** worker thread whose
    cwd is the backend's launch dir — relative paths break.

    Fix: resolve against ``ctx.meta['workspace_path']`` first, then fall
    back to current cwd. Absolute paths pass through unchanged.

    NOTE: keep this helper *above* the ``# add_layer`` section so the
    ``@tool(name="add_layer", ...)`` decorator below stays glued to the
    real ``add_layer`` function. A previous refactor inserted this helper
    *between* the decorator and ``def add_layer``, which silently
    rebound ``add_layer`` to this two-arg helper and produced
    ``TypeError: _resolve_workspace_path() got an unexpected keyword
    argument 'geojson_path'`` at every call site.
    """
    p = Path(raw_path)
    if p.is_absolute():
        return str(p)
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if workspace:
        return str((Path(workspace) / raw_path).resolve())
    return str(p.resolve())


# ──────────────────────────────────────────────────────────────────────
# add_layer
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="add_layer",
    display_name="Add Map Layer",
    description=(
        "Add a vector layer (GeoJSON) to the map. Provide either a file path "
        "OR an inline GeoJSON dict. Returns a dict with layer_id, bbox, "
        "feature_count, and geometry_type. Pass the returned bbox to fly_to, "
        "or — simpler — use zoom_to_layer(layer_id) to fit the camera."
    ),
    category="visualization",
    params=[
        {"name": "geojson_path", "type": "string", "required": False,
         "description": "Path to a GeoJSON file on disk."},
        {"name": "geojson", "type": "string", "required": False,
         "description": "Inline GeoJSON content as a JSON-encoded string. Used if geojson_path is not provided."},
        {"name": "layer_id", "type": "string", "required": False,
         "description": "Optional layer id. Auto-generated if omitted."},
        {"name": "name", "type": "string", "required": False,
         "description": "Display name shown in the layer panel."},
        {"name": "color", "type": "string", "required": False,
         "description": "Fill / line color, e.g. '#ff6600'. Defaults to a system color."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Layer opacity 0.0-1.0. Default 0.8."},
        {"name": "fill_color", "type": "string", "required": False,
         "description": "Polygon fill color, e.g. '#88ccff'."},
        {"name": "fill_opacity", "type": "number", "required": False,
         "description": "Polygon fill opacity 0.0-1.0."},
        {"name": "line_color", "type": "string", "required": False,
         "description": "Line color for line layers, or polygon outline color."},
        {"name": "line_width", "type": "number", "required": False,
         "description": "Line width / polygon outline width in pixels."},
        {"name": "line_opacity", "type": "number", "required": False,
         "description": "Line opacity 0.0-1.0."},
        {"name": "line_dasharray", "type": "array", "required": False,
         "description": "Line dash pattern, e.g. [2, 2] for dashed lines."},
        {"name": "border_color", "type": "string", "required": False,
         "description": "Polygon/point border color. Alias for stroke color."},
        {"name": "border_width", "type": "number", "required": False,
         "description": "Polygon/point border width in pixels."},
        {"name": "border_opacity", "type": "number", "required": False,
         "description": "Polygon/point border opacity 0.0-1.0."},
        {"name": "point_color", "type": "string", "required": False,
         "description": "Point fill color."},
        {"name": "point_size", "type": "number", "required": False,
         "description": "Point radius in pixels."},
        {"name": "point_opacity", "type": "number", "required": False,
         "description": "Point opacity 0.0-1.0."},
    ],
    returns=(
        "dict with keys: layer_id (str), bbox (list[float] [minx, miny, maxx, maxy] "
        "or None if empty), feature_count (int), geometry_type (str or None)."
    ),
    examples=[
        "Add the buffered school zones to the map",
        "Show the parsed CSV points",
    ],
    tags=["map", "render", "layer", "visualization"],
    needs_context=True,
)
def add_layer(
    ctx: ToolContext,
    geojson_path: Optional[str] = None,
    geojson: Optional[str] = None,
    layer_id: Optional[str] = None,
    name: Optional[str] = None,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    fill_color: Optional[str] = None,
    fill_opacity: Optional[float] = None,
    line_color: Optional[str] = None,
    line_width: Optional[float] = None,
    line_opacity: Optional[float] = None,
    line_dasharray: Optional[Union[str, list[float]]] = None,
    border_color: Optional[str] = None,
    border_width: Optional[float] = None,
    border_opacity: Optional[float] = None,
    point_color: Optional[str] = None,
    point_size: Optional[float] = None,
    point_opacity: Optional[float] = None,
) -> dict:
    if not geojson_path and not geojson:
        raise ValueError("add_layer requires either geojson_path or geojson")

    payload: dict = {}
    geojson_obj: dict
    if geojson_path:
        # Relative paths are resolved against the workspace, mirroring
        # the subprocess sandbox's cwd. Without this, the LLM has to
        # guess that display tools can't see its own cwd.
        geojson_path = _resolve_workspace_path(ctx, geojson_path)
        geojson_obj, stem = _load_geojson_from_path(geojson_path)
        if not name:
            name = stem
    else:
        try:
            geojson_obj = json.loads(geojson) if isinstance(geojson, str) else geojson
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid inline GeoJSON: {e}") from e
    payload["geojson"] = geojson_obj

    if not layer_id:
        # 内容无关的唯一 id：同名图层多轮生成不互相覆盖，并行 subagent
        # 同时造层也不会因 hash 碰撞而丢失。
        layer_id = f"layer_{uuid4().hex}"

    payload["layer_id"] = layer_id
    payload["name"] = name or layer_id
    # Compute bbox locally so we can return it synchronously to the
    # agent's python sandbox — the frontend doesn't need to round-trip.
    bbox, feature_count, geometry_type = _compute_geojson_bbox(geojson_obj)
    if bbox is not None:
        payload["bbox"] = bbox
    style = _style_payload(
        geometry_type=geometry_type,
        color=color,
        opacity=opacity,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
        line_color=line_color,
        line_width=line_width,
        line_opacity=line_opacity,
        line_dasharray=line_dasharray,
        border_color=border_color,
        border_width=border_width,
        border_opacity=border_opacity,
        point_color=point_color,
        point_size=point_size,
        point_opacity=point_opacity,
    )
    if style:
        payload["style"] = style

    _notify_map(ctx, "rpc.ui.map.add_layer_from_geojson", payload)

    return {
        "layer_id": layer_id,
        "bbox": bbox,
        "feature_count": feature_count,
        "geometry_type": geometry_type,
        "name": payload["name"],
    }


# ──────────────────────────────────────────────────────────────────────
# remove_layer
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="remove_layer",
    display_name="Remove Map Layer",
    description="Remove a previously added layer from the map by its layer_id.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id returned by add_layer."},
    ],
    returns="True if the removal command was sent.",
    examples=["Remove the buffer layer"],
    tags=["map", "render", "layer"],
    needs_context=True,
)
def remove_layer(ctx: ToolContext, layer_id: str) -> bool:
    _notify_map(ctx, "rpc.ui.map.remove_layer", {"layer_id": layer_id})
    return True


# ──────────────────────────────────────────────────────────────────────
# map read tools
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="list_layers",
    display_name="List Map Layers",
    description=(
        "Read the current frontend map layer list. Use this whenever the user "
        "asks what layers are on the map, how many layers exist, or asks for "
        "current layer ids/names."
    ),
    category="visualization",
    params=[],
    returns="dict with keys: layers (list of layer summaries) and count (int).",
    examples=["How many layers are on the current map?", "List current layer ids"],
    tags=["map", "layer", "inspect", "current-state"],
    needs_context=True,
)
def list_layers(ctx: ToolContext) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.map.list_layers", {}))


@tool(
    name="get_layer",
    display_name="Get Map Layer",
    description=(
        "Read metadata for one current frontend map layer by id. Use after "
        "list_layers when you need bbox, geometry type, fields, visibility, "
        "or renderer/style information."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "Layer id from list_layers()."},
    ],
    returns="dict containing the layer summary, fields, style, bbox, and visibility.",
    examples=["Get metadata for the Shanghai districts layer"],
    tags=["map", "layer", "inspect", "current-state"],
    needs_context=True,
)
def get_layer(ctx: ToolContext, layer_id: str) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.map.get_layer", {"layer_id": layer_id}))


@tool(
    name="query_features",
    display_name="Query Map Features",
    description=(
        "Query features from a current frontend vector layer by attribute, bbox, "
        "or point. Use this only when the user asks to inspect actual feature "
        "records; use list_layers/get_layer for layer inventory."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "Vector layer id from list_layers()."},
        {"name": "filter", "type": "object", "required": False,
         "description": "Optional filter dict: {'attribute': [{'field': str, 'op': '=', 'value': any}], 'bbox': [minx,miny,maxx,maxy], 'point': [lng,lat]}."},
        {"name": "limit", "type": "number", "required": False,
         "description": "Maximum number of features to return. Default 1000."},
    ],
    returns="dict with keys: layer_id, total_matched, truncated, features.",
    examples=["Query the selected layer for POI type='school'"],
    tags=["map", "layer", "feature", "inspect", "query"],
    needs_context=True,
)
def query_features(
    ctx: ToolContext,
    layer_id: str,
    filter: Optional[dict] = None,
    limit: Optional[float] = None,
) -> dict:
    payload: dict = {"layer_id": layer_id}
    if filter is not None:
        payload["filter"] = filter
    if limit is not None:
        payload["limit"] = int(limit)
    return run_async_from_sync(ctx.request("rpc.ui.map.query_features", payload))


# ──────────────────────────────────────────────────────────────────────
# fly_to
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="fly_to",
    display_name="Fly Camera",
    description=(
        "Animate the map camera to a target. Provide either (lng, lat) plus optional zoom, "
        "OR a bbox [minx, miny, maxx, maxy] to fit. Use after add_layer to focus the user. "
        "TIP: If you just added a layer, zoom_to_layer(layer_id) is simpler."
    ),
    category="visualization",
    params=[
        {"name": "lng", "type": "number", "required": False,
         "description": "Target longitude."},
        {"name": "lat", "type": "number", "required": False,
         "description": "Target latitude."},
        {"name": "zoom", "type": "number", "required": False,
         "description": "Target zoom level (0-22)."},
        {"name": "pitch", "type": "number", "required": False,
         "description": "Camera pitch in degrees (0=top-down, 45-70=oblique 3D)."},
        {"name": "bearing", "type": "number", "required": False,
         "description": "Camera bearing/rotation in degrees."},
        {"name": "bbox", "type": "array", "required": False,
         "description": "Bounding box as a list [minx, miny, maxx, maxy] OR a JSON-encoded string. Overrides lng/lat."},
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms. Default 1500."},
    ],
    returns="dict with keys: success (bool), target (str)",
    examples=["Fly to Beijing", "Zoom to the buffered features"],
    tags=["map", "camera", "navigation"],
    needs_context=True,
)
def fly_to(
    ctx: ToolContext,
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    zoom: Optional[float] = None,
    pitch: Optional[float] = None,
    bearing: Optional[float] = None,
    bbox: Optional[Union[str, list, tuple]] = None,
    duration: Optional[float] = None,
) -> dict:
    payload: dict = {}
    if bbox is not None:
        arr: Any = bbox
        # Accept either a native sequence or a JSON string.
        if isinstance(bbox, str):
            s = bbox.strip()
            if not s:
                arr = None
            else:
                try:
                    arr = json.loads(s)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid bbox JSON: {e}") from e
        if arr is None or not (isinstance(arr, (list, tuple)) and len(arr) == 4):
            raise ValueError(
                "bbox must be a 4-element list [minx, miny, maxx, maxy] "
                "or a JSON-encoded string of the same."
            )
        payload["bbox"] = [float(x) for x in arr]
    elif lng is not None and lat is not None:
        payload["center"] = [float(lng), float(lat)]
        if zoom is not None:
            payload["zoom"] = float(zoom)
    else:
        raise ValueError("fly_to requires either (lng, lat) or bbox")

    if duration is not None:
        payload["duration"] = float(duration)
    if pitch is not None:
        payload["pitch"] = float(pitch)
    if bearing is not None:
        payload["bearing"] = float(bearing)

    # Route to the right canonical method based on payload shape.
    if "bbox" in payload:
        _notify_map(ctx, "rpc.ui.map.zoom_to_bbox", payload)
        if pitch is not None or bearing is not None:
            camera_payload: dict = {}
            if pitch is not None:
                camera_payload["pitch"] = float(pitch)
            if bearing is not None:
                camera_payload["bearing"] = float(bearing)
            if duration is not None:
                camera_payload["duration"] = float(duration)
            run_async_from_sync(ctx.request("rpc.ui.map.set_camera", camera_payload))
        return {"success": True, "target": f"bbox={payload['bbox']}"}
    else:
        _notify_map(ctx, "rpc.ui.map.fly_to", payload)
        return {"success": True, "target": f"center=[{lng}, {lat}]"}


# ──────────────────────────────────────────────────────────────────────
# set_map_camera / 3D view shortcuts
# ──────────────────────────────────────────────────────────────────────
def _set_map_camera(
    ctx: ToolContext,
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    zoom: Optional[float] = None,
    pitch: Optional[float] = None,
    bearing: Optional[float] = None,
    duration: Optional[float] = None,
) -> dict:
    payload: dict = {}
    if lng is not None or lat is not None:
        if lng is None or lat is None:
            raise ValueError("set_map_camera requires both lng and lat when setting center")
        payload["center"] = [float(lng), float(lat)]
    if zoom is not None:
        payload["zoom"] = float(zoom)
    if pitch is not None:
        payload["pitch"] = float(pitch)
    if bearing is not None:
        payload["bearing"] = float(bearing)
    if duration is not None:
        payload["duration"] = float(duration)
    if not payload:
        raise ValueError("set_map_camera requires at least one camera field")
    return run_async_from_sync(ctx.request("rpc.ui.map.set_camera", payload))


@tool(
    name="set_map_camera",
    display_name="Set Map Camera",
    description=(
        "Set the map camera fields directly. Use this for view-only changes, "
        "including 3D oblique view. Provide any subset of lng/lat, zoom, pitch, "
        "bearing, duration. pitch=0 is top-down 2D; pitch around 45-70 shows 3D "
        "extrusion layers clearly."
    ),
    category="visualization",
    params=[
        {"name": "lng", "type": "number", "required": False,
         "description": "Target longitude. Must be paired with lat."},
        {"name": "lat", "type": "number", "required": False,
         "description": "Target latitude. Must be paired with lng."},
        {"name": "zoom", "type": "number", "required": False,
         "description": "Target zoom level."},
        {"name": "pitch", "type": "number", "required": False,
         "description": "Camera pitch in degrees, 0-85."},
        {"name": "bearing", "type": "number", "required": False,
         "description": "Camera bearing/rotation in degrees."},
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms."},
    ],
    returns="dict with keys: center, zoom, pitch, bearing",
    examples=["Tilt the map into a 3D view", "Rotate the map 30 degrees"],
    tags=["map", "camera", "3d", "view"],
    needs_context=True,
)
def set_map_camera(
    ctx: ToolContext,
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    zoom: Optional[float] = None,
    pitch: Optional[float] = None,
    bearing: Optional[float] = None,
    duration: Optional[float] = None,
) -> dict:
    return _set_map_camera(
        ctx,
        lng=lng,
        lat=lat,
        zoom=zoom,
        pitch=pitch,
        bearing=bearing,
        duration=duration,
    )


@tool(
    name="enter_3d_view",
    display_name="Enter 3D View",
    description=(
        "Switch the map into an oblique 3D viewing angle. This changes only the "
        "camera pitch/bearing/optional zoom; it does not change basemap or layer "
        "visibility. Use after extrusion rendering or when the user asks for 3D view."
    ),
    category="visualization",
    params=[
        {"name": "pitch", "type": "number", "required": False,
         "description": "Pitch in degrees. Default 60."},
        {"name": "bearing", "type": "number", "required": False,
         "description": "Bearing in degrees. Default -25."},
        {"name": "zoom", "type": "number", "required": False,
         "description": "Optional zoom level."},
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms. Default 800."},
    ],
    returns="dict with keys: center, zoom, pitch, bearing",
    examples=["Switch to 3D view", "Show this layer from an oblique angle"],
    tags=["map", "camera", "3d", "view"],
    needs_context=True,
)
def enter_3d_view(
    ctx: ToolContext,
    pitch: Optional[float] = None,
    bearing: Optional[float] = None,
    zoom: Optional[float] = None,
    duration: Optional[float] = None,
) -> dict:
    return _set_map_camera(
        ctx,
        pitch=60.0 if pitch is None else pitch,
        bearing=-25.0 if bearing is None else bearing,
        zoom=zoom,
        duration=800.0 if duration is None else duration,
    )


@tool(
    name="exit_3d_view",
    display_name="Exit 3D View",
    description="Return the map camera to top-down 2D view by resetting pitch and bearing.",
    category="visualization",
    params=[
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms. Default 600."},
    ],
    returns="dict with keys: center, zoom, pitch, bearing",
    examples=["Return to 2D view", "Reset the map rotation"],
    tags=["map", "camera", "2d", "view"],
    needs_context=True,
)
def exit_3d_view(ctx: ToolContext, duration: Optional[float] = None) -> dict:
    return _set_map_camera(
        ctx,
        pitch=0.0,
        bearing=0.0,
        duration=600.0 if duration is None else duration,
    )


# ──────────────────────────────────────────────────────────────────────
# zoom_to_layer
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="zoom_to_layer",
    display_name="Zoom To Layer",
    description=(
        "Fit the map camera to the extent of a previously-added layer. "
        "This is the simplest way to focus on a layer you just added — "
        "no need to compute bbox manually. Internally uses the bbox captured "
        "by add_layer."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id returned by add_layer (via info['layer_id'])."},
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms. Default 1500."},
        {"name": "padding", "type": "number", "required": False,
         "description": "Extra padding in pixels around the bbox when fitting. Default 40."},
    ],
    returns="dict with keys: success (bool), layer_id (str)",
    examples=["Zoom to the points layer I just added"],
    tags=["map", "camera", "layer", "navigation"],
    needs_context=True,
)
def zoom_to_layer(
    ctx: ToolContext,
    layer_id: str,
    duration: Optional[float] = None,
    padding: Optional[float] = None,
) -> dict:
    payload: dict = {"layer_id": layer_id}
    if duration is not None:
        payload["duration"] = float(duration)
    if padding is not None:
        payload["padding"] = float(padding)
    _notify_map(ctx, "rpc.ui.map.zoom_to_layer", payload)
    return {"success": True, "layer_id": layer_id}


# ──────────────────────────────────────────────────────────────────────
# set_basemap
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="set_basemap",
    display_name="Set Basemap",
    description=(
        "Disabled for autonomous agents. Basemap selection is user-controlled UI state. "
        "Use set_basemap_visibility(False) only when the user explicitly asks to hide the basemap."
    ),
    category="visualization",
    params=[
        {"name": "basemap_id", "type": "string", "required": True,
         "description": "Basemap identifier."},
    ],
    returns="dict with keys: success (bool), basemap_id (str)",
    examples=["Switch to satellite basemap"],
    tags=["map", "basemap"],
    needs_context=True,
)
def set_basemap(ctx: ToolContext, basemap_id: str) -> dict:
    return {
        "success": False,
        "basemap_id": basemap_id,
        "error": (
            "Agent-initiated basemap switching is disabled. "
            "Ask the user to change the basemap in the UI, or use set_basemap_visibility "
            "only if the user explicitly asked to show/hide the current basemap."
        ),
    }


# ──────────────────────────────────────────────────────────────────────
# set_basemap_visibility
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="set_basemap_visibility",
    display_name="Set Basemap Visibility",
    description="Show or hide the basemap without changing vector/raster layer visibility.",
    category="visualization",
    params=[
        {"name": "visible", "type": "boolean", "required": True,
         "description": "True to show the basemap, False to hide it."},
    ],
    returns="dict with keys: success (bool), visible (bool)",
    examples=["Hide the basemap", "Show the basemap again"],
    tags=["map", "basemap", "visibility"],
    needs_context=True,
)
def set_basemap_visibility(ctx: ToolContext, visible: bool) -> dict:
    run_async_from_sync(
        ctx.notify("rpc.ui.map.set_basemap_visibility", {"visible": bool(visible)})
    )
    return {"success": True, "visible": bool(visible)}


# ──────────────────────────────────────────────────────────────────────
# get_map_state
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="get_map_state",
    display_name="Get Map State",
    description=(
        "Read current map camera and basemap state without changing the map. "
        "Use this when the user asks about current zoom/location, whether the "
        "basemap is visible, or before making camera/layout decisions."
    ),
    category="visualization",
    params=[],
    returns=(
        "dict with keys: basemap, basemap_visible, labels_visible, view_state, "
        "layer_count."
    ),
    examples=["What basemap is currently shown?", "Where is the map currently centered?"],
    tags=["map", "basemap", "camera", "inspect", "current-state"],
    needs_context=True,
)
def get_map_state(ctx: ToolContext) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.map.get_state", {}))


# ──────────────────────────────────────────────────────────────────────
# update_layer_style
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="update_layer_style",
    display_name="Update Layer Style",
    description=(
        "Change color, opacity, line/border width, point size, or visibility "
        "of an existing vector/raster layer."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id to update."},
        {"name": "color", "type": "string", "required": False,
         "description": "New color, e.g. '#3388ff'."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "New opacity 0.0-1.0."},
        {"name": "fill_color", "type": "string", "required": False,
         "description": "Polygon fill color."},
        {"name": "fill_opacity", "type": "number", "required": False,
         "description": "Polygon fill opacity 0.0-1.0."},
        {"name": "line_color", "type": "string", "required": False,
         "description": "Line color for line layers, or polygon outline color."},
        {"name": "line_width", "type": "number", "required": False,
         "description": "Line width / polygon outline width in pixels."},
        {"name": "line_opacity", "type": "number", "required": False,
         "description": "Line opacity 0.0-1.0."},
        {"name": "line_dasharray", "type": "array", "required": False,
         "description": "Line dash pattern, e.g. [2, 2] for dashed lines."},
        {"name": "border_color", "type": "string", "required": False,
         "description": "Polygon/point border color."},
        {"name": "border_width", "type": "number", "required": False,
         "description": "Polygon/point border width in pixels."},
        {"name": "border_opacity", "type": "number", "required": False,
         "description": "Polygon/point border opacity 0.0-1.0."},
        {"name": "point_color", "type": "string", "required": False,
         "description": "Point fill color."},
        {"name": "point_size", "type": "number", "required": False,
         "description": "Point radius in pixels."},
        {"name": "point_opacity", "type": "number", "required": False,
         "description": "Point opacity 0.0-1.0."},
        {"name": "visible", "type": "boolean", "required": False,
         "description": "Show / hide the layer."},
    ],
    returns="dict with keys: success (bool), layer_id (str)",
    examples=["Make the buffer layer red", "Hide the points layer"],
    tags=["map", "style", "layer"],
    needs_context=True,
)
def update_layer_style(
    ctx: ToolContext,
    layer_id: str,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    fill_color: Optional[str] = None,
    fill_opacity: Optional[float] = None,
    line_color: Optional[str] = None,
    line_width: Optional[float] = None,
    line_opacity: Optional[float] = None,
    line_dasharray: Optional[Union[str, list[float]]] = None,
    border_color: Optional[str] = None,
    border_width: Optional[float] = None,
    border_opacity: Optional[float] = None,
    point_color: Optional[str] = None,
    point_size: Optional[float] = None,
    point_opacity: Optional[float] = None,
    visible: Optional[bool] = None,
) -> dict:
    payload: dict = {"layer_id": layer_id}
    if color is not None:
        payload["color"] = color
    if opacity is not None:
        payload["opacity"] = float(opacity)
    if visible is not None:
        payload["visible"] = bool(visible)
    geometry_type: Optional[str] = None
    try:
        layer_info = run_async_from_sync(ctx.request("rpc.ui.map.get_layer", {"layer_id": layer_id}))
        if isinstance(layer_info, dict):
            raw_geometry = layer_info.get("geometry_type")
            if isinstance(raw_geometry, str):
                geometry_type = raw_geometry
    except Exception:
        # Styling can still be attempted without layer metadata. The frontend
        # will reject unknown layer ids; this fallback only preserves old
        # fire-and-forget behavior if the read channel is temporarily absent.
        geometry_type = None
    style = _style_payload(
        geometry_type=geometry_type,
        color=color,
        opacity=opacity,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
        line_color=line_color,
        line_width=line_width,
        line_opacity=line_opacity,
        line_dasharray=line_dasharray,
        border_color=border_color,
        border_width=border_width,
        border_opacity=border_opacity,
        point_color=point_color,
        point_size=point_size,
        point_opacity=point_opacity,
    )

    # Canonical v3.0: style and visibility go through different handlers.
    # Only send the canonical messages that have meaningful payload.
    if style is not None:
        style_payload: dict = {"layer_id": layer_id, "style": style}
        run_async_from_sync(ctx.notify("rpc.ui.map.set_layer_style", style_payload))
    if visible is not None:
        run_async_from_sync(
            ctx.notify("rpc.ui.map.set_layer_visibility",
                       {"layer_id": layer_id, "visible": bool(visible)})
        )
    return {
        "success": True,
        "layer_id": layer_id,
        "note": "Style update sent to map. Verify visually that the change took effect.",
    }


# ──────────────────────────────────────────────────────────────────────
# set_graduated_style  (choropleth / graduated renderer)
# ──────────────────────────────────────────────────────────────────────

_COLOR_RAMPS: dict[str, list[str]] = {
    "viridis": ["#fde725", "#5ec962", "#21918c", "#3b528b", "#440154"],
    "plasma": ["#f0f921", "#f89540", "#cc4778", "#7e03a8", "#0d0887"],
    "inferno": ["#fcffa4", "#f98e09", "#bc3754", "#57106e", "#000004"],
    "magma": ["#fcfdbf", "#fb8861", "#b73779", "#51127c", "#000004"],
    "reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "oranges": ["#feedde", "#fdbe85", "#fd8d3c", "#e6550d", "#a63603"],
    "purples": ["#f3e8ff", "#d8b4fe", "#a855f7", "#7e22ce", "#581c87"],
    "purple": ["#f3e8ff", "#d8b4fe", "#a855f7", "#7e22ce", "#581c87"],
    "rdylgn": ["#d73027", "#fc8d59", "#ffffbf", "#91cf60", "#1a9850"],
    "rdylbu": ["#d73027", "#fc8d59", "#ffffbf", "#91bfdb", "#4575b4"],
    "spectral": ["#9e0142", "#f46d43", "#ffffbf", "#66c2a5", "#5e4fa2"],
    "tableau10": ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f"],
}


def _resolve_color_ramp(value: Any, classes: int) -> list[str]:
    parsed = _coerce_jsonish(value)
    if isinstance(parsed, str):
        key = parsed.strip().lower()
        if key in _COLOR_RAMPS:
            base = _COLOR_RAMPS[key]
            if classes == len(base):
                return list(base)
            return [base[round((i / max(classes - 1, 1)) * (len(base) - 1))] for i in range(classes)]
        parsed = [item.strip() for item in parsed.split(",") if item.strip()]
    colors = _coerce_string_list(parsed)
    if colors is None or not colors:
        return _resolve_color_ramp("viridis", classes)
    return colors


@tool(
    name="set_graduated_style",
    display_name="Set Graduated Style",
    description=(
        "Apply graduated (choropleth) styling to a vector layer. "
        "Colors features by a numeric field using classification."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id to style."},
        {"name": "field", "type": "string", "required": True,
         "description": "Numeric field name to classify by (e.g. 'population', 'area')."},
        {"name": "method", "type": "string", "required": False, "default": "quantile",
         "description": "Classification method: 'quantile', 'equal-interval', 'jenks', or 'manual'."},
        {"name": "classes", "type": "number", "required": False, "default": 5,
         "description": "Number of classes (2-12)."},
        {"name": "palette", "type": "string", "required": False, "default": "viridis",
         "description": f"Color ramp name or JSON/list of colors. Options: {', '.join(_COLOR_RAMPS)}."},
        {"name": "breaks", "type": "array", "required": False,
         "description": "Manual numeric breaks. If provided, method becomes 'manual' unless explicitly set."},
    ],
    returns="dict with keys: success (bool), layer_id (str), field (str), method (str), classes (int)",
    examples=["Style districts layer by population with 5 quantile classes", "Choropleth the provinces layer by GDP using blues palette"],
    tags=["map", "style", "choropleth", "graduated"],
    needs_context=True,
)
def set_graduated_style(
    ctx: ToolContext,
    layer_id: str,
    field: str,
    method: str = "quantile",
    classes: int = 5,
    palette: Any = "viridis",
    breaks: Any = None,
) -> dict:
    # Normalise method: Python callers often use underscores (e.g.
    # "equal_interval") but the frontend Zod schema expects hyphens
    # ("equal-interval").
    method = method.replace("_", "-")
    parsed_breaks = _coerce_number_list(breaks) if breaks is not None else None
    if parsed_breaks is not None and method == "quantile":
        method = "manual"
    parsed_palette = _resolve_color_ramp(palette, int(classes))

    payload = {
        "layer_id": layer_id,
        "renderer": "graduated",
        "graduated": {
            "field": field,
            "method": method,
            "classes": int(classes),
            "palette": parsed_palette,
            **({"breaks": parsed_breaks} if parsed_breaks is not None else {}),
        },
    }
    result = run_async_from_sync(ctx.request(
        "rpc.ui.map.set_layer_renderer",
        payload,
    ))
    return {
        "success": True,
        "layer_id": layer_id,
        "field": field,
        "method": method,
        "classes": int(classes),
        **(result or {}),
    }


# ──────────────────────────────────────────────────────────────────────
# set_categorized_style  (categorical renderer)
# ──────────────────────────────────────────────────────────────────────

@tool(
    name="set_categorized_style",
    display_name="Set Categorized Style",
    description=(
        "Apply categorized styling to a vector layer. "
        "Assigns unique colors to each unique value of a text/enum field."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id to style."},
        {"name": "field", "type": "string", "required": True,
         "description": "Categorical field name (e.g. 'type', 'land_use')."},
        {"name": "max_categories", "type": "number", "required": False, "default": 10,
         "description": "Max unique categories before grouping as 'other' (1-64)."},
        {"name": "other_color", "type": "string", "required": False, "default": "#cccccc",
         "description": "Color for values beyond max_categories."},
        {"name": "colors", "type": "object", "required": False,
         "description": "Explicit category color mapping as dict/JSON: {'Cafe':'#ef4444'}."},
        {"name": "categories", "type": "array", "required": False,
         "description": "Optional fixed category order / whitelist."},
    ],
    returns="dict with keys: success (bool), layer_id (str), field (str)",
    examples=["Color land_use layer by type", "Categorize roads layer by road_class"],
    tags=["map", "style", "categorized"],
    needs_context=True,
)
def set_categorized_style(
    ctx: ToolContext,
    layer_id: str,
    field: str,
    max_categories: int = 10,
    other_color: str = "#cccccc",
    colors: Any = None,
    categories: Any = None,
) -> dict:
    parsed_colors = _coerce_string_map(colors)
    parsed_categories = _coerce_string_list(categories)
    payload = {
        "layer_id": layer_id,
        "renderer": "categorized",
        "categorized": {
            "field": field,
            "maxCategories": int(max_categories),
            "otherColor": other_color,
            **({"colors": parsed_colors} if parsed_colors is not None else {}),
            **({"categories": parsed_categories} if parsed_categories is not None else {}),
        },
    }
    result = run_async_from_sync(ctx.request(
        "rpc.ui.map.set_layer_renderer",
        payload,
    ))
    return {"success": True, "layer_id": layer_id, "field": field, **(result or {})}


@tool(
    name="set_extrusion_style",
    display_name="Set 3D Extrusion Style",
    description=(
        "Extrude polygon features into 3D using a numeric height field. "
        "Use this for buildings, 3D choropleths, or any user request involving "
        "'3D', 'extrude', '拉伸', '拔起', or building height. This is the canonical "
        "tool for layer extrusion; do not use set_layer_visual_variables for extrusion."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "Polygon layer id to extrude."},
        {"name": "height_field", "type": "string", "required": True,
         "description": "Numeric field containing extrusion height, e.g. height or floors_m."},
        {"name": "height_multiplier", "type": "number", "required": False, "default": 1,
         "description": "Multiplier applied to height_field. Use >1 when heights are too subtle."},
        {"name": "base_field", "type": "string", "required": False,
         "description": "Optional numeric base/elevation field."},
        {"name": "color", "type": "string", "required": False,
         "description": "Optional extrusion color, e.g. #f59e0b."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Optional extrusion opacity 0-1."},
        {"name": "enter_3d", "type": "boolean", "required": False, "default": False,
         "description": "Also tilt the map camera. Use true only when the user asks for 3D/oblique view or wants an angled preview."},
        {"name": "pitch", "type": "number", "required": False, "default": 60,
         "description": "Camera pitch when enter_3d is true."},
        {"name": "bearing", "type": "number", "required": False, "default": -25,
         "description": "Camera bearing when enter_3d is true."},
        {"name": "zoom", "type": "number", "required": False,
         "description": "Optional zoom when entering 3D view."},
    ],
    returns="dict with keys: success, layer_id, height_field, extrusion, camera",
    examples=[
        "Extrude campus buildings by height and switch to 3D view",
        "拉伸建筑图层，height 字段作为高度，并进入三维视角",
    ],
    tags=["map", "style", "3d", "extrusion", "building", "拉伸"],
    needs_context=True,
)
def set_extrusion_style(
    ctx: ToolContext,
    layer_id: str,
    height_field: str,
    height_multiplier: float = 1,
    base_field: Optional[str] = None,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    enter_3d: bool = False,
    pitch: Optional[float] = None,
    bearing: Optional[float] = None,
    zoom: Optional[float] = None,
) -> dict:
    payload: dict[str, Any] = {
        "layer_id": layer_id,
        "renderer": "extrusion",
        "extrusion": {
            "heightField": height_field,
            "heightMultiplier": float(height_multiplier),
        },
    }
    if base_field:
        payload["extrusion"]["baseField"] = base_field

    renderer_result = run_async_from_sync(ctx.request("rpc.ui.map.set_layer_renderer", payload))

    style_result: dict[str, Any] | None = None
    paint: dict[str, Any] = {}
    if color is not None:
        paint["fill-color"] = color
    if opacity is not None:
        paint["fill-opacity"] = float(opacity)
    if paint:
        style_result = run_async_from_sync(ctx.request(
            "rpc.ui.map.set_layer_style",
            {"layer_id": layer_id, "style": {"type": "fill", "paint": paint}},
        ))

    camera_result: dict[str, Any] | None = None
    if enter_3d:
        camera_result = _set_map_camera(
            ctx,
            pitch=60.0 if pitch is None else pitch,
            bearing=-25.0 if bearing is None else bearing,
            zoom=zoom,
            duration=800.0,
        )

    return {
        "success": True,
        "layer_id": layer_id,
        "height_field": height_field,
        "height_multiplier": float(height_multiplier),
        "extrusion": payload["extrusion"],
        "renderer": renderer_result,
        "style": style_result,
        "camera": camera_result,
    }


@tool(
    name="set_layer_visual_variables",
    display_name="Set Layer Visual Variables",
    description=(
        "Overlay field-driven size and/or opacity on an existing vector layer renderer. "
        "Use after set_categorized_style or set_graduated_style when color plus size/opacity are both needed."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id to style."},
        {"name": "size_field", "type": "string", "required": False,
         "description": "Numeric field mapped to point radius, line width, or polygon border width."},
        {"name": "size_method", "type": "string", "required": False, "default": "quantile",
         "description": "Classification method for size: quantile, equal-interval, jenks, manual."},
        {"name": "size_classes", "type": "number", "required": False, "default": 5,
         "description": "Number of size classes."},
        {"name": "size_range", "type": "array", "required": False,
         "description": "Output size range, e.g. [3, 16] pixels."},
        {"name": "size_values", "type": "array", "required": False,
         "description": "Explicit output size values per class."},
        {"name": "size_breaks", "type": "array", "required": False,
         "description": "Manual numeric breaks for size classes."},
        {"name": "opacity_field", "type": "string", "required": False,
         "description": "Numeric field mapped to feature opacity."},
        {"name": "opacity_method", "type": "string", "required": False, "default": "quantile",
         "description": "Classification method for opacity: quantile, equal-interval, jenks, manual."},
        {"name": "opacity_classes", "type": "number", "required": False, "default": 5,
         "description": "Number of opacity classes."},
        {"name": "opacity_range", "type": "array", "required": False,
         "description": "Output opacity range, e.g. [0.25, 0.9]."},
        {"name": "opacity_values", "type": "array", "required": False,
         "description": "Explicit output opacity values per class."},
        {"name": "opacity_breaks", "type": "array", "required": False,
         "description": "Manual numeric breaks for opacity classes."},
        {"name": "sort_field", "type": "string", "required": False,
         "description": "Numeric field controlling feature draw order inside the layer."},
        {"name": "sort_order", "type": "string", "required": False, "default": "descending",
         "description": "Feature draw order: 'descending' draws higher values on top; 'ascending' draws lower values on top."},
        {"name": "clear_size", "type": "boolean", "required": False,
         "description": "Clear existing size visual variable."},
        {"name": "clear_opacity", "type": "boolean", "required": False,
         "description": "Clear existing opacity visual variable."},
        {"name": "clear_sort", "type": "boolean", "required": False,
         "description": "Clear existing feature draw order variable."},
    ],
    returns="dict with keys: success, layer_id, size_variable, opacity_variable, sort_variable",
    examples=[
        "Color POIs by type, then set point size by comment_count and opacity by rating",
        "Make road line width follow traffic volume",
    ],
    tags=["map", "style", "visual-variable", "symbol"],
    needs_context=True,
)
def set_layer_visual_variables(
    ctx: ToolContext,
    layer_id: str,
    size_field: Optional[str] = None,
    size_method: str = "quantile",
    size_classes: int = 5,
    size_range: Any = None,
    size_values: Any = None,
    size_breaks: Any = None,
    opacity_field: Optional[str] = None,
    opacity_method: str = "quantile",
    opacity_classes: int = 5,
    opacity_range: Any = None,
    opacity_values: Any = None,
    opacity_breaks: Any = None,
    sort_field: Optional[str] = None,
    sort_order: str = "descending",
    clear_size: bool = False,
    clear_opacity: bool = False,
    clear_sort: bool = False,
) -> dict:
    payload: dict[str, Any] = {"layer_id": layer_id}
    if clear_size:
        payload["size_variable"] = None
    elif size_field:
        payload["size_variable"] = _visual_variable_payload(
            field=size_field,
            method=size_method,
            classes=size_classes,
            value_range=size_range,
            values=size_values,
            breaks=size_breaks,
        )
    if clear_opacity:
        payload["opacity_variable"] = None
    elif opacity_field:
        payload["opacity_variable"] = _visual_variable_payload(
            field=opacity_field,
            method=opacity_method,
            classes=opacity_classes,
            value_range=opacity_range,
            values=opacity_values,
            breaks=opacity_breaks,
        )
    if clear_sort:
        payload["sort_variable"] = None
    elif sort_field:
        normalized_order = str(sort_order or "descending").replace("_", "-").lower()
        if normalized_order in {"desc", "high", "higher", "high-on-top", "高值在上"}:
            normalized_order = "descending"
        elif normalized_order in {"asc", "low", "lower", "low-on-top", "低值在上"}:
            normalized_order = "ascending"
        if normalized_order not in {"ascending", "descending"}:
            raise ValueError("sort_order must be 'ascending' or 'descending'")
        payload["sort_variable"] = {"field": sort_field, "order": normalized_order}
    if len(payload) == 1:
        raise ValueError("Provide size_field, opacity_field, sort_field, clear_size, clear_opacity, or clear_sort")
    result = run_async_from_sync(ctx.request("rpc.ui.map.update_visual_variables", payload))
    return {"success": True, "layer_id": layer_id, **(result or {})}


# ──────────────────────────────────────────────────────────────────────
# semantic layer operations
# ──────────────────────────────────────────────────────────────────────

@tool(
    name="set_layer_filter",
    display_name="Set Layer Display Filter",
    description="Apply or clear an attribute display filter on a map layer without deleting data.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True, "description": "Layer id."},
        {"name": "attribute_filter", "type": "array", "required": False,
         "description": "List of filters: [{'field':'rating','op':'>=','value':4.5}]. Pass null/omit to clear."},
    ],
    returns="dict with keys: success, layer_id, filter",
    examples=["Only show POIs with rating >= 4.5"],
    tags=["map", "style", "filter", "layer"],
    needs_context=True,
)
def set_layer_filter(ctx: ToolContext, layer_id: str, attribute_filter: Any = None) -> dict:
    payload = {"layer_id": layer_id, "filter": _filter_payload(attribute_filter)}
    run_async_from_sync(ctx.notify("rpc.ui.map.set_layer_filter", payload))
    return {"success": True, **payload}


@tool(
    name="set_layer_label",
    display_name="Set Layer Labels",
    description="Show, update, or hide labels for a vector layer while preserving its existing renderer.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True, "description": "Layer id."},
        {"name": "field", "type": "string", "required": False, "description": "Property field used as label text."},
        {"name": "visible", "type": "boolean", "required": False, "description": "False hides labels."},
        {"name": "font_size", "type": "number", "required": False, "description": "Label font size in pixels."},
        {"name": "color", "type": "string", "required": False, "description": "Label text color."},
        {"name": "halo_color", "type": "string", "required": False, "description": "Text halo color."},
        {"name": "halo_width", "type": "number", "required": False, "description": "Text halo width."},
    ],
    returns="dict with keys: success, layer_id",
    examples=["Label the district layer by NAME"],
    tags=["map", "label", "symbol", "style"],
    needs_context=True,
)
def set_layer_label(
    ctx: ToolContext,
    layer_id: str,
    field: Optional[str] = None,
    visible: Optional[bool] = None,
    font_size: Optional[float] = None,
    color: Optional[str] = None,
    halo_color: Optional[str] = None,
    halo_width: Optional[float] = None,
) -> dict:
    payload = {
        "layer_id": layer_id,
        "field": field,
        "visible": visible,
        "font_size": font_size,
        "color": color,
        "halo_color": halo_color,
        "halo_width": halo_width,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    run_async_from_sync(ctx.notify("rpc.ui.map.set_layer_label", payload))
    return {"success": True, "layer_id": layer_id}


@tool(
    name="highlight_features",
    display_name="Highlight Features",
    description=(
        "Create or replace a separate highlight overlay layer from features "
        "matching an attribute filter. This does not mutate the source layer style."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True, "description": "Source layer id."},
        {"name": "attribute_filter", "type": "array", "required": False,
         "description": "List of filters: [{'field':'type','op':'=','value':'Cafe'}]."},
        {"name": "name", "type": "string", "required": False, "description": "Highlight layer display name."},
        {"name": "color", "type": "string", "required": False, "description": "Highlight color."},
        {"name": "opacity", "type": "number", "required": False, "description": "Highlight opacity."},
        {"name": "point_size", "type": "number", "required": False, "description": "Point highlight radius."},
    ],
    returns="dict with source layer id, highlight_layer_id, feature_count",
    examples=["Highlight beverage shops with rating >= 4.8"],
    tags=["map", "highlight", "filter", "selection"],
    needs_context=True,
)
def highlight_features(
    ctx: ToolContext,
    layer_id: str,
    attribute_filter: Any = None,
    name: Optional[str] = None,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    point_size: Optional[float] = None,
) -> dict:
    style = _style_payload(color=color, opacity=opacity, point_size=point_size)
    payload: dict = {
        "layer_id": layer_id,
        "filter": _filter_payload(attribute_filter),
    }
    if name is not None:
        payload["name"] = name
    if style is not None:
        payload["style"] = style
    return run_async_from_sync(ctx.request("rpc.ui.map.highlight_features", payload))


@tool(
    name="set_layer_order",
    display_name="Set Layer Order",
    description="Move a layer to top/bottom or above/below another layer.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True, "description": "Layer id to move."},
        {"name": "position", "type": "string", "required": True, "description": "top, bottom, above, or below."},
        {"name": "target_layer_id", "type": "string", "required": False, "description": "Target layer for above/below."},
    ],
    returns="dict with layer_id and new index",
    examples=["Move labels above roads", "Send basemap overlay to bottom"],
    tags=["map", "layer", "order"],
    needs_context=True,
)
def set_layer_order(
    ctx: ToolContext,
    layer_id: str,
    position: str,
    target_layer_id: Optional[str] = None,
) -> dict:
    payload = {"layer_id": layer_id, "position": position}
    if target_layer_id is not None:
        payload["target_layer_id"] = target_layer_id
    return run_async_from_sync(ctx.request("rpc.ui.map.set_layer_order", payload))


@tool(
    name="update_legend_spec",
    display_name="Update Layer Legend",
    description="Set stable legend metadata for a layer: title, labels, order, visibility.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True, "description": "Layer id."},
        {"name": "title", "type": "string", "required": False, "description": "Legend title."},
        {"name": "labels", "type": "object", "required": False, "description": "Legend label overrides."},
        {"name": "order", "type": "array", "required": False, "description": "Legend item order."},
        {"name": "visible", "type": "boolean", "required": False, "description": "Whether this layer appears in legends."},
    ],
    returns="dict with updated legend spec",
    examples=["Rename legend category labels for land use"],
    tags=["map", "legend", "style"],
    needs_context=True,
)
def update_legend_spec(
    ctx: ToolContext,
    layer_id: str,
    title: Optional[str] = None,
    labels: Any = None,
    order: Any = None,
    visible: Optional[bool] = None,
) -> dict:
    legend = {
        "title": title,
        "labels": _coerce_string_map(labels) if labels is not None else None,
        "order": _coerce_string_list(order) if order is not None else None,
        "visible": visible,
    }
    legend = {k: v for k, v in legend.items() if v is not None}
    return run_async_from_sync(ctx.request("rpc.ui.map.update_legend_spec", {"layer_id": layer_id, "legend": legend}))


# ──────────────────────────────────────────────────────────────────────
# add_raster
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="add_raster",
    display_name="Add Raster Layer",
    description=(
        "Add a raster layer to the map. Use render_mode='tiles' for large "
        "rasters or non-trivial CRS because the backend reads windowed XYZ "
        "tiles with rasterio. Use frontend for small GeoTIFFs when direct "
        "browser rendering is enough. Returns a dict with layer_id."
    ),
    category="visualization",
    params=[
        {"name": "path", "type": "string", "required": False,
         "description": "Path to a GeoTIFF (.tif/.tiff) file on disk. Preferred parameter."},
        {"name": "raster_path", "type": "string", "required": False,
         "description": "Alias for path. Use only if you naturally refer to the file as raster_path."},
        {"name": "file_path", "type": "string", "required": False,
         "description": "Alias for path."},
        {"name": "name", "type": "string", "required": False,
         "description": "Display name shown in the layer panel. Defaults to file stem."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Layer opacity 0.0-1.0. Default 1.0."},
        {"name": "render_mode", "type": "string", "required": False,
         "description": "auto (default), frontend, backend, or tiles. tiles is best for large/multi-format rasters; frontend uses geotiff.js for small GeoTIFFs; backend renders one preview PNG."},
        {"name": "ramp", "type": "string", "required": False,
         "description": "Color ramp for single-band raster display, e.g. viridis, magma, plasma, inferno, turbo, gray, terrain, spectral."},
        {"name": "band", "type": "number", "required": False,
         "description": "1-based band index for single-band rendering."},
        {"name": "stops", "type": "array", "required": False,
         "description": "Custom color/alpha stops. For agent calls, value is a source pixel value by default, e.g. band_stats p2/mean/p98. Use stops_unit='normalized' for 0-1 stops."},
        {"name": "stops_unit", "type": "string", "required": False,
         "description": "Value coordinate system for custom stops: 'source' (default for agent/tool calls) or 'normalized' (0-1 UI color-ramp positions)."},
        {"name": "min", "type": "number", "required": False,
         "description": "Stretch minimum in source values."},
        {"name": "max", "type": "number", "required": False,
         "description": "Stretch maximum in source values."},
        {"name": "reverse", "type": "boolean", "required": False,
         "description": "Reverse the selected color ramp."},
    ],
    returns=(
        "dict with keys: layer_id (str), path (str), name (str). "
        "Full metadata (bbox, width, height, crs) is available after "
        "the frontend finishes parsing the TIFF."
    ),
    examples=[
        "Add the DEM raster to the map",
        "Load the NDVI.tif file",
    ],
    tags=["map", "raster", "visualization", "geotiff"],
    needs_context=True,
)
def add_raster(
    ctx: ToolContext,
    path: Optional[str] = None,
    raster_path: Optional[str] = None,
    file_path: Optional[str] = None,
    name: Optional[str] = None,
    opacity: Optional[float] = None,
    render_mode: Optional[str] = None,
    ramp: Optional[str] = None,
    band: Optional[int] = None,
    stops: Any = None,
    stops_unit: Optional[str] = None,
    min: Optional[float] = None,  # noqa: A002 - tool parameter name
    max: Optional[float] = None,  # noqa: A002 - tool parameter name
    reverse: Optional[bool] = None,
) -> dict:
    path = path or raster_path or file_path
    if not path:
        raise ValueError("add_raster requires path, raster_path, or file_path")
    path = _resolve_workspace_path(ctx, path)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Raster file not found: {path}")
    if not name:
        name = p.stem

    mode = (render_mode or "auto").strip().lower()
    if mode not in {"auto", "frontend", "backend", "tiles"}:
        raise ValueError("add_raster render_mode must be one of: auto, frontend, backend, tiles")

    ext = p.suffix.lower()
    file_size = p.stat().st_size
    use_tiles = mode == "tiles" or (mode == "auto" and (file_size > 40 * 1024 * 1024 or ext not in {".tif", ".tiff"}))
    use_frontend = not use_tiles and (mode == "frontend" or (mode == "auto" and ext in {".tif", ".tiff"}))

    if use_tiles:
        return _add_raster_backend_tiles(
            ctx,
            path=str(p.resolve()),
            name=name,
            opacity=opacity,
            ramp=ramp,
            band=band,
            stops=stops,
            stops_unit=stops_unit,
            min=min,
            max=max,
            reverse=reverse,
        )

    if not use_frontend:
        return _add_raster_backend_preview(
            ctx,
            path=str(p.resolve()),
            name=name,
            opacity=opacity,
            ramp=ramp,
            band=band,
            stops=stops,
            stops_unit=stops_unit,
            min=min,
            max=max,
            reverse=reverse,
        )

    # 内容无关的唯一 id，避免同名栅格多次加载互相覆盖。
    layer_id = f"raster_{uuid4().hex}"

    payload: dict = {
        "path": str(p.resolve()),
        "name": name,
        "layer_id": layer_id,
    }
    if opacity is not None:
        payload["opacity"] = float(opacity)
    if ramp or band is not None or stops is not None or min is not None or max is not None or reverse is not None:
        payload["raster"] = {
            **({"ramp": ramp} if ramp else {}),
            **({"band": int(band)} if band is not None else {}),
            **({"opacity": float(opacity)} if opacity is not None else {}),
            **({"stops": _coerce_jsonish(stops)} if stops is not None else {}),
            **({"stopsUnit": _normalize_raster_stops_unit(stops_unit)} if stops is not None else {}),
            **({"min": float(min)} if min is not None else {}),
            **({"max": float(max)} if max is not None else {}),
            **({"reverse": bool(reverse)} if reverse is not None else {}),
        }

    result = run_async_from_sync(ctx.request("rpc.ui.map.add_raster_from_file", payload))
    if not isinstance(result, dict):
        result = {}

    return {
        "layer_id": result.get("layer_id", layer_id),
        "path": str(p.resolve()),
        "name": result.get("name", name),
        "bbox": result.get("bbox"),
        "width": result.get("width"),
        "height": result.get("height"),
        "band_count": result.get("band_count"),
        "crs": result.get("crs"),
        "nodata": result.get("nodata"),
        "band_stats": result.get("band_stats"),
        "render_mode": "frontend",
        "raster_style": result.get("raster_style"),
        "rerenderable": result.get("rerenderable"),
    }


# ──────────────────────────────────────────────────────────────────────
# add_3dtiles_layer  (OGC 3D Tiles)
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="add_3dtiles_layer",
    display_name="Add 3D Tiles Layer",
    description=(
        "Add an OGC 3D Tiles dataset (tileset.json + b3dm/i3dm/pnts + glTF) to "
        "the map. Point at either the tileset.json file OR the directory that "
        "contains it; the backend spins up a static file endpoint and the "
        "frontend renders it with deck.gl (shares the MapLibre camera). Works "
        "for photogrammetry meshes, BIM/CityGML tiles, and pnts point clouds "
        "that are already tiled. Returns a dict with layer_id."
    ),
    category="visualization",
    params=[
        {"name": "path", "type": "string", "required": True,
         "description": "Path to tileset.json OR the directory containing it."},
        {"name": "name", "type": "string", "required": False,
         "description": "Display name in the layer panel. Defaults to folder/file stem."},
        {"name": "point_size", "type": "number", "required": False,
         "description": "Point size (px) for pnts point-cloud tiles. Default 1.5."},
        {"name": "color", "type": "string", "required": False,
         "description": "Hex tint color, e.g. #ff8800. Omit to keep tile colors."},
        {"name": "max_screen_space_error", "type": "number", "required": False,
         "description": "LOD detail threshold. Larger = faster/coarser. Default 16."},
    ],
    returns="dict with keys: layer_id (str), name (str), tileset_url (str), asset_id (str).",
    examples=[
        "Load the 3D Tiles model at C:/data/mesh/tileset.json",
        "Add the photogrammetry tileset folder to the map",
    ],
    tags=["map", "3dtiles", "visualization", "3d"],
    needs_context=True,
)
def add_3dtiles_layer(
    ctx: ToolContext,
    path: str,
    name: Optional[str] = None,
    point_size: Optional[float] = None,
    color: Optional[str] = None,
    max_screen_space_error: Optional[float] = None,
) -> dict:
    from opengis_backend.integrations.gis.tiles3d_service import register_tileset

    if not path:
        raise ValueError("add_3dtiles_layer requires a path to tileset.json or its directory")
    resolved = _resolve_workspace_path(ctx, path)
    p = Path(resolved)
    if not p.exists():
        raise FileNotFoundError(f"3D Tiles path not found: {resolved}")
    if not name:
        name = p.stem if p.is_file() else p.name

    registration = register_tileset(str(p.resolve()))
    tileset_url = f"{_backend_http_base()}/api/assets/{registration.asset_id}/{registration.entry}"

    payload: dict = {
        "tileset_url": tileset_url,
        "name": name,
    }
    if point_size is not None:
        payload["point_size"] = float(point_size)
    if color:
        payload["color"] = color
    if max_screen_space_error is not None:
        payload["max_screen_space_error"] = float(max_screen_space_error)

    result = run_async_from_sync(ctx.request("rpc.ui.map.add_tiles3d", payload))
    if not isinstance(result, dict):
        result = {}
    return {
        "layer_id": result.get("layer_id"),
        "name": result.get("name", name),
        "tileset_url": tileset_url,
        "asset_id": registration.asset_id,
        "kind": "3dtiles",
    }


# ──────────────────────────────────────────────────────────────────────
# add_point_cloud_layer  (bare .las / .laz)
# ──────────────────────────────────────────────────────────────────────
@tool(
    name="add_point_cloud_layer",
    display_name="Add Point Cloud Layer",
    description=(
        "Add a bare point cloud (.las/.laz) to the map. The backend serves the "
        "file over HTTP and the frontend renders it with deck.gl's "
        "PointCloudLayer (loaders.gl LASLoader). If the points are already in "
        "lon/lat use coordinate='lnglat' (default); if they are in projected "
        "meters, pass coordinate='meter-offset' with an origin [lng, lat] anchor. "
        "For very large clouds, consider tiling to 3D Tiles (pnts) and using "
        "add_3dtiles_layer instead. Returns a dict with layer_id."
    ),
    category="visualization",
    params=[
        {"name": "path", "type": "string", "required": True,
         "description": "Path to a .las or .laz file on disk."},
        {"name": "name", "type": "string", "required": False,
         "description": "Display name in the layer panel. Defaults to file stem."},
        {"name": "coordinate", "type": "string", "required": False,
         "description": "'lnglat' (default) if points are WGS84 lon/lat, or 'meter-offset' if points are meter offsets from an origin anchor."},
        {"name": "origin", "type": "array", "required": False,
         "description": "Anchor [lng, lat] (optional altitude) used when coordinate='meter-offset'."},
        {"name": "point_size", "type": "number", "required": False,
         "description": "Point size in pixels. Default 1.5."},
        {"name": "color", "type": "string", "required": False,
         "description": "Hex color, e.g. #00ff88. Omit to use per-point colors or white."},
    ],
    returns="dict with keys: layer_id (str), name (str), url (str), asset_id (str), format (str).",
    examples=[
        "Load the point cloud at C:/data/scan.laz",
        "Add scan.las to the map anchored at 116.39, 39.9",
    ],
    tags=["map", "pointcloud", "visualization", "3d", "las", "laz"],
    needs_context=True,
)
def add_point_cloud_layer(
    ctx: ToolContext,
    path: str,
    name: Optional[str] = None,
    coordinate: Optional[str] = None,
    origin: Any = None,
    point_size: Optional[float] = None,
    color: Optional[str] = None,
) -> dict:
    from opengis_backend.integrations.gis.tiles3d_service import register_point_cloud

    if not path:
        raise ValueError("add_point_cloud_layer requires a path to a .las/.laz file")
    resolved = _resolve_workspace_path(ctx, path)
    p = Path(resolved)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Point cloud file not found: {resolved}")
    ext = p.suffix.lower()
    if ext not in {".las", ".laz"}:
        raise ValueError("add_point_cloud_layer only supports .las or .laz files")
    if not name:
        name = p.stem

    coord = (coordinate or "").strip().lower() or None
    if coord and coord not in {"lnglat", "meter-offset"}:
        raise ValueError("coordinate must be 'lnglat' or 'meter-offset'")

    origin_list = None
    if origin is not None:
        origin_list = _coerce_jsonish(origin)
        if not isinstance(origin_list, (list, tuple)) or len(origin_list) < 2:
            raise ValueError("origin must be a [lng, lat] (optionally with altitude) array")
        origin_list = [float(v) for v in origin_list]

    registration = register_point_cloud(str(p.resolve()))
    url = f"{_backend_http_base()}/api/assets/{registration.asset_id}/{registration.entry}"

    payload: dict = {
        "url": url,
        "name": name,
        "format": ext.lstrip("."),
    }
    if coord:
        payload["coordinate"] = coord
    if origin_list is not None:
        payload["origin"] = origin_list
    if point_size is not None:
        payload["point_size"] = float(point_size)
    if color:
        payload["color"] = color

    result = run_async_from_sync(ctx.request("rpc.ui.map.add_pointcloud", payload))
    if not isinstance(result, dict):
        result = {}
    return {
        "layer_id": result.get("layer_id"),
        "name": result.get("name", name),
        "url": url,
        "asset_id": registration.asset_id,
        "format": ext.lstrip("."),
        "kind": "pointcloud",
    }


def _raster_cache_dir(ctx: ToolContext) -> str:
    workspace = ctx.meta.get("workspace_path") if isinstance(ctx.meta, dict) else None
    base = Path(str(workspace)).expanduser() if workspace else Path.cwd()
    out = base / ".opengis" / "raster-cache"
    out.mkdir(parents=True, exist_ok=True)
    return str(out)


def _backend_http_base() -> str:
    port = os.environ.get("OPENGIS_PORT") or "8765"
    return f"http://127.0.0.1:{port}"


def _normalize_raster_stops_unit(value: Optional[str]) -> str:
    unit = str(value or "source").strip().lower().replace("-", "_")
    if unit in {"source", "data", "pixel", "pixel_value", "raw"}:
        return "source"
    if unit in {"normalized", "normalised", "normalize", "normalise", "0_1", "zero_one"}:
        return "normalized"
    raise ValueError("stops_unit must be 'source' or 'normalized'")


def _raster_style_payload(
    *,
    opacity: Optional[float] = None,
    ramp: Optional[str] = None,
    band: Optional[int] = None,
    stops: Any = None,
    stops_unit: Optional[str] = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    reverse: Optional[bool] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "band": int(band or 1),
        "ramp": ramp or "viridis",
        "opacity": 1.0 if opacity is None else float(opacity),
        "mode": "singleband",
    }
    if stops is not None:
        payload["stops"] = _coerce_jsonish(stops)
        payload["stopsUnit"] = _normalize_raster_stops_unit(stops_unit)
        payload["ramp"] = "custom"
    if min is not None:
        payload["min"] = float(min)
    if max is not None:
        payload["max"] = float(max)
    if reverse is not None:
        payload["reverse"] = bool(reverse)
    return payload


def _add_raster_backend_tiles(
    ctx: ToolContext,
    *,
    path: str,
    name: str,
    opacity: Optional[float] = None,
    ramp: Optional[str] = None,
    band: Optional[int] = None,
    stops: Any = None,
    stops_unit: Optional[str] = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    reverse: Optional[bool] = None,
) -> dict:
    from opengis_backend.integrations.gis.raster_service import register_raster

    style = _raster_style_payload(opacity=opacity, ramp=ramp, band=band, stops=stops, stops_unit=stops_unit, min=min, max=max, reverse=reverse)
    registration = register_raster(path, style=style)
    tile_url = (
        f"{_backend_http_base()}/api/rasters/{registration.raster_id}"
        f"/tiles/{{z}}/{{x}}/{{y}}.png?rev={registration.style_revision}"
    )
    result = run_async_from_sync(ctx.request("rpc.ui.map.add_raster_from_url", {
        "url": tile_url,
        "name": name,
        "tile_type": "xyz",
        "bounds": list(registration.info.bbox) if registration.info.bbox else None,
        "raster_id": registration.raster_id,
        "raster_source_path": path,
        "raster_info": registration.info.to_dict(),
        "raster_style": registration.style,
    }))
    if not isinstance(result, dict):
        result = {}
    return {
        "layer_id": result.get("layer_id"),
        "path": path,
        "name": result.get("name", name),
        "bbox": list(registration.info.bbox) if registration.info.bbox else None,
        "width": registration.info.width,
        "height": registration.info.height,
        "band_count": registration.info.band_count,
        "crs": registration.info.crs,
        "nodata": registration.info.nodata,
        "band_stats": registration.info.band_stats,
        "driver": registration.info.driver,
        "render_mode": "tiles",
        "raster_id": registration.raster_id,
        "tile_url": tile_url,
        "raster_style": registration.style,
        "rerenderable": True,
    }


def _add_raster_backend_preview(
    ctx: ToolContext,
    *,
    path: str,
    name: str,
    opacity: Optional[float] = None,
    ramp: Optional[str] = None,
    band: Optional[int] = None,
    stops: Any = None,
    stops_unit: Optional[str] = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    reverse: Optional[bool] = None,
) -> dict:
    from opengis_backend.integrations.gis.raster_service import render_raster_preview

    preview = render_raster_preview(
        path,
        output_dir=_raster_cache_dir(ctx),
        band=int(band or 1),
        ramp=ramp or "viridis",
        stops=_coerce_jsonish(stops) if stops is not None else None,
        stops_unit=_normalize_raster_stops_unit(stops_unit) if stops is not None else None,
        min_value=min,
        max_value=max,
        opacity=1.0 if opacity is None else float(opacity),
        reverse=bool(reverse) if reverse is not None else False,
    )
    raster_style = _raster_style_payload(opacity=opacity, ramp=ramp, band=band, stops=stops, stops_unit=stops_unit, min=min, max=max, reverse=reverse)
    result = run_async_from_sync(ctx.request("rpc.ui.map.add_image_overlay", {
        "path": preview.image_path,
        "name": name,
        "bbox": list(preview.bbox),
        "opacity": 1.0 if opacity is None else float(opacity),
        "raster_source_path": path,
        "raster_info": preview.info.to_dict(),
        "raster_style": raster_style,
    }))
    if not isinstance(result, dict):
        result = {}
    return {
        "layer_id": result.get("layer_id"),
        "path": path,
        "preview_path": preview.image_path,
        "name": result.get("name", name),
        "bbox": list(preview.bbox),
        "width": preview.info.width,
        "height": preview.info.height,
        "band_count": preview.info.band_count,
        "crs": preview.info.crs,
        "nodata": preview.info.nodata,
        "band_stats": preview.info.band_stats,
        "driver": preview.info.driver,
        "render_mode": "backend",
        "raster_style": raster_style,
        "rerenderable": False,
    }


@tool(
    name="get_raster_info",
    display_name="Get Raster Info",
    description=(
        "Inspect a raster layer already on the map, or inspect a raster file path "
        "with backend rasterio. Use this before choosing bands, color ramps, or "
        "stretch ranges."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": False,
         "description": "Existing raster layer id on the map."},
        {"name": "path", "type": "string", "required": False,
         "description": "Raster file path to inspect with backend rasterio."},
    ],
    returns="dict with bbox, crs, width, height, band_count, band_stats, raster_style.",
    examples=["Inspect this TIFF's band statistics", "查看 raster 图层的数据特征"],
    tags=["map", "raster", "metadata", "statistics"],
    needs_context=True,
)
def get_raster_info(
    ctx: ToolContext,
    layer_id: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    if layer_id:
        return run_async_from_sync(ctx.request("rpc.ui.map.get_raster_info", {"layer_id": layer_id}))
    if not path:
        raise ValueError("get_raster_info requires either layer_id or path")
    resolved = _resolve_workspace_path(ctx, path)
    from opengis_backend.integrations.gis.raster_service import inspect_raster

    return inspect_raster(resolved).to_dict()


@tool(
    name="set_raster_style",
    display_name="Set Raster Style",
    description=(
        "Update a raster layer's color ramp / stretch / opacity. For frontend "
        "GeoTIFF layers this re-renders the raster in place. Supports named ramps "
        "or custom stops with per-stop opacity."
    ),
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "Raster layer id."},
        {"name": "ramp", "type": "string", "required": False,
         "description": "Named ramp: viridis, magma, plasma, inferno, turbo, gray, terrain, spectral."},
        {"name": "stops", "type": "array", "required": False,
         "description": "Custom stops. For agent calls, value is a source pixel value by default, e.g. values from band_stats. Use stops_unit='normalized' for 0-1 stops."},
        {"name": "stops_unit", "type": "string", "required": False,
         "description": "Value coordinate system for custom stops: 'source' (default for agent/tool calls) or 'normalized' (0-1 UI color-ramp positions)."},
        {"name": "band", "type": "number", "required": False,
         "description": "1-based band index."},
        {"name": "min", "type": "number", "required": False,
         "description": "Stretch minimum in source values."},
        {"name": "max", "type": "number", "required": False,
         "description": "Stretch maximum in source values."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Overall opacity 0-1."},
        {"name": "reverse", "type": "boolean", "required": False,
         "description": "Reverse color ramp."},
        {"name": "mode", "type": "string", "required": False,
         "description": "auto, singleband, or rgb."},
    ],
    returns="dict with updated raster_style and raster metadata.",
    examples=["Set DEM raster to terrain ramp with 70% opacity", "Use blue transparent to red opaque custom raster stops"],
    tags=["map", "raster", "style", "color-ramp", "opacity"],
    needs_context=True,
)
def set_raster_style(
    ctx: ToolContext,
    layer_id: str,
    ramp: Optional[str] = None,
    stops: Any = None,
    stops_unit: Optional[str] = None,
    band: Optional[int] = None,
    min: Optional[float] = None,  # noqa: A002 - tool parameter name
    max: Optional[float] = None,  # noqa: A002 - tool parameter name
    opacity: Optional[float] = None,
    reverse: Optional[bool] = None,
    mode: Optional[str] = None,
) -> dict:
    raster: dict[str, Any] = {}
    if ramp is not None:
        raster["ramp"] = ramp
    if stops is not None:
        raster["stops"] = _coerce_jsonish(stops)
        raster["stopsUnit"] = _normalize_raster_stops_unit(stops_unit)
    if band is not None:
        raster["band"] = int(band)
    if min is not None:
        raster["min"] = float(min)
    if max is not None:
        raster["max"] = float(max)
    if opacity is not None:
        raster["opacity"] = float(opacity)
    if reverse is not None:
        raster["reverse"] = bool(reverse)
    if mode is not None:
        raster["mode"] = mode
    if not raster:
        raise ValueError("set_raster_style requires at least one style option")
    return run_async_from_sync(ctx.request("rpc.ui.map.set_raster_style", {
        "layer_id": layer_id,
        "raster": raster,
    }))


# ──────────────────────────────────────────────────────────────────────
# Layout composer tools
# ──────────────────────────────────────────────────────────────────────

@tool(
    name="layout_get_state",
    display_name="Layout: Get State",
    description="Read the current print/layout composer state: page, elements, selected element, and map snapshot availability.",
    category="visualization",
    params=[],
    returns="dict with page, elements, selected_element_id, map_scale_denominator, has_map_snapshot",
    examples=["Inspect the current layout canvas"],
    tags=["layout", "canvas", "print", "map"],
    needs_context=True,
)
def layout_get_state(ctx: ToolContext) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.layout.get_state", {}))


@tool(
    name="layout_set_page",
    display_name="Layout: Set Page",
    description="Set the layout page preset or custom dimensions. Presets include a4-landscape, a4-portrait, letter-landscape, screen-16-9, screen-4-3, square-1-1.",
    category="visualization",
    params=[
        {"name": "preset", "type": "string", "required": False, "description": "Page preset id."},
        {"name": "width_mm", "type": "number", "required": False, "description": "Custom page width in millimeters."},
        {"name": "height_mm", "type": "number", "required": False, "description": "Custom page height in millimeters."},
        {"name": "background", "type": "string", "required": False, "description": "Page background color, e.g. '#ffffff'."},
    ],
    returns="updated layout state",
    examples=["Set the layout to 4:3 landscape"],
    tags=["layout", "canvas", "page"],
    needs_context=True,
)
def layout_set_page(
    ctx: ToolContext,
    preset: Optional[str] = None,
    width_mm: Optional[float] = None,
    height_mm: Optional[float] = None,
    background: Optional[str] = None,
) -> dict:
    payload: dict[str, Any] = {}
    if preset:
        payload["preset"] = preset
    if width_mm is not None:
        payload["width_mm"] = float(width_mm)
    if height_mm is not None:
        payload["height_mm"] = float(height_mm)
    if background:
        payload["background"] = background
    return run_async_from_sync(ctx.request("rpc.ui.layout.set_page", payload))


@tool(
    name="layout_add_element",
    display_name="Layout: Add Element",
    description="Add one layout element: map-frame, scale-bar, north-arrow, legend, or text. Coordinates are percentages of the page.",
    category="visualization",
    params=[
        {"name": "element_type", "type": "string", "required": True, "description": "map-frame | scale-bar | north-arrow | legend | text"},
        {"name": "element_id", "type": "string", "required": False, "description": "Optional stable element id."},
        {"name": "label", "type": "string", "required": False, "description": "Display label."},
        {"name": "x", "type": "number", "required": False, "description": "Left position, percent of page."},
        {"name": "y", "type": "number", "required": False, "description": "Top position, percent of page."},
        {"name": "width", "type": "number", "required": False, "description": "Width, percent of page."},
        {"name": "height", "type": "number", "required": False, "description": "Height, percent of page."},
    ],
    returns="updated layout state",
    examples=["Add a north arrow at top right", "Add a scale bar near the bottom"],
    tags=["layout", "canvas", "element"],
    needs_context=True,
)
def layout_add_element(
    ctx: ToolContext,
    element_type: str,
    element_id: Optional[str] = None,
    label: Optional[str] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
) -> dict:
    frame = {k: v for k, v in {"x": x, "y": y, "width": width, "height": height}.items() if v is not None}
    payload: dict[str, Any] = {"type": element_type}
    if element_id:
        payload["id"] = element_id
    if label:
        payload["label"] = label
    if frame:
        payload["frame"] = frame
    return run_async_from_sync(ctx.request("rpc.ui.layout.add_element", payload))


@tool(
    name="layout_update_frame",
    display_name="Layout: Update Element Frame",
    description="Move or resize one layout element. Values are percentages of the page and are clamped to stay inside the page.",
    category="visualization",
    params=[
        {"name": "element_id", "type": "string", "required": True, "description": "Layout element id."},
        {"name": "x", "type": "number", "required": False, "description": "Left position, percent."},
        {"name": "y", "type": "number", "required": False, "description": "Top position, percent."},
        {"name": "width", "type": "number", "required": False, "description": "Width, percent."},
        {"name": "height", "type": "number", "required": False, "description": "Height, percent."},
    ],
    returns="updated layout state",
    examples=["Move the legend to the right side", "Resize the map frame"],
    tags=["layout", "canvas", "element", "position"],
    needs_context=True,
)
def layout_update_frame(
    ctx: ToolContext,
    element_id: str,
    x: Optional[float] = None,
    y: Optional[float] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
) -> dict:
    frame = {k: v for k, v in {"x": x, "y": y, "width": width, "height": height}.items() if v is not None}
    return run_async_from_sync(ctx.request("rpc.ui.layout.update_frame", {"element_id": element_id, "frame": frame}))


@tool(
    name="layout_update_style",
    display_name="Layout: Update Element Style",
    description="Update visual style for a layout element: variant, colors, opacity, border, font size, padding.",
    category="visualization",
    params=[
        {"name": "element_id", "type": "string", "required": True, "description": "Layout element id."},
        {"name": "variant", "type": "string", "required": False, "description": "Style variant, e.g. boxed, alternating, compass."},
        {"name": "fill_color", "type": "string", "required": False, "description": "Fill color."},
        {"name": "stroke_color", "type": "string", "required": False, "description": "Stroke/line color."},
        {"name": "stroke_width", "type": "number", "required": False, "description": "Stroke width."},
        {"name": "background_color", "type": "string", "required": False, "description": "Background color."},
        {"name": "background_opacity", "type": "number", "required": False, "description": "Background opacity 0-1; separate from element opacity."},
        {"name": "border_color", "type": "string", "required": False, "description": "Border color."},
        {"name": "border_width", "type": "number", "required": False, "description": "Border width."},
        {"name": "border_radius", "type": "number", "required": False, "description": "Border radius."},
        {"name": "text_color", "type": "string", "required": False, "description": "Text color."},
        {"name": "font_size", "type": "number", "required": False, "description": "Font size."},
        {"name": "opacity", "type": "number", "required": False, "description": "Opacity 0-1."},
        {"name": "padding", "type": "number", "required": False, "description": "Padding."},
    ],
    returns="updated layout state",
    examples=["Make the north arrow a compass style", "Set the legend background to transparent white"],
    tags=["layout", "canvas", "style"],
    needs_context=True,
)
def layout_update_style(
    ctx: ToolContext,
    element_id: str,
    variant: Optional[str] = None,
    fill_color: Optional[str] = None,
    stroke_color: Optional[str] = None,
    stroke_width: Optional[float] = None,
    background_color: Optional[str] = None,
    background_opacity: Optional[float] = None,
    border_color: Optional[str] = None,
    border_width: Optional[float] = None,
    border_radius: Optional[float] = None,
    text_color: Optional[str] = None,
    font_size: Optional[float] = None,
    opacity: Optional[float] = None,
    padding: Optional[float] = None,
) -> dict:
    mapping = {
        "variant": variant,
        "fillColor": fill_color,
        "strokeColor": stroke_color,
        "strokeWidth": stroke_width,
        "backgroundColor": background_color,
        "backgroundOpacity": background_opacity,
        "borderColor": border_color,
        "borderWidth": border_width,
        "borderRadius": border_radius,
        "textColor": text_color,
        "fontSize": font_size,
        "opacity": opacity,
        "padding": padding,
    }
    style = {k: v for k, v in mapping.items() if v is not None}
    return run_async_from_sync(ctx.request("rpc.ui.layout.update_style", {"element_id": element_id, "style": style}))


@tool(
    name="layout_update_props",
    display_name="Layout: Update Element Properties",
    description="Update semantic properties such as text content, scale bar label, or segment count.",
    category="visualization",
    params=[
        {"name": "element_id", "type": "string", "required": True, "description": "Layout element id."},
        {"name": "text", "type": "string", "required": False, "description": "Text element content."},
        {"name": "label", "type": "string", "required": False, "description": "Scale bar label."},
        {"name": "segments", "type": "number", "required": False, "description": "Scale bar segment count."},
        {"name": "auto_label", "type": "boolean", "required": False, "description": "Scale bar auto label flag. True makes labels update from map scale and element width."},
        {"name": "layer_ids", "type": "string", "required": False, "description": "Comma-separated layer ids used by legend elements."},
        {"name": "grouped", "type": "boolean", "required": False, "description": "Legend grouping flag. True groups selected layers into one legend."},
        {"name": "title", "type": "string", "required": False, "description": "Legend title."},
    ],
    returns="updated layout state",
    examples=["Set the map title", "Change scale bar label"],
    tags=["layout", "canvas", "element"],
    needs_context=True,
)
def layout_update_props(
    ctx: ToolContext,
    element_id: str,
    text: Optional[str] = None,
    label: Optional[str] = None,
    segments: Optional[int] = None,
    auto_label: Optional[bool] = None,
    layer_ids: Optional[Union[str, list[str]]] = None,
    grouped: Optional[bool] = None,
    title: Optional[str] = None,
) -> dict:
    props: dict[str, Any] = {}
    if text is not None:
        props["text"] = text
    if label is not None:
        props["label"] = label
    if segments is not None:
        props["segments"] = int(segments)
    if auto_label is not None:
        props["autoLabel"] = bool(auto_label)
    if layer_ids is not None:
        if isinstance(layer_ids, str):
            props["layerIds"] = [item.strip() for item in layer_ids.split(",") if item.strip()]
        else:
            props["layerIds"] = list(layer_ids)
    if grouped is not None:
        props["grouped"] = bool(grouped)
    if title is not None:
        props["title"] = title
    return run_async_from_sync(ctx.request("rpc.ui.layout.update_props", {"element_id": element_id, "props": props}))


@tool(
    name="layout_update_map_view",
    display_name="Layout: Update Map Frame View",
    description="Adjust the internal image position and scale of a map-frame without moving the outer frame.",
    category="visualization",
    params=[
        {"name": "element_id", "type": "string", "required": True, "description": "Map frame element id."},
        {"name": "x", "type": "number", "required": False, "description": "Internal horizontal pan percent -100 to 100."},
        {"name": "y", "type": "number", "required": False, "description": "Internal vertical pan percent -100 to 100."},
        {"name": "scale", "type": "number", "required": False, "description": "Internal scale 0.12-8."},
    ],
    returns="updated layout state",
    examples=["Zoom the map image inside the frame", "Pan the map content left inside the frame"],
    tags=["layout", "canvas", "map-frame"],
    needs_context=True,
)
def layout_update_map_view(
    ctx: ToolContext,
    element_id: str,
    x: Optional[float] = None,
    y: Optional[float] = None,
    scale: Optional[float] = None,
) -> dict:
    map_view = {k: v for k, v in {"x": x, "y": y, "scale": scale}.items() if v is not None}
    return run_async_from_sync(ctx.request("rpc.ui.layout.update_map_view", {"element_id": element_id, "map_view": map_view}))


@tool(
    name="layout_capture_map",
    display_name="Layout: Capture Current Map",
    description="Capture the current map canvas into the layout map frame snapshot.",
    category="visualization",
    params=[],
    returns="dict with success and has_snapshot",
    examples=["Refresh the layout map frame from the current map"],
    tags=["layout", "canvas", "map-frame"],
    needs_context=True,
)
def layout_capture_map(ctx: ToolContext) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.layout.capture_map", {}))


@tool(
    name="layout_remove_element",
    display_name="Layout: Remove Element",
    description="Remove a layout element by id.",
    category="visualization",
    params=[
        {"name": "element_id", "type": "string", "required": True, "description": "Layout element id."},
    ],
    returns="updated layout state",
    examples=["Remove an extra legend"],
    tags=["layout", "canvas", "element"],
    needs_context=True,
)
def layout_remove_element(ctx: ToolContext, element_id: str) -> dict:
    return run_async_from_sync(ctx.request("rpc.ui.layout.remove_element", {"element_id": element_id}))


@tool(
    name="layout_export",
    display_name="Layout: Export PNG",
    description="Export the current layout composer canvas as a PNG. Provide save_path to write a file.",
    category="visualization",
    params=[
        {"name": "save_path", "type": "string", "required": False, "description": "Absolute or workspace-relative output path."},
        {"name": "pixel_ratio", "type": "number", "required": False, "default": 2, "description": "Export pixel ratio 1-4."},
        {"name": "file_name", "type": "string", "required": False, "description": "Download file name if save_path is omitted."},
    ],
    returns="dict with saved_to or file_name plus width/height",
    examples=["Export the final layout to figures/final_map.png"],
    tags=["layout", "canvas", "export"],
    needs_context=True,
)
def layout_export(
    ctx: ToolContext,
    save_path: Optional[str] = None,
    pixel_ratio: Optional[float] = 2,
    file_name: Optional[str] = None,
) -> dict:
    payload: dict[str, Any] = {"pixel_ratio": float(pixel_ratio or 2)}
    if save_path:
        payload["save_path"] = _resolve_workspace_path(ctx, save_path)
    if file_name:
        payload["file_name"] = file_name
    return run_async_from_sync(ctx.request("rpc.ui.layout.export", payload))
