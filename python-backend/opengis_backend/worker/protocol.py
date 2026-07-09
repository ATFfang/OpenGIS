"""Resident worker protocol contracts.

This module owns the wire contract between a long-running Python worker and
OpenGIS. The process manager should only launch processes and forward parsed
events; dynamic map semantics live here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


DYNAMIC_LAYER_METHOD = "rpc.ui.map.dynamic_layer_update"


@dataclass(frozen=True)
class WorkerEvent:
    method: str
    params: dict[str, Any]


def parse_worker_event(text: str) -> WorkerEvent | None:
    """Parse one stdout line emitted by a resident worker.

    Accepted shapes:
      - {"opengis_method": "rpc.ui.*", "params": {...}}
      - {"opengis_event": "dynamic_layer_update", ...params}
    """
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    method: str | None = None
    params: dict[str, Any] | None = None
    if isinstance(payload.get("opengis_method"), str):
        method = str(payload.get("opengis_method"))
        raw_params = payload.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
    elif payload.get("opengis_event") == "dynamic_layer_update":
        method = DYNAMIC_LAYER_METHOD
        params = {
            key: value
            for key, value in payload.items()
            if key not in {"opengis_event", "opengis_method"}
        }

    if not method or not method.startswith("rpc.ui."):
      return None
    return WorkerEvent(method=method, params=dict(params or {}))


WORKER_HELPER_CODE = '''"""OpenGIS resident worker helper API.

This file is generated next to each worker ``main.py`` and is importable from that
worker without installing any package:

    from opengis_worker import (
        emit_dynamic_layer_update,
        emit_dynamic_layer_diff,
        emit_dynamic_points,
        emit_dynamic_tracks,
        emit_moving_objects,
    )

The helper emits one compact JSON line to stdout. The OpenGIS worker manager
forwards that line to the frontend, where the map is updated.

Dynamic map protocol:
    1. Use a stable ``layer_id`` for the same live layer.
    2. Start with ``emit_dynamic_layer_update`` to send a full GeoJSON frame.
    3. For high-frequency changes, use ``emit_dynamic_layer_diff`` after the
       first full frame. Diff mode requires every feature to have a stable
       ``feature.id``. OpenGIS also accepts full GeoJSON Feature objects in
       ``diff["update"]`` and treats missing ids as upserts when possible.
    4. Use increasing ``sequence`` numbers; stale frames are ignored.
    5. For performance, pass ``bbox``, ``schema_changed=False``, and
       ``size_bytes`` when you know them.

High-level helpers:
    ``emit_dynamic_points``, ``emit_dynamic_tracks`` and
    ``emit_moving_objects`` send a full frame automatically the first time a
    layer id is used, then diff frames afterwards. Pass ``full=True`` to force
    a reset, or ``full=False`` only when you intentionally know the layer
    already exists.
"""

from __future__ import annotations

import json
import sys

_EMITTED_FULL_LAYERS: set[str] = set()


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _point_feature(
    feature_id: str,
    lon: float,
    lat: float,
    properties: dict | None = None,
) -> dict:
    props = dict(properties or {})
    props.setdefault("id", feature_id)
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def _line_feature(
    feature_id: str,
    coordinates: list,
    properties: dict | None = None,
) -> dict:
    props = dict(properties or {})
    props.setdefault("id", feature_id)
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": props,
    }


def _bbox_from_coordinates(coords: list) -> list[float] | None:
    flat: list[list[float]] = []

    def visit(value):
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            flat.append([float(value[0]), float(value[1])])
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(coords)
    if not flat:
        return None
    xs = [item[0] for item in flat]
    ys = [item[1] for item in flat]
    return [min(xs), min(ys), max(xs), max(ys)]


def _should_emit_full(layer_id: str, full: bool | None) -> bool:
    if full is None:
        return layer_id not in _EMITTED_FULL_LAYERS
    return bool(full)


def _mark_full_if_needed(layer_id: str, emitted_full: bool) -> None:
    if emitted_full:
        _EMITTED_FULL_LAYERS.add(layer_id)


def emit(method: str, params: dict) -> None:
    """Emit a raw frontend RPC notification.

    Prefer ``emit_dynamic_layer_update`` / ``emit_dynamic_layer_diff`` for map
    rendering. Use this only when you intentionally need another ``rpc.ui.*``
    method.
    """
    print(
        json.dumps(
            {"opengis_method": method, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def emit_dynamic_layer_update(
    *,
    layer_id: str,
    name: str,
    geojson: dict,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    style: dict | None = None,
    visible: bool = True,
    sequence: int | None = None,
    schema_changed: bool | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit a full GeoJSON frame for a dynamic map layer."""
    payload = {
        "mode": "full",
        "layer_id": layer_id,
        "name": name,
        "geojson": geojson,
        "visible": visible,
    }
    if bbox is not None:
        payload["bbox"] = list(bbox)
    if style is not None:
        payload["style"] = style
    if sequence is not None:
        payload["sequence"] = sequence
    if schema_changed is not None:
        payload["schema_changed"] = schema_changed
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
    emit("rpc.ui.map.dynamic_layer_update", payload)


