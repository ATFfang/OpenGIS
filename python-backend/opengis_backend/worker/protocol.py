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

    Accepted shape:
      - {"opengis_method": "rpc.ui.*", "params": {...}}
    """
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    raw_method = payload.get("opengis_method")
    if not isinstance(raw_method, str):
        return None
    method = raw_method
    raw_params = payload.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}

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
_STYLE_PAINT_KEYS = {
    "circle-color",
    "circle-radius",
    "circle-opacity",
    "circle-stroke-color",
    "circle-stroke-width",
    "circle-stroke-opacity",
    "line-color",
    "line-width",
    "line-opacity",
    "line-dasharray",
    "fill-color",
    "fill-opacity",
    "fill-outline-color",
    "stroke-color",
    "stroke-width",
    "stroke-opacity",
    "stroke-dasharray",
}


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _is_feature(value) -> bool:
    return isinstance(value, dict) and value.get("type") == "Feature" and isinstance(value.get("geometry"), dict)


def _normalize_style(style: dict | None, *, default_type: str | None = None) -> dict | None:
    if style is None:
        return None
    if not isinstance(style, dict):
        return None

    normalized = dict(style)
    paint = dict(normalized.get("paint") or {}) if isinstance(normalized.get("paint"), dict) else {}
    for key in list(normalized.keys()):
        if key in _STYLE_PAINT_KEYS:
            paint[key] = normalized.pop(key)
    if paint:
        normalized["paint"] = paint
    if default_type and not isinstance(normalized.get("type"), str):
        normalized["type"] = default_type
    return normalized


def _style_with_defaults(
    style: dict | None,
    *,
    render_type: str,
    color: str | None = None,
    size: float | int | None = None,
    width: float | int | None = None,
    opacity: float | int | None = None,
) -> dict | None:
    normalized = _normalize_style(style or {"type": render_type}, default_type=render_type)
    if normalized is None:
        normalized = {"type": render_type}
    paint = dict(normalized.get("paint") or {})
    alias_color = normalized.pop("color", None)
    alias_size = normalized.pop("size", None)
    alias_width = normalized.pop("width", None)
    alias_opacity = normalized.pop("opacity", None)
    if color is None and isinstance(alias_color, str) and alias_color:
        color = alias_color
    if size is None and alias_size is not None:
        size = alias_size
    if width is None and alias_width is not None:
        width = alias_width
    if opacity is None and alias_opacity is not None:
        opacity = alias_opacity
    if color is not None:
        if render_type == "line":
            paint.setdefault("line-color", color)
        elif render_type == "fill":
            paint.setdefault("fill-color", color)
        else:
            paint.setdefault("circle-color", color)
    if size is not None and render_type == "circle":
        try:
            paint.setdefault("circle-radius", float(size))
        except Exception:
            pass
    if width is not None and render_type == "line":
        try:
            paint.setdefault("line-width", float(width))
        except Exception:
            pass
    if opacity is not None:
        try:
            numeric_opacity = float(opacity)
        except Exception:
            numeric_opacity = None
        if numeric_opacity is not None:
            if render_type == "line":
                paint.setdefault("line-opacity", numeric_opacity)
            elif render_type == "fill":
                paint.setdefault("fill-opacity", numeric_opacity)
            else:
                paint.setdefault("circle-opacity", numeric_opacity)
    if paint:
        normalized["paint"] = paint
    return normalized


def _feature_id(feature: dict, fallback: str) -> str:
    raw_id = feature.get("id")
    props = feature.get("properties")
    if raw_id is None and isinstance(props, dict):
        raw_id = props.get("id") or props.get("track_id")
    return str(raw_id if raw_id is not None else fallback)


def _normalize_point_features(
    points,
    *,
    lon_key: str = "lon",
    lat_key: str = "lat",
    id_key: str = "id",
    label: str | None = None,
) -> list[dict]:
    if isinstance(points, dict) and points.get("type") == "FeatureCollection":
        features = points.get("features") if isinstance(points.get("features"), list) else []
        normalized = []
        for index, feature in enumerate(features):
            if not _is_feature(feature):
                continue
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "Point":
                continue
            props = dict(feature.get("properties") or {})
            fid = _feature_id(feature, str(index))
            props.setdefault("id", fid)
            normalized.append({
                "type": "Feature",
                "id": fid,
                "geometry": geometry,
                "properties": props,
            })
        return normalized

    normalized = []
    if not isinstance(points, list):
        return normalized
    for index, item in enumerate(points):
        if _is_feature(item):
            geometry = item.get("geometry") or {}
            if geometry.get("type") != "Point":
                continue
            props = dict(item.get("properties") or {})
            fid = _feature_id(item, str(index))
            props.setdefault("id", fid)
            normalized.append({
                "type": "Feature",
                "id": fid,
                "geometry": geometry,
                "properties": props,
            })
            continue
        if not isinstance(item, dict):
            continue
        raw_lon = item.get(lon_key, item.get("lon", item.get("lng", item.get("longitude"))))
        raw_lat = item.get(lat_key, item.get("lat", item.get("latitude")))
        try:
            lon_value = float(raw_lon)
            lat_value = float(raw_lat)
        except Exception:
            continue
        fid = item.get(id_key, item.get("id", item.get("icao24", item.get("callsign", index))))
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {
            key: value for key, value in item.items() if key not in {id_key, "id", lon_key, lat_key, "lon", "lng", "lat", "longitude", "latitude"}
        }
        props = dict(props or {})
        props.setdefault("id", str(fid))
        if label and label in item:
            props.setdefault("label", item.get(label))
        normalized.append(_point_feature(str(fid), lon_value, lat_value, props))
    return normalized


def _normalize_track_features(tracks, *, max_track_points: int = 200, id_key: str = "id") -> list[dict]:
    if isinstance(tracks, dict) and tracks.get("type") == "FeatureCollection":
        features = tracks.get("features") if isinstance(tracks.get("features"), list) else []
        normalized = []
        for index, feature in enumerate(features):
            if not _is_feature(feature):
                continue
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "LineString":
                continue
            coordinates = geometry.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                continue
            fid = _feature_id(feature, str(index))
            props = dict(feature.get("properties") or {})
            props.setdefault("track_id", fid)
            normalized.append(_line_feature(fid, coordinates[-max(2, int(max_track_points)):], props))
        return normalized

    features = []
    if isinstance(tracks, dict):
        iterable = tracks.items()
    elif isinstance(tracks, list):
        iterable = []
        for index, item in enumerate(tracks):
            if _is_feature(item):
                geometry = item.get("geometry") or {}
                if geometry.get("type") == "LineString":
                    coordinates = geometry.get("coordinates")
                    if isinstance(coordinates, list) and len(coordinates) >= 2:
                        fid = _feature_id(item, str(index))
                        props = dict(item.get("properties") or {})
                        props.setdefault("track_id", fid)
                        features.append(_line_feature(fid, coordinates[-max(2, int(max_track_points)):], props))
                continue
            if isinstance(item, dict):
                iterable.append((item.get(id_key, item.get("track_id", index)), item.get("coordinates", item.get("coords", []))))
    else:
        return features

    for track_id, coords in iterable:
        try:
            trimmed = list(coords)[-max(2, int(max_track_points)):]
        except Exception:
            continue
        if len(trimmed) < 2:
            continue
        features.append(_line_feature(str(track_id), trimmed, {"track_id": str(track_id)}))
    return features


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
        payload["style"] = _normalize_style(style)
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
        payload["style"] = _normalize_style(style)
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
    points,
    sequence: int,
    full: bool | None = None,
    style: dict | None = None,
    lon: str = "lon",
    lat: str = "lat",
    id_key: str = "id",
    label: str | None = None,
    size: float | int | None = None,
    color: str | None = None,
    opacity: float | int | None = None,
) -> None:
    """Emit moving point objects."""
    features = _normalize_point_features(points, lon_key=lon, lat_key=lat, id_key=id_key, label=label)
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    normalized_style = _style_with_defaults(style, render_type="circle", color=color, size=size, opacity=opacity)
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=normalized_style,
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
            style=normalized_style,
            sequence=sequence,
            schema_changed=False,
        )


def emit_dynamic_tracks(
    *,
    layer_id: str,
    name: str,
    tracks,
    sequence: int,
    full: bool | None = None,
    max_track_points: int = 200,
    style: dict | None = None,
    id_key: str = "id",
    label: str | None = None,
    width: float | int | None = None,
    color: str | None = None,
    opacity: float | int | None = None,
) -> None:
    """Emit trajectory LineString features keyed by moving object id."""
    features = _normalize_track_features(tracks, max_track_points=max_track_points, id_key=id_key)
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    normalized_style = _style_with_defaults(style, render_type="line", color=color, width=width, opacity=opacity)
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=normalized_style,
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
            style=normalized_style,
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
