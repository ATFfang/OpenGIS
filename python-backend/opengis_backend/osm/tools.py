"""OSM tools — single unified entry point for OpenStreetMap data downloads."""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import requests

from opengis_backend.osm.overpass import (
    bbox_from_place,
    nominatim_search,
    osm_to_geojson,
    overpass_query,
)
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool

logger = logging.getLogger("opengis.osm.tools")

_INLINE_GEOJSON_MAX_BYTES = 40 * 1024

# ── Command reference ──────────────────────────────────────────────

OSM_COMMANDS = {
    "overpass_query": (
        "Run a raw Overpass QL query and return GeoJSON.\n"
        "Params: query (Overpass QL, e.g. 'node[\"amenity\"=\"cafe\"](39.9,116.3,40.0,116.5);'),\n"
        "        timeout (seconds, default 120), retries (default 1),\n"
        "        output_path/save_path (optional GeoJSON file path), return_geojson (bool, default false for saved/large results)."
    ),
    "download_bbox": (
        "Download OSM features within a bounding box by tag.\n"
        "Params: south (float), west (float), north (float), east (float),\n"
        "        key (OSM tag key, e.g. 'highway'), value (tag value, e.g. 'primary', or '*' for any),\n"
        "        geometry_type ('node'|'way'|'relation', default 'way'),\n"
        "        timeout (seconds, default 120), retries (default 1),\n"
        "        output_path/save_path (optional GeoJSON file path), return_geojson (bool, default false for saved/large results)."
    ),
    "download_features": (
        "Download OSM features by place name and tag.\n"
        "Params: place (place name for geocoding, e.g. 'Tsinghua University'),\n"
        "        key (OSM tag key), value (tag value or '*'),\n"
        "        geometry_type ('node'|'way'|'relation', default 'way'),\n"
        "        geocode_timeout (seconds, default 45), geocode_retries (default 2), timeout (Overpass seconds, default 120), retries (default 1),\n"
        "        output_path/save_path (optional GeoJSON file path), return_geojson (bool, default false for saved/large results)."
    ),
    "search": (
        "Search for a place by name (Nominatim geocoding).\n"
        "Params: query (search text), limit (max results, default 5), timeout (seconds, default 45), retries (default 2)."
    ),
}

_COMMAND_LIST_STR = "\n".join(f"  {cmd}: {desc}" for cmd, desc in OSM_COMMANDS.items())


def _build_tag_filter(key: str, value: str, geometry_type: str = "way") -> str:
    """Build an Overpass QL element query with tag filter."""
    geometry_type = (geometry_type or "way").strip().lower()
    if geometry_type not in {"node", "way", "relation"}:
        raise ValueError("geometry_type must be one of: node, way, relation")
    if not re.match(r"^[A-Za-z0-9_:\-]+$", key or ""):
        raise ValueError(f"Invalid OSM tag key: {key!r}")
    if value == "*":
        tag = f'["{key}"]'
    else:
        escaped_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
        tag = f'["{key}"="{escaped_value}"]'
    return f'{geometry_type}{tag}'


def _cmd_download_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    key: str,
    value: str = "*",
    geometry_type: str = "way",
    timeout: int = 120,
    retries: int = 1,
) -> dict:
    _validate_bbox(south, west, north, east)
    bbox = f"({south},{west},{north},{east})"
    element = _build_tag_filter(key, value, geometry_type)
    query = f"{element}{bbox};\nout geom;"
    data = overpass_query(query, timeout=timeout, retries=retries)
    return osm_to_geojson(data)


def _cmd_download_features(place: str, key: str, value: str = "*",
                           geometry_type: str = "way",
                           timeout: int = 120,
                           retries: int = 1,
                           geocode_timeout: int = 45,
                           geocode_retries: int = 2) -> dict:
    bb = bbox_from_place(place, timeout=geocode_timeout, retries=geocode_retries)
    if bb is None:
        raise ValueError(f"Place not found: '{place}'")
    south, north, west, east = bb
    return _cmd_download_bbox(south, west, north, east, key, value, geometry_type, timeout, retries)


def _validate_bbox(south: float, west: float, north: float, east: float) -> None:
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("south/north must be within [-90, 90]")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("west/east must be within [-180, 180]")
    if south >= north:
        raise ValueError("south must be less than north")
    if west >= east:
        raise ValueError("west must be less than east")


def _workspace_path(ctx: ToolContext) -> Path:
    workspace = (ctx.meta or {}).get("workspace_path")
    if workspace:
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(ctx: ToolContext, raw_path: str | None, command: str) -> Path:
    workspace = _workspace_path(ctx)
    if raw_path:
        raw = Path(str(raw_path)).expanduser()
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    else:
        safe_command = re.sub(r"[^A-Za-z0-9_.-]+", "_", command).strip("_") or "osm"
        path = (workspace / "osm" / f"{safe_command}_{uuid.uuid4().hex[:8]}.geojson").resolve()
    if workspace != path and workspace not in path.parents:
        raise ValueError(f"output_path must be inside workspace: {path}")
    if path.suffix.lower() not in {".geojson", ".json"}:
        path = path.with_suffix(".geojson")
    return path


