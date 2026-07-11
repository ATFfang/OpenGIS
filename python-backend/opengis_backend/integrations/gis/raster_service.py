"""Raster metadata and preview rendering helpers.

This is the backend half of OpenGIS' hybrid raster path.  The frontend can
decode small GeoTIFF files directly with geotiff.js; the backend handles
formats that require GDAL/rasterio and prepares a georeferenced PNG preview
until a full tile pyramid service is attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import math
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RasterInfo:
    path: str
    name: str
    driver: str
    width: int
    height: int
    band_count: int
    crs: str | None
    bbox: tuple[float, float, float, float] | None
    source_bbox: tuple[float, float, float, float] | None
    nodata: Any
    dtype: str
    band_stats: list[dict[str, float | int | None]]
    resolution: tuple[float, float] | None
    file_size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "driver": self.driver,
            "width": self.width,
            "height": self.height,
            "band_count": self.band_count,
            "crs": self.crs,
            "bbox": list(self.bbox) if self.bbox else None,
            "source_bbox": list(self.source_bbox) if self.source_bbox else None,
            "nodata": self.nodata,
            "dtype": self.dtype,
            "band_stats": self.band_stats,
            "resolution": list(self.resolution) if self.resolution else None,
            "file_size_bytes": self.file_size_bytes,
        }


@dataclass(frozen=True)
class RasterPreview:
    image_path: str
    bbox: tuple[float, float, float, float]
    info: RasterInfo

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_path": self.image_path,
            "bbox": list(self.bbox),
            "info": self.info.to_dict(),
        }


@dataclass
class RasterTileRegistration:
    raster_id: str
    path: str
    info: RasterInfo
    style: dict[str, Any] = field(default_factory=dict)
    style_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "raster_id": self.raster_id,
            "path": self.path,
            "info": self.info.to_dict(),
            "style": self.style,
            "style_revision": self.style_revision,
        }


_REGISTRY_LOCK = RLock()
_RASTER_REGISTRY: dict[str, RasterTileRegistration] = {}


def inspect_raster(path: str, *, max_stat_pixels: int = 250_000) -> RasterInfo:
    rasterio = _rasterio()
    src_path = str(Path(path).expanduser().resolve())
    with rasterio.open(src_path) as src:
        source_bbox = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
        bbox = _bounds_to_wgs84(src, source_bbox)
        stats: list[dict[str, float | int | None]] = []
        for band_index in range(1, src.count + 1):
            stats.append(_band_stats(src, band_index, max_pixels=max_stat_pixels))
        return RasterInfo(
            path=src_path,
            name=Path(src_path).name,
            driver=str(src.driver),
            width=int(src.width),
            height=int(src.height),
            band_count=int(src.count),
            crs=str(src.crs) if src.crs else None,
            bbox=bbox,
            source_bbox=source_bbox,
            nodata=src.nodata,
            dtype=str(src.dtypes[0]) if src.dtypes else "unknown",
            band_stats=stats,
            resolution=(float(src.res[0]), float(src.res[1])) if src.res else None,
            file_size_bytes=Path(src_path).stat().st_size,
        )


def register_raster(path: str, *, style: dict[str, Any] | None = None) -> RasterTileRegistration:
    """Register a local raster for backend XYZ tile rendering."""
    src_path = str(Path(path).expanduser().resolve())
    info = inspect_raster(src_path)
    if not info.bbox:
        raise RuntimeError("Raster has no georeferenced bounds.")
    registration = RasterTileRegistration(
        raster_id=f"rst_{uuid4().hex}",
        path=src_path,
        info=info,
        style=_normalize_style(style),
    )
    with _REGISTRY_LOCK:
        _RASTER_REGISTRY[registration.raster_id] = registration
    return registration


def get_registered_raster(raster_id: str) -> RasterTileRegistration:
    with _REGISTRY_LOCK:
        registration = _RASTER_REGISTRY.get(raster_id)
    if not registration:
        raise KeyError(f"Raster '{raster_id}' is not registered.")
    return registration


def update_registered_raster_style(raster_id: str, style: dict[str, Any]) -> RasterTileRegistration:
    with _REGISTRY_LOCK:
        registration = _RASTER_REGISTRY.get(raster_id)
        if not registration:
            raise KeyError(f"Raster '{raster_id}' is not registered.")
        registration.style = _normalize_style({**registration.style, **style})
        registration.style_revision += 1
        return registration


def render_registered_raster_tile(raster_id: str, z: int, x: int, y: int, *, tile_size: int = 256) -> bytes:
    """Render one XYZ tile as PNG bytes.

    Tiles are rendered in EPSG:3857 through Rasterio's WarpedVRT so source
    rasters can remain in their native CRS. This keeps the frontend thin:
    MapLibre asks for ordinary XYZ PNG tiles, while GDAL handles windowed
    reads and reprojection on demand.
    """
    rasterio = _rasterio()
    np = _numpy()
    registration = get_registered_raster(raster_id)
    style = registration.style
    bounds_3857 = _xyz_bounds_mercator(z, x, y)

    with rasterio.open(registration.path) as src:
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds
        from rasterio.warp import reproject, transform_bounds

        if not src.crs:
            raise RuntimeError("Raster has no CRS; cannot render XYZ tiles.")
        source_bounds_3857 = transform_bounds(src.crs, "EPSG:3857", *src.bounds, densify_pts=21)
        if not _bounds_intersect(bounds_3857, tuple(float(v) for v in source_bounds_3857)):
            return _transparent_png(tile_size)

        band_index = max(1, min(int(style.get("band") or 1), src.count))
        arr = np.full((tile_size, tile_size), np.nan, dtype="float64")
        reproject(
            source=rasterio.band(src, band_index),
            destination=arr,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=from_bounds(*bounds_3857, tile_size, tile_size),
            dst_crs="EPSG:3857",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    mask = ~np.isfinite(arr)
    valid = arr[~mask]
    if valid.size == 0:
        return _transparent_png(tile_size)

    stats = registration.info.band_stats[band_index - 1] if band_index - 1 < len(registration.info.band_stats) else {}
    vmin = _float_or_none(style.get("min"))
    vmax = _float_or_none(style.get("max"))
    if vmin is None:
        vmin = _float_or_none(stats.get("p2")) or _float_or_none(stats.get("min")) or float(np.nanmin(valid))
    if vmax is None:
        vmax = _float_or_none(stats.get("p98")) or _float_or_none(stats.get("max")) or float(np.nanmax(valid))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin == vmax:
        vmin = float(np.nanmin(valid))
        vmax = float(np.nanmax(valid))
    if vmin == vmax:
        return _transparent_png(tile_size)

    norm = np.clip((arr - vmin) / (vmax - vmin), 0, 1)
    if style.get("reverse"):
        norm = 1.0 - norm
    rgba = _style_rgba(norm, style, vmin=vmin, vmax=vmax)
    opacity = max(0.0, min(1.0, float(style.get("opacity", 1.0))))
    rgba[..., 3] = np.where(mask, 0.0, rgba[..., 3] * opacity)
    rgba[..., 0:3] = np.where(mask[..., None], 0.0, rgba[..., 0:3])
    return _rgba_to_png_bytes(rgba)


def render_raster_preview(
    path: str,
    *,
    output_dir: str,
    band: int = 1,
    ramp: str = "viridis",
    stops: Any = None,
    stops_unit: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    opacity: float = 1.0,
    reverse: bool = False,
    max_dim: int = 4096,
) -> RasterPreview:
    rasterio = _rasterio()
    np = _numpy()
    src_path = str(Path(path).expanduser().resolve())
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"raster_preview_{uuid4().hex}.png"

    with rasterio.open(src_path) as src:
        info = inspect_raster(src_path)
        if not info.bbox:
            raise RuntimeError("Raster has no georeferenced bounds.")
        band_index = max(1, min(int(band), src.count))
        scale = min(1.0, max_dim / max(src.width, src.height))
        out_width = max(1, int(src.width * scale))
        out_height = max(1, int(src.height * scale))
        data = src.read(
            band_index,
            out_shape=(out_height, out_width),
            masked=True,
        )
        arr = np.asarray(data, dtype="float64")
        mask = np.ma.getmaskarray(data)
        valid = arr[~mask]
        if valid.size == 0:
            raise RuntimeError("Raster band contains no valid pixels.")
        vmin = float(min_value) if min_value is not None else float(np.nanpercentile(valid, 2))
        vmax = float(max_value) if max_value is not None else float(np.nanpercentile(valid, 98))
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin == vmax:
            vmin = float(np.nanmin(valid))
            vmax = float(np.nanmax(valid))
        norm = np.clip((arr - vmin) / (vmax - vmin or 1.0), 0, 1)
        if reverse:
            norm = 1.0 - norm
        rgba = _style_rgba(
            norm,
            {
                "ramp": ramp,
                "stops": stops,
                "stopsUnit": stops_unit,
                "opacity": opacity,
            },
            vmin=vmin,
            vmax=vmax,
        )
        global_opacity = max(0.0, min(1.0, float(opacity)))
        rgba[..., 3] = np.where(mask, 0.0, rgba[..., 3] * global_opacity)
        _save_png(out_path, rgba)
        return RasterPreview(image_path=str(out_path), bbox=info.bbox, info=info)


def _band_stats(src: Any, band_index: int, *, max_pixels: int) -> dict[str, float | int | None]:
    np = _numpy()
    scale = min(1.0, (max_pixels / max(1, src.width * src.height)) ** 0.5)
    out_width = max(1, int(src.width * scale))
    out_height = max(1, int(src.height * scale))
    data = src.read(band_index, out_shape=(out_height, out_width), masked=True)
    arr = np.asarray(data.compressed() if hasattr(data, "compressed") else data).astype("float64")
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"band": band_index, "min": None, "max": None, "mean": None, "p2": None, "p98": None}
    return {
        "band": band_index,
        "min": float(np.nanmin(arr)),
        "max": float(np.nanmax(arr)),
        "mean": float(np.nanmean(arr)),
        "p2": float(np.nanpercentile(arr, 2)),
        "p98": float(np.nanpercentile(arr, 98)),
    }


def _bounds_to_wgs84(src: Any, bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    if not src.crs:
        return None
    crs_text = str(src.crs).upper()
    if "4326" in crs_text or crs_text in {"EPSG:4326", "OGC:CRS84"}:
        return tuple(float(v) for v in bounds)
    try:
        from rasterio.warp import transform_bounds

        return tuple(float(v) for v in transform_bounds(src.crs, "EPSG:4326", *bounds, densify_pts=21))
    except Exception as exc:  # pragma: no cover - depends on GDAL CRS support
        raise RuntimeError(f"Failed to transform raster bounds to EPSG:4326: {exc}") from exc


def _style_rgba(norm: Any, style: dict[str, Any], *, vmin: float = 0.0, vmax: float = 1.0) -> Any:
    np = _numpy()
    stops = style.get("stops")
    if stops:
        return _custom_stops_rgba(norm, stops, stops_unit=style.get("stopsUnit"), vmin=vmin, vmax=vmax)
    return _colormap_rgba(norm, str(style.get("ramp") or "viridis"))


def _custom_stops_rgba(
    norm: Any,
    stops: Any,
    *,
    stops_unit: Any = None,
    vmin: float = 0.0,
    vmax: float = 1.0,
) -> Any:
    np = _numpy()
    clean: list[tuple[float, tuple[float, float, float], float]] = []
    if isinstance(stops, str):
        import json

        stops = json.loads(stops)
    use_source_values = _style_uses_source_stops(stops, stops_unit=stops_unit, vmin=vmin, vmax=vmax)
    value_range = vmax - vmin or 1.0
    for item in stops if isinstance(stops, list) else []:
        if not isinstance(item, dict):
            continue
        raw_value = float(item.get("value", 0))
        value = (raw_value - vmin) / value_range if use_source_values else raw_value
        value = max(0.0, min(1.0, value))
        color = _hex_to_rgb01(str(item.get("color") or "#000000"))
        alpha = max(0.0, min(1.0, float(item.get("opacity", 1.0))))
        clean.append((value, color, alpha))
    if len(clean) < 2:
        return _colormap_rgba(norm, "viridis")
    clean.sort(key=lambda x: x[0])
    values = np.asarray([s[0] for s in clean], dtype="float64")
    red = np.asarray([s[1][0] for s in clean], dtype="float64")
    green = np.asarray([s[1][1] for s in clean], dtype="float64")
    blue = np.asarray([s[1][2] for s in clean], dtype="float64")
    alpha = np.asarray([s[2] for s in clean], dtype="float64")
    return np.stack(
        [
            np.interp(norm, values, red),
            np.interp(norm, values, green),
            np.interp(norm, values, blue),
            np.interp(norm, values, alpha),
        ],
        axis=-1,
    )


def _style_uses_source_stops(stops: Any, *, stops_unit: Any, vmin: float, vmax: float) -> bool:
    unit = str(stops_unit or "").strip().lower().replace("-", "_")
    if unit in {"source", "data", "pixel", "pixel_value", "raw"}:
        return True
    if unit in {"normalized", "normalised", "normalize", "normalise", "0_1", "zero_one"}:
        return False
    values: list[float] = []
    for item in stops if isinstance(stops, list) else []:
        if isinstance(item, dict):
            value = _float_or_none(item.get("value"))
            if value is not None:
                values.append(value)
    if not values:
        return False
    if any(value < 0 or value > 1 for value in values):
        return True
    data_range = abs(vmax - vmin)
    if data_range <= 0 or data_range >= 1:
        return False
    data_extent = max(abs(vmin), abs(vmax), data_range)
    return any(value > 0 and value <= data_extent * 1.5 for value in values)


def _colormap_rgba(norm: Any, ramp: str) -> Any:
    cm = _matplotlib_cm()
    ramp_key = str(ramp or "viridis").lower()
    name = ramp_key if ramp_key in {"viridis", "magma", "plasma", "inferno", "turbo", "gray", "terrain", "spectral"} else "viridis"
    if name == "spectral":
        name = "Spectral"
    return cm.get_cmap(name)(norm)


def _normalize_style(style: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(style or {})
    normalized: dict[str, Any] = {
        "band": max(1, int(raw.get("band") or 1)),
        "ramp": str(raw.get("ramp") or ("custom" if raw.get("stops") else "viridis")),
        "opacity": max(0.0, min(1.0, float(raw.get("opacity", 1.0)))),
        "mode": str(raw.get("mode") or "singleband"),
    }
    for key in ("min", "max"):
        value = _float_or_none(raw.get(key))
        if value is not None:
            normalized[key] = value
    if raw.get("reverse") is not None:
        normalized["reverse"] = bool(raw.get("reverse"))
    if raw.get("stops"):
        normalized["stops"] = raw["stops"]
        normalized["stopsUnit"] = _normalize_stops_unit(raw.get("stopsUnit"))
        normalized["ramp"] = "custom"
    return normalized


def _normalize_stops_unit(value: Any) -> str:
    unit = str(value or "source").strip().lower().replace("-", "_")
    if unit in {"source", "data", "pixel", "pixel_value", "raw"}:
        return "source"
    if unit in {"normalized", "normalised", "normalize", "normalise", "0_1", "zero_one"}:
        return "normalized"
    return "source"


def _xyz_bounds_mercator(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    limit = 20037508.342789244
    tiles = 2 ** int(z)
    tile_span = (limit * 2) / tiles
    minx = -limit + int(x) * tile_span
    maxx = minx + tile_span
    maxy = limit - int(y) * tile_span
    miny = maxy - tile_span
    return (minx, miny, maxx, maxy)


def _bounds_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _transparent_png(size: int) -> bytes:
    np = _numpy()
    rgba = np.zeros((size, size, 4), dtype="uint8")
    return _rgba_to_png_bytes(rgba)


def _rgba_to_png_bytes(rgba: Any) -> bytes:
    np = _numpy()
    arr = np.asarray(rgba)
    if arr.dtype != np.uint8:
        arr = np.clip(arr * 255, 0, 255).astype("uint8")
    try:
        from PIL import Image

        image = Image.fromarray(arr, mode="RGBA")
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        plt = _matplotlib_pyplot()
        buf = BytesIO()
        plt.imsave(buf, arr, format="png")
        return buf.getvalue()


def _hex_to_rgb01(color: str) -> tuple[float, float, float]:
    text = color.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) < 6:
        text = "000000"
    try:
        return (
            int(text[0:2], 16) / 255,
            int(text[2:4], 16) / 255,
            int(text[4:6], 16) / 255,
        )
    except ValueError:
        return (0.0, 0.0, 0.0)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _save_png(path: Path, rgba: Any) -> None:
    plt = _matplotlib_pyplot()
    plt.imsave(str(path), rgba)


def _rasterio() -> Any:
    try:
        import rasterio

        return rasterio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Raster backend requires rasterio. Install rasterio to load non-GeoTIFF raster formats.") from exc


def _numpy() -> Any:
    try:
        import numpy as np

        return np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Raster backend requires numpy.") from exc


def _matplotlib_cm() -> Any:
    try:
        import matplotlib.cm as cm

        return cm
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Raster preview rendering requires matplotlib.") from exc


def _matplotlib_pyplot() -> Any:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Raster preview rendering requires matplotlib.") from exc


__all__ = [
    "RasterInfo",
    "RasterPreview",
    "RasterTileRegistration",
    "get_registered_raster",
    "inspect_raster",
    "register_raster",
    "render_raster_preview",
    "render_registered_raster_tile",
    "update_registered_raster_style",
]
