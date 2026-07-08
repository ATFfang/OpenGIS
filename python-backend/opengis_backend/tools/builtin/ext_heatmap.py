"""Tool: ext_heatmap_render — extension heatmap rendering.

Renders a GPU-accelerated heatmap on the frontend map using the extension
layer (ext.heatmap.render RPC).  The frontend heatmap extension handles
MapLibre's native heatmap layer type directly, bypassing the base layer
store.

The weight of each feature is read from a named property and normalised
to [0, 1] before being sent to the frontend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opengis_backend.tools.registry import tool
from opengis_backend.tools.context import ToolContext, run_async_from_sync
from opengis_backend.tools.builtin.ext_registry import register_extension

# ── Register capability so list_extensions picks it up ─────────────────

register_extension({
    "name": "heatmap",
    "display_name": "Extension Heatmap",
    "description": (
        "Render a heatmap from GeoJSON point data with per-feature weights. "
        "GPU-accelerated via MapLibre native heatmap layer."
    ),
    "params": [
        {"name": "geojson_path", "type": "string", "required": True,
         "description": "Path to a GeoJSON file (FeatureCollection of Point features)."},
        {"name": "weight_field", "type": "string", "required": False,
         "description": "Property name to use as weight. If omitted, all features get weight 1."},
        {"name": "radius", "type": "number", "required": False,
         "description": "Kernel radius in pixels (default: 30)."},
        {"name": "intensity", "type": "number", "required": False,
         "description": "Global intensity multiplier (default: 1)."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Layer opacity 0-1 (default: 0.8)."},
    ],
})


# ── Tool implementation ────────────────────────────────────────────────

@tool(
    name="ext_heatmap_render",
    display_name="Extension: Heatmap Render",
    description=(
        "Render a heatmap layer on the map from a GeoJSON point file. "
        "Each feature's weight is read from the specified property (or defaults to 1) "
        "and normalised to [0, 1].  The heatmap is GPU-accelerated and rendered "
        "directly on the map via the extension layer."
    ),
    category="visualization",
    group="core",
    params=[
        {"name": "geojson_path", "type": "string", "required": True,
         "description": "Path to a GeoJSON file (FeatureCollection of Point features)."},
        {"name": "weight_field", "type": "string", "required": False,
         "description": "Property name to use as weight. Omit for uniform weight."},
        {"name": "radius", "type": "number", "required": False,
         "description": "Kernel radius in pixels (default: 30)."},
        {"name": "intensity", "type": "number", "required": False,
         "description": "Intensity multiplier (default: 1)."},
        {"name": "opacity", "type": "number", "required": False,
         "description": "Layer opacity 0-1 (default: 0.8)."},
    ],
    returns="dict with feature_count and layer_id",
    needs_context=True,
)
def ext_heatmap_render(
    ctx: ToolContext,
    geojson_path: str,
    weight_field: str | None = None,
    radius: float | None = None,
    intensity: float | None = None,
    opacity: float | None = None,
) -> dict[str, Any]:
    """Read GeoJSON, normalise weights, send ext.heatmap.render RPC."""
    fc = _load_geojson(geojson_path)

    if weight_field:
        _normalise_weights(fc, weight_field)

    payload: dict[str, Any] = {"geojson": fc}
    if radius is not None:
        payload["radius"] = radius
    if intensity is not None:
        payload["intensity"] = intensity
    if opacity is not None:
        payload["opacity"] = opacity

    run_async_from_sync(ctx.notify("ext.heatmap.render", payload))

    return {
        "feature_count": len(fc.get("features", [])),
        "layer_id": "ext-heatmap-layer",
    }


@tool(
    name="ext_heatmap_remove",
    display_name="Extension: Heatmap Remove",
    description="Remove the extension heatmap layer from the map.",
    category="visualization",
    group="core",
    params=[],
    returns="dict with status",
    needs_context=True,
)
def ext_heatmap_remove(ctx: ToolContext) -> dict[str, str]:
    """Send ext.heatmap.remove RPC to clear the heatmap layer."""
    run_async_from_sync(ctx.notify("ext.heatmap.remove", {}))
    return {"status": "removed"}


# ── Helpers ────────────────────────────────────────────────────────────

def _load_geojson(path: str) -> dict:
    """Load a GeoJSON FeatureCollection from a file path."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def _normalise_weights(fc: dict, field: str) -> None:
    """Normalise the named property to [0, 1] across all features (in-place)."""
    features = fc.get("features", [])
    values = [
        f["properties"].get(field)
        for f in features
        if isinstance(f.get("properties"), dict) and isinstance(f["properties"].get(field), (int, float))
    ]
    if not values:
        # No valid numeric values — fall back to uniform weight 1.
        for f in features:
            props = f.setdefault("properties", {})
            props["weight"] = 1
        return

    lo, hi = min(values), max(values)
    span = hi - lo if hi != lo else 1.0

    for f in features:
        props = f.setdefault("properties", {})
        raw = props.get(field)
        props["weight"] = (raw - lo) / span if isinstance(raw, (int, float)) else 0