def _feature_summary(geojson: dict[str, Any]) -> dict[str, Any]:
    features = geojson.get("features") if isinstance(geojson, dict) else None
    if not isinstance(features, list):
        return {"feature_count": 0, "geometry_types": [], "bbox": None}

    geometry_types: set[str] = set()
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def visit_coords(coords: Any) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if not coords:
            return
        if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
            if len(coords) >= 2:
                x = float(coords[0])
                y = float(coords[1])
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            return
        if isinstance(coords, list):
            for item in coords:
                visit_coords(item)

    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        if isinstance(gtype, str):
            geometry_types.add(gtype)
        visit_coords(geom.get("coordinates"))

    bbox = None if min_x == float("inf") else [min_x, min_y, max_x, max_y]
    return {
        "feature_count": len(features),
        "geometry_types": sorted(geometry_types),
        "bbox": bbox,
    }


def _finalize_geojson_result(
    ctx: ToolContext,
    *,
    command: str,
    params: dict[str, Any],
    geojson: dict[str, Any],
) -> dict[str, Any]:
    raw_output_path = params.get("output_path") or params.get("save_path")
    return_geojson = bool(params.get("return_geojson", False))
    encoded = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    should_save = bool(raw_output_path) or len(encoded.encode("utf-8")) > _INLINE_GEOJSON_MAX_BYTES

    if not should_save:
        return geojson

    output_path = _resolve_output_path(
        ctx,
        str(raw_output_path) if raw_output_path else None,
        command,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    summary = _feature_summary(geojson)
    result: dict[str, Any] = {
        "success": True,
        "type": "FeatureCollection",
        "path": str(output_path),
        "output_path": str(output_path),
        **summary,
        "bytes": len(encoded.encode("utf-8")),
        "message": (
            f"Downloaded {summary['feature_count']} OSM features and saved GeoJSON to "
            f"{output_path}. Use add_layer(geojson_path=...) to display it."
        ),
    }
    if return_geojson:
        result["geojson"] = geojson
    return result


def _network_error_response(command: str, exc: Exception, params: dict[str, Any]) -> dict[str, Any]:
    is_timeout = isinstance(exc, requests.exceptions.Timeout)
    return {
        "success": False,
        "error": "osm_network_timeout" if is_timeout else "osm_network_error",
        "command": command,
        "message": str(exc),
        "retryable": True,
        "timeout": params.get("timeout") or params.get("geocode_timeout"),
        "retries": params.get("retries") or params.get("geocode_retries"),
        "suggestion": (
            "Nominatim/Overpass is slow or unreachable. Retry with a larger timeout, "
            "or avoid geocoding by using download_bbox with explicit south/west/north/east."
        ),
    }


@tool(
    name="osm_call",
    display_name="OSM Call",
    description=(
        "Download OpenStreetMap data via Overpass API. Pass the command name and JSON params.\n"
        f"Available commands:\n{_COMMAND_LIST_STR}"
    ),
    category="data",
    group="osm",
    params=[
        {"name": "command", "type": "string",
         "description": "Command name: 'overpass_query', 'download_bbox', 'download_features', 'search'"},
        {"name": "params", "type": "string", "required": False, "default": "{}",
         "description": "JSON string of command parameters"},
    ],
    returns=(
        "dict — search results, inline GeoJSON for small downloads, or "
        "{success,path,feature_count,bbox,geometry_types} for saved/large downloads"
    ),
    tags=["osm", "openstreetmap", "data"],
    needs_context=True,
)
def osm_call(ctx: ToolContext, command: str, params: str = "{}") -> dict[str, Any]:
    if command not in OSM_COMMANDS:
        raise ValueError(
            f"Unknown OSM command: '{command}'. "
            f"Available: {', '.join(OSM_COMMANDS.keys())}"
        )

    try:
        p = json.loads(params) if isinstance(params, str) else params
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON params for osm_call: {exc}") from exc
    if not isinstance(p, dict):
        raise ValueError("osm_call params must be a JSON object")

    if command == "overpass_query":
        try:
            data = overpass_query(
                p["query"],
                timeout=int(p.get("timeout", 120)),
                retries=int(p.get("retries", 1)),
            )
            return _finalize_geojson_result(
                ctx,
                command=command,
                params=p,
                geojson=osm_to_geojson(data),
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            return _network_error_response(command, exc, p)

    elif command == "download_bbox":
        try:
            geojson = _cmd_download_bbox(
                south=float(p["south"]),
                west=float(p["west"]),
                north=float(p["north"]),
                east=float(p["east"]),
                key=p["key"],
                value=p.get("value", "*"),
                geometry_type=p.get("geometry_type", "way"),
                timeout=int(p.get("timeout", 120)),
                retries=int(p.get("retries", 1)),
            )
            return _finalize_geojson_result(ctx, command=command, params=p, geojson=geojson)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            return _network_error_response(command, exc, p)

    elif command == "download_features":
        try:
            geojson = _cmd_download_features(
                place=p["place"],
                key=p["key"],
                value=p.get("value", "*"),
                geometry_type=p.get("geometry_type", "way"),
                timeout=int(p.get("timeout", 120)),
                retries=int(p.get("retries", 1)),
                geocode_timeout=int(p.get("geocode_timeout", p.get("timeout", 45))),
                geocode_retries=int(p.get("geocode_retries", p.get("retries", 2))),
            )
            return _finalize_geojson_result(ctx, command=command, params=p, geojson=geojson)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            return _network_error_response(command, exc, p)

    elif command == "search":
        try:
            return {"success": True, "results": nominatim_search(
                p["query"],
                limit=int(p.get("limit", 5)),
                timeout=int(p.get("timeout", 45)),
                retries=int(p.get("retries", 2)),
            )}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            return _network_error_response(command, exc, p)

    raise ValueError(f"Unhandled command: {command}")
