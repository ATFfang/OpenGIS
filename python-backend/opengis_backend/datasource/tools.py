"""Datasource tools — provide curated GeoJSON data sources for the agent."""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool

logger = logging.getLogger("opengis.datasource.tools")
_INLINE_GEOJSON_MAX_BYTES = 40 * 1024

# ── Default catalog path (next to this file) ─────────────────────────
CATALOG_PATH = Path(__file__).parent / "catalog.json"

# ── Catalog schema ────────────────────────────────────────────────────
# [
#   {
#     "name": "中国国土json（省级）",
#     "description": "包含省划分的中国国土数据",
#     "url": "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
#   },
#   ...
# ]


def _load_catalog() -> list[dict[str, str]]:
    """Load datasource catalog from JSON file."""
    if not CATALOG_PATH.exists():
        logger.warning("Datasource catalog not found: %s", CATALOG_PATH)
        return []
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    if not isinstance(catalog, list):
        raise ValueError("catalog.json must be a JSON array")
    return catalog


def _build_catalog_description() -> str:
    """Build a description string listing all available data sources."""
    try:
        catalog = _load_catalog()
    except Exception:
        return "(catalog unavailable)"
    if not catalog:
        return "(empty catalog)"
    lines = []
    for i, item in enumerate(catalog, 1):
        lines.append(f"  {i}. {item['name']} — {item['description']}")
        lines.append(f"     URL: {item['url']}")
    return "\n".join(lines)


# Build command reference at import time
DATASOURCE_COMMANDS = {
    "list": "List all available data sources. No params needed.",
    "get": (
        "Get URL and metadata of a data source.\n"
        "Params: {\"name\": \"数据源名称\"}\n"
        "Example: datasource_call(command='get', params='{\"name\": \"上海市\"}')"
    ),
    "fetch": (
        "Download GeoJSON data. Small results may be returned inline; large "
        "results or calls with output_path/save_path are saved to workspace.\n"
        "Params: {\"name\": \"数据源名称\", \"output_path\": \"data/shanghai.geojson\"}\n"
        "Example: datasource_call(command='fetch', params='{\"name\": \"上海市\", \"output_path\": \"data/shanghai.geojson\"}')"
    ),
}

_COMMAND_LIST_STR = "\n".join(f"  {cmd}: {desc}" for cmd, desc in DATASOURCE_COMMANDS.items())


def _find_source(name: str) -> dict[str, str] | None:
    """Find a data source by exact or partial name match."""
    catalog = _load_catalog()
    # Exact match first
    for item in catalog:
        if item["name"] == name:
            return item
    # Partial match
    for item in catalog:
        if name.lower() in item["name"].lower():
            return item
    return None


def _workspace_path(ctx: ToolContext) -> Path:
    workspace = (ctx.meta or {}).get("workspace_path")
    if workspace:
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(ctx: ToolContext, raw_path: str | None, source_name: str) -> Path:
    workspace = _workspace_path(ctx)
    if raw_path:
        raw = Path(str(raw_path)).expanduser()
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    else:
        safe_name = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", source_name).strip("_") or "datasource"
        path = (workspace / "data" / f"{safe_name}_{uuid.uuid4().hex[:8]}.geojson").resolve()
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
    source: dict[str, str],
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
        source.get("name", "datasource"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    summary = _feature_summary(geojson)
    result: dict[str, Any] = {
        "success": True,
        "name": source.get("name"),
        "url": source.get("url"),
        "type": geojson.get("type", "FeatureCollection"),
        "path": str(output_path),
        "output_path": str(output_path),
        **summary,
        "bytes": len(encoded.encode("utf-8")),
        "message": (
            f"Fetched datasource '{source.get('name')}' with {summary['feature_count']} "
            f"features and saved GeoJSON to {output_path}. Use add_layer(geojson_path=...) "
            "to display it."
        ),
    }
    if return_geojson:
        result["geojson"] = geojson
    return result


@tool(
    name="datasource_call",
    display_name="Datasource Call",
    description=(
        "Access curated GeoJSON data sources from a predefined catalog.\n\n"
        "Commands:\n"
        "  list — list all sources (no params needed)\n"
        '  get — get metadata: datasource_call(command=\'get\', params=\'{"name": "上海市"}\')\n'
        '  fetch — download GeoJSON: datasource_call(command=\'fetch\', params=\'{"name": "上海市", "output_path": "data/shanghai.geojson"}\')\n\n'
        "IMPORTANT: params must be a JSON string, e.g. '{\"name\": \"上海市\"}'.\n\n"
        f"Available data sources:\n{_build_catalog_description()}"
    ),
    category="data",
    group="datasource",
    params=[
        {"name": "command", "type": "string",
         "description": "Command: 'list', 'get', or 'fetch'."},
        {"name": "params", "type": "string", "required": False, "default": "{}",
         "description": 'JSON string with command params. Example: \'{"name": "上海市"}\'. For "list" command, omit this.'},
    ],
    returns=(
        "dict — catalog listing, source metadata, inline GeoJSON for small fetches, "
        "or {success,path,feature_count,bbox,geometry_types} for saved/large fetches"
    ),
    tags=["datasource", "geojson", "catalog", "data"],
    needs_context=True,
)
def datasource_call(ctx: ToolContext, command: str, params: str = "{}") -> dict[str, Any]:
    """Execute a datasource command.

    Args:
        command: 'list', 'get', or 'fetch'
        params: JSON string, e.g. '{"name": "上海市"}'

    Returns:
        dict with source metadata or GeoJSON FeatureCollection
    """
    if command not in DATASOURCE_COMMANDS:
        raise ValueError(
            f"Unknown datasource command: '{command}'. "
            f"Available: {', '.join(DATASOURCE_COMMANDS.keys())}"
        )

    try:
        p = json.loads(params) if isinstance(params, str) else params
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON params for datasource_call: {exc}") from exc
    if not isinstance(p, dict):
        raise ValueError("datasource_call params must be a JSON object")

    if command == "list":
        catalog = _load_catalog()
        return {
            "sources": [
                {"name": item["name"], "description": item["description"], "url": item["url"]}
                for item in catalog
            ],
            "count": len(catalog),
        }

    elif command == "get":
        name = p.get("name", "")
        if not name:
            raise ValueError("'name' parameter is required for 'get' command")
        source = _find_source(name)
        if source is None:
            raise ValueError(f"Data source not found: '{name}'")
        return {"name": source["name"], "description": source["description"], "url": source["url"]}

    elif command == "fetch":
        import urllib.request

        name = p.get("name", "")
        if not name:
            raise ValueError("'name' parameter is required for 'fetch' command")
        source = _find_source(name)
        if source is None:
            raise ValueError(f"Data source not found: '{name}'")

        logger.info("Fetching datasource: %s from %s", source["name"], source["url"])
        req = urllib.request.Request(source["url"], headers={"User-Agent": "OpenGIS/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _finalize_geojson_result(ctx, source=source, params=p, geojson=data)

    raise ValueError(f"Unhandled command: {command}")
