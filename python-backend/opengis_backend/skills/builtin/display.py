"""
Display skills — wrap MapEngine operations as Python skills.

These skills do NOT compute anything. They emit JSON-RPC notifications
that the frontend's v3.0 ``rpc.ui.map.*`` handlers translate into
MapEngine / mapStore calls.

This is the bridge that lets the LLM "render to the map" by writing
ordinary Python code, with no awareness of TS/Electron.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from opengis_backend.skills.context import SkillContext, run_async_from_sync
from opengis_backend.skills.registry import skill


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

# In-process registry so the agent can look a layer up by id after
# add_layer() has returned. Keyed by layer_id → {bbox, feature_count,
# geometry_type, name}. Lives for the lifetime of the backend process,
# which is fine — layer_ids are scoped to the current map.
_LAYER_INDEX: dict[str, dict[str, Any]] = {}


def _notify_map(ctx: SkillContext, canonical: str, payload: dict) -> None:
    """
    Emit a canonical v3.0 map command notification.

    ``map.*`` is fire-and-forget — the LLM does not await a response.
    Since Stage 3.6 there is only one channel: ``rpc.ui.map.*``.
    The legacy ``map.addLayer`` / ``map.flyTo`` / etc. names are gone.
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
    geom_types: set[str] = set()

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
            geom_types.add(gtype)
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
        return None, feature_count, (next(iter(geom_types)) if geom_types else None)
    bbox = [min_x, min_y, max_x, max_y]
    # Dominant geometry type (first one seen is fine for display purposes)
    gt = next(iter(geom_types)) if geom_types else None
    return bbox, feature_count, gt


