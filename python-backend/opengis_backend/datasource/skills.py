"""Datasource skill — provide curated GeoJSON data sources for the agent."""

import json
import logging
from pathlib import Path
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger("opengis.datasource.skills")

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
        "Download GeoJSON data and return it directly.\n"
        "Params: {\"name\": \"数据源名称\"}\n"
        "Example: datasource_call(command='fetch', params='{\"name\": \"上海市\"}')"
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


@skill(
    name="datasource_call",
    display_name="Datasource Call",
    description=(
        "Access curated GeoJSON data sources from a predefined catalog.\n\n"
        "Commands:\n"
        "  list — list all sources (no params needed)\n"
        '  get — get metadata: datasource_call(command=\'get\', params=\'{"name": "上海市"}\')\n'
        '  fetch — download GeoJSON: datasource_call(command=\'fetch\', params=\'{"name": "上海市"}\')\n\n'
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
    returns="dict — catalog listing, source metadata, or GeoJSON FeatureCollection",
    tags=["datasource", "geojson", "catalog", "data"],
    needs_context=False,
)
def datasource_call(command: str, params: str = "{}") -> dict[str, Any]:
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

    p = json.loads(params) if isinstance(params, str) else params

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
        return data

    raise ValueError(f"Unhandled command: {command}")
