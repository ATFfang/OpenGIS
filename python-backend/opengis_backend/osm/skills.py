"""OSM skill — single unified entry point for OpenStreetMap data downloads."""

import json
import logging
from typing import Any

from opengis_backend.osm.overpass import (
    bbox_from_place,
    nominatim_search,
    osm_to_geojson,
    overpass_query,
)
from opengis_backend.skills.registry import skill

logger = logging.getLogger("opengis.osm.skills")

# ── Command reference ──────────────────────────────────────────────

OSM_COMMANDS = {
    "overpass_query": (
        "Run a raw Overpass QL query and return GeoJSON.\n"
        "Params: query (Overpass QL, e.g. 'node[\"amenity\"=\"cafe\"](39.9,116.3,40.0,116.5);')."
    ),
    "download_bbox": (
        "Download OSM features within a bounding box by tag.\n"
        "Params: south (float), west (float), north (float), east (float),\n"
        "        key (OSM tag key, e.g. 'highway'), value (tag value, e.g. 'primary', or '*' for any),\n"
        "        geometry_type ('node'|'way'|'relation', default 'way')."
    ),
    "download_features": (
        "Download OSM features by place name and tag.\n"
        "Params: place (place name for geocoding, e.g. 'Tsinghua University'),\n"
        "        key (OSM tag key), value (tag value or '*'),\n"
        "        geometry_type ('node'|'way'|'relation', default 'way')."
    ),
    "search": (
        "Search for a place by name (Nominatim geocoding).\n"
        "Params: query (search text), limit (max results, default 5)."
    ),
}

_COMMAND_LIST_STR = "\n".join(f"  {cmd}: {desc}" for cmd, desc in OSM_COMMANDS.items())


def _build_tag_filter(key: str, value: str, geometry_type: str = "way") -> str:
    """Build an Overpass QL element query with tag filter."""
    if value == "*":
        tag = f'["{key}"]'
    else:
        tag = f'["{key}"="{value}"]'
    return f'{geometry_type}{tag}'


def _cmd_download_bbox(south: float, west: float, north: float, east: float,
                       key: str, value: str = "*", geometry_type: str = "way") -> dict:
    bbox = f"({south},{west},{north},{east})"
    element = _build_tag_filter(key, value, geometry_type)
    query = f"{element}{bbox};\nout geom;"
    data = overpass_query(query)
    return osm_to_geojson(data)


def _cmd_download_features(place: str, key: str, value: str = "*",
                           geometry_type: str = "way") -> dict:
    bb = bbox_from_place(place)
    if bb is None:
        raise ValueError(f"Place not found: '{place}'")
    south, north, west, east = bb
    return _cmd_download_bbox(south, west, north, east, key, value, geometry_type)


@skill(
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
    returns="dict — GeoJSON FeatureCollection for download commands, list of results for search",
    tags=["osm", "openstreetmap", "data"],
    needs_context=False,
)
def osm_call(command: str, params: str = "{}") -> dict[str, Any]:
    if command not in OSM_COMMANDS:
        raise ValueError(
            f"Unknown OSM command: '{command}'. "
            f"Available: {', '.join(OSM_COMMANDS.keys())}"
        )

    p = json.loads(params) if isinstance(params, str) else params

    if command == "overpass_query":
        data = overpass_query(p["query"])
        return osm_to_geojson(data)

    elif command == "download_bbox":
        return _cmd_download_bbox(
            south=float(p["south"]),
            west=float(p["west"]),
            north=float(p["north"]),
            east=float(p["east"]),
            key=p["key"],
            value=p.get("value", "*"),
            geometry_type=p.get("geometry_type", "way"),
        )

    elif command == "download_features":
        return _cmd_download_features(
            place=p["place"],
            key=p["key"],
            value=p.get("value", "*"),
            geometry_type=p.get("geometry_type", "way"),
        )

    elif command == "search":
        return {"results": nominatim_search(
            p["query"],
            limit=int(p.get("limit", 5)),
        )}

    raise ValueError(f"Unhandled command: {command}")