def _resolve_workspace_path(ctx: SkillContext, raw_path: str) -> str:
    """Turn a (possibly-relative) path into an absolute one.

    The agent's subprocess sandbox runs with ``cwd=workspace_path``, so
    code like ``gpd.read_file("foo.geojson")`` works there. But display
    skills (add_layer, etc.) run in the **parent** worker thread whose
    cwd is the backend's launch dir — relative paths break.

    Fix: resolve against ``ctx.meta['workspace_path']`` first, then fall
    back to current cwd. Absolute paths pass through unchanged.

    NOTE: keep this helper *above* the ``# add_layer`` section so the
    ``@skill(name="add_layer", ...)`` decorator below stays glued to the
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
@skill(
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
    ctx: SkillContext,
    geojson_path: Optional[str] = None,
    geojson: Optional[str] = None,
    layer_id: Optional[str] = None,
    name: Optional[str] = None,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
) -> dict:
    if not geojson_path and not geojson:
        raise ValueError("add_layer requires either geojson_path or geojson")

    payload: dict = {}
    geojson_obj: dict
    if geojson_path:
        # Relative paths are resolved against the workspace, mirroring
        # the subprocess sandbox's cwd. Without this, the LLM has to
        # guess that display skills can't see its own cwd.
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
        # Stable-ish id from name or hash of payload
        layer_id = f"layer_{abs(hash((name or '', geojson_path or ''))) % 10**8}"

    payload["layer_id"] = layer_id
    payload["name"] = name or layer_id
    if color:
        payload["color"] = color
    if opacity is not None:
        payload["opacity"] = float(opacity)

    # Compute bbox locally so we can return it synchronously to the
    # agent's python sandbox — the frontend doesn't need to round-trip.
    bbox, feature_count, geometry_type = _compute_geojson_bbox(geojson_obj)
    if bbox is not None:
        payload["bbox"] = bbox

    _notify_map(ctx, "rpc.ui.map.add_layer_from_geojson", payload)

    info = {
        "layer_id": layer_id,
        "bbox": bbox,
        "feature_count": feature_count,
        "geometry_type": geometry_type,
        "name": payload["name"],
    }
    _LAYER_INDEX[layer_id] = info
    return info


# ──────────────────────────────────────────────────────────────────────
# remove_layer
# ──────────────────────────────────────────────────────────────────────
@skill(
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
def remove_layer(ctx: SkillContext, layer_id: str) -> bool:
    _notify_map(ctx, "rpc.ui.map.remove_layer", {"layer_id": layer_id})
    _LAYER_INDEX.pop(layer_id, None)
    return True


# ──────────────────────────────────────────────────────────────────────
# fly_to
# ──────────────────────────────────────────────────────────────────────
@skill(
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
        {"name": "bbox", "type": "any", "required": False,
         "description": "Bounding box as a list [minx, miny, maxx, maxy] OR a JSON-encoded string. Overrides lng/lat."},
        {"name": "duration", "type": "number", "required": False,
         "description": "Animation duration in ms. Default 1500."},
    ],
    returns="True when the command was sent.",
    examples=["Fly to Beijing", "Zoom to the buffered features"],
    tags=["map", "camera", "navigation"],
    needs_context=True,
)
def fly_to(
    ctx: SkillContext,
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    zoom: Optional[float] = None,
    bbox: Optional[Union[str, list, tuple]] = None,
    duration: Optional[float] = None,
) -> bool:
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

    # Route to the right canonical method based on payload shape.
    if "bbox" in payload:
        _notify_map(ctx, "rpc.ui.map.zoom_to_bbox", payload)
    else:
        _notify_map(ctx, "rpc.ui.map.fly_to", payload)
    return True


# ──────────────────────────────────────────────────────────────────────
# zoom_to_layer
# ──────────────────────────────────────────────────────────────────────
@skill(
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
    returns="True when the command was sent.",
    examples=["Zoom to the points layer I just added"],
    tags=["map", "camera", "layer", "navigation"],
    needs_context=True,
)
def zoom_to_layer(
    ctx: SkillContext,
    layer_id: str,
    duration: Optional[float] = None,
    padding: Optional[float] = None,
) -> bool:
    info = _LAYER_INDEX.get(layer_id)
    if info is None:
        raise ValueError(
            f"Unknown layer_id '{layer_id}'. "
            f"Did you call add_layer() and use its returned info['layer_id']?"
        )
    bbox = info.get("bbox")
    if not bbox:
        raise ValueError(
            f"Layer '{layer_id}' has no bbox (likely empty or invalid geometry). "
            f"Cannot fit camera."
        )
    payload: dict = {"bbox": [float(x) for x in bbox]}
    if duration is not None:
        payload["duration"] = float(duration)
    if padding is not None:
        payload["padding"] = float(padding)
    # zoom_to_layer always fits a bbox — canonical method is zoom_to_bbox.
    _notify_map(ctx, "rpc.ui.map.zoom_to_bbox", payload)
    return True


# ──────────────────────────────────────────────────────────────────────
# set_basemap
# ──────────────────────────────────────────────────────────────────────
@skill(
    name="set_basemap",
    display_name="Set Basemap",
    description="Switch the basemap. Common ids: 'osm', 'satellite', 'dark', 'light', 'terrain'.",
    category="visualization",
    params=[
        {"name": "basemap_id", "type": "string", "required": True,
         "description": "Basemap identifier."},
    ],
    returns="True when the command was sent.",
    examples=["Switch to satellite basemap"],
    tags=["map", "basemap"],
    needs_context=True,
)
def set_basemap(ctx: SkillContext, basemap_id: str) -> bool:
    # Canonical payload uses `basemap` (matches SetBasemapSchema);
    # legacy payload keeps `basemap_id` for the old CommandBus listener.
    run_async_from_sync(ctx.notify("rpc.ui.map.set_basemap", {"basemap": basemap_id}))
    run_async_from_sync(ctx.notify("map.setBasemap", {"basemap_id": basemap_id}))
    return True


# ──────────────────────────────────────────────────────────────────────
# update_layer_style
# ──────────────────────────────────────────────────────────────────────
@skill(
    name="update_layer_style",
    display_name="Update Layer Style",
    description="Change color / opacity / visibility of an existing layer.",
    category="visualization",
    params=[
        {"name": "layer_id", "type": "string", "required": True,
         "description": "The layer id to update."},
        {"name": "color", "type": "string", "required": False,
         "description": "New color, e.g. '#3388ff'."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "New opacity 0.0-1.0."},
        {"name": "visible", "type": "boolean", "required": False,
         "description": "Show / hide the layer."},
    ],
    returns="True when the command was sent.",
    examples=["Make the buffer layer red", "Hide the points layer"],
    tags=["map", "style", "layer"],
    needs_context=True,
)
def update_layer_style(
    ctx: SkillContext,
    layer_id: str,
    color: Optional[str] = None,
    opacity: Optional[float] = None,
    visible: Optional[bool] = None,
) -> bool:
    payload: dict = {"layer_id": layer_id}
    if color is not None:
        payload["color"] = color
    if opacity is not None:
        payload["opacity"] = float(opacity)
    if visible is not None:
        payload["visible"] = bool(visible)
    # Canonical v3.0: style and visibility go through different handlers.
    # Only send the canonical messages that have meaningful payload.
    if color is not None or opacity is not None:
        style_payload: dict = {
            "layer_id": layer_id,
            "style": {
                "type": "fill",  # best-effort default; Stage 3.5 handler can re-infer
                "paint": {
                    **({"fill-color": color} if color is not None else {}),
                    **({"fill-opacity": float(opacity)} if opacity is not None else {}),
                },
            },
        }
        run_async_from_sync(ctx.notify("rpc.ui.map.set_layer_style", style_payload))
    if visible is not None:
        run_async_from_sync(
            ctx.notify("rpc.ui.map.set_layer_visibility",
                       {"layer_id": layer_id, "visible": bool(visible)})
        )
    return True


# ──────────────────────────────────────────────────────────────────────
# add_raster
# ──────────────────────────────────────────────────────────────────────
@skill(
    name="add_raster",
    display_name="Add Raster Layer",
    description=(
        "Add a raster layer (GeoTIFF) to the map. The frontend parses the "
        "TIFF file directly using geotiff.js. Returns a dict with layer_id. "
        "After calling this, use zoom_to_layer(layer_id) to fit the camera."
    ),
    category="visualization",
    params=[
        {"name": "path", "type": "string", "required": True,
         "description": "Path to a GeoTIFF (.tif/.tiff) file on disk."},
        {"name": "name", "type": "string", "required": False,
         "description": "Display name shown in the layer panel. Defaults to file stem."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Layer opacity 0.0-1.0. Default 1.0."},
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
    ctx: SkillContext,
    path: str,
    name: Optional[str] = None,
    opacity: Optional[float] = None,
) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Raster file not found: {path}")
    if not name:
        name = p.stem

    layer_id = f"raster_{abs(hash(name + path)) % 10**8}"

    payload: dict = {
        "path": str(p.resolve()),
        "name": name,
        "layer_id": layer_id,
    }
    if opacity is not None:
        payload["opacity"] = float(opacity)

    _notify_map(ctx, "rpc.ui.map.add_raster_from_file", payload)

    return {
        "layer_id": layer_id,
        "path": str(p.resolve()),
        "name": name,
    }