def emit_dynamic_layer_diff(
    *,
    layer_id: str,
    diff: dict,
    name: str | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    style: dict | None = None,
    visible: bool | None = None,
    sequence: int | None = None,
    schema_changed: bool | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit a diff frame for a dynamic map layer.

    ``diff`` supports ``removeAll``, ``remove``, ``add`` and ``update``.
    Updates may be patch objects or full GeoJSON Feature objects with stable
    ids. If ids are unavailable, emit a full frame instead.
    """
    payload = {
        "mode": "diff",
        "layer_id": layer_id,
        "diff": diff,
    }
    if name is not None:
        payload["name"] = name
    if bbox is not None:
        payload["bbox"] = list(bbox)
    if style is not None:
        payload["style"] = style
    if visible is not None:
        payload["visible"] = visible
    if sequence is not None:
        payload["sequence"] = sequence
    if schema_changed is not None:
        payload["schema_changed"] = schema_changed
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
    emit("rpc.ui.map.dynamic_layer_update", payload)


def emit_dynamic_points(
    *,
    layer_id: str,
    name: str,
    points: list[dict],
    sequence: int,
    full: bool | None = None,
    style: dict | None = None,
) -> None:
    """Emit moving point objects."""
    features = [
        _point_feature(
            str(item["id"]),
            float(item["lon"]),
            float(item["lat"]),
            item.get("properties") if isinstance(item.get("properties"), dict) else {
                key: value for key, value in item.items() if key not in {"id", "lon", "lat"}
            },
        )
        for item in points
        if "id" in item and "lon" in item and "lat" in item
    ]
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=True,
        )
        _mark_full_if_needed(layer_id, True)
    else:
        emit_dynamic_layer_diff(
            layer_id=layer_id,
            name=name,
            diff={"update": features},
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=False,
        )


def emit_dynamic_tracks(
    *,
    layer_id: str,
    name: str,
    tracks: dict[str, list],
    sequence: int,
    full: bool | None = None,
    max_track_points: int = 200,
    style: dict | None = None,
) -> None:
    """Emit trajectory LineString features keyed by moving object id."""
    features = []
    for track_id, coords in tracks.items():
        trimmed = list(coords)[-max(2, int(max_track_points)):]
        if len(trimmed) < 2:
            continue
        features.append(_line_feature(str(track_id), trimmed, {"track_id": str(track_id)}))
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=True,
        )
        _mark_full_if_needed(layer_id, True)
    else:
        emit_dynamic_layer_diff(
            layer_id=layer_id,
            name=name,
            diff={"update": features},
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=False,
        )


def emit_moving_objects(
    *,
    point_layer_id: str,
    track_layer_id: str,
    points: list[dict],
    tracks: dict[str, list],
    sequence: int,
    point_name: str = "Live Points",
    track_name: str = "Live Tracks",
    full: bool | None = None,
    max_track_points: int = 200,
    point_style: dict | None = None,
    track_style: dict | None = None,
) -> None:
    """Emit synchronized moving points and their trajectories."""
    emit_dynamic_tracks(
        layer_id=track_layer_id,
        name=track_name,
        tracks=tracks,
        sequence=sequence,
        full=full,
        max_track_points=max_track_points,
        style=track_style,
    )
    emit_dynamic_points(
        layer_id=point_layer_id,
        name=point_name,
        points=points,
        sequence=sequence,
        full=full,
        style=point_style,
    )
'''


__all__ = ["DYNAMIC_LAYER_METHOD", "WORKER_HELPER_CODE", "WorkerEvent", "parse_worker_event"]
