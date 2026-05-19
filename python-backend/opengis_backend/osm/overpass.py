"""Overpass API & Nominatim client for OpenStreetMap data."""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger("opengis.osm")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
_USER_AGENT = {"User-Agent": "OpenGIS/1.0 (opengis-app)"}

# Rate-limit: Overpass ~2 req/s, Nominatim ~1 req/s
_last_overpass_call: float = 0.0
_last_nominatim_call: float = 0.0


def _throttle(interval: float, last_call_attr: str) -> None:
    """Simple rate limiter."""
    import osm.overpass as mod
    last = getattr(mod, last_call_attr)
    wait = interval - (time.time() - last)
    if wait > 0:
        time.sleep(wait)


def overpass_query(query: str) -> dict[str, Any]:
    """Run an Overpass QL query and return the JSON response.

    The query should be raw Overpass QL WITHOUT the [out:json] header —
    it is prepended automatically.
    """
    global _last_overpass_call
    wait = 1.0 - (time.time() - _last_overpass_call)
    if wait > 0:
        time.sleep(wait)

    full_query = f"[out:json][timeout:60];\n{query}"
    logger.info("Overpass query: %s", full_query[:200])

    resp = requests.post(
        OVERPASS_URL,
        data={"data": full_query},
        headers=_USER_AGENT,
        timeout=120,
    )
    _last_overpass_call = time.time()
    resp.raise_for_status()
    return resp.json()


def osm_to_geojson(data: dict) -> dict[str, Any]:
    """Convert Overpass JSON response to GeoJSON FeatureCollection."""
    features: list[dict] = []

    for element in data.get("elements", []):
        etype = element.get("type")
        tags = element.get("tags", {})

        if etype == "node":
            lat = element.get("lat")
            lon = element.get("lon")
            if lat is None or lon is None:
                continue
            geometry = {"type": "Point", "coordinates": [lon, lat]}

        elif etype == "way":
            nodes = element.get("geometry", [])
            coords = [[n["lon"], n["lat"]] for n in nodes if "lat" in n and "lon" in n]
            if len(coords) < 2:
                continue
            # Closed way → Polygon, open way → LineString
            if coords[0] == coords[-1] and len(coords) >= 4:
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}

        elif etype == "relation":
            # Skip relations for simplicity — they're complex multipolygons
            continue
        else:
            continue

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                **tags,
                "_osm_id": element.get("id"),
                "_osm_type": etype,
            },
        }
        features.append(feature)

    return {"type": "FeatureCollection", "features": features}


def nominatim_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search for a place by name using Nominatim.

    Returns a list of results with display_name, lat, lon, boundingbox.
    """
    global _last_nominatim_call
    wait = 1.05 - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)

    resp = requests.get(
        f"{NOMINATIM_URL}/search",
        params={"q": query, "format": "json", "limit": limit, "addressdetails": 1},
        headers=_USER_AGENT,
        timeout=30,
    )
    _last_nominatim_call = time.time()
    resp.raise_for_status()
    return resp.json()


def bbox_from_place(place: str) -> list[float] | None:
    """Get bounding box [south, north, west, east] for a place name."""
    results = nominatim_search(place, limit=1)
    if not results:
        return None
    bb = results[0].get("boundingbox")
    if bb and len(bb) == 4:
        return [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
    return None
