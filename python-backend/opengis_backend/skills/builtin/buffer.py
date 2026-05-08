"""Buffer analysis skill — create buffer zones around features.

Handles CRS-aware buffering: if the input data is in a geographic CRS
(e.g. WGS84 / EPSG:4326), it automatically projects to a suitable UTM
zone, performs the buffer in meters, then projects back to WGS84.
"""

from pathlib import Path
from typing import Optional

from opengis_backend.skills.registry import skill


def _estimate_utm_crs(gdf):
    """Estimate the best UTM CRS for a GeoDataFrame based on its centroid."""
    import pyproj

    centroid = gdf.geometry.union_all().centroid
    lon, lat = centroid.x, centroid.y

    # Determine UTM zone number
    zone_number = int((lon + 180) / 6) + 1
    # Determine hemisphere
    hemisphere = "north" if lat >= 0 else "south"

    epsg = 32600 + zone_number if hemisphere == "north" else 32700 + zone_number
    return pyproj.CRS.from_epsg(epsg)


@skill(
    name="buffer_analysis",
    display_name="Buffer Analysis",
    description=(
        "Create buffer zones around geographic features. "
        "Generates a new polygon layer where each feature is expanded "
        "by the specified distance in meters. Automatically handles "
        "CRS projection for accurate metric buffering. "
        "Supports GeoJSON, Shapefile, GeoPackage, and CSV files with coordinate columns. "
        "Useful for proximity analysis, impact zones, and service area delineation."
    ),
    category="vector",
    params=[
        {
            "name": "input_path",
            "type": "file_path",
            "description": "Path to the input GIS file (GeoJSON, Shapefile, GeoPackage, etc.)",
        },
        {
            "name": "distance",
            "type": "number",
            "description": "Buffer distance in meters",
        },
        {
            "name": "output_path",
            "type": "string",
            "description": "Path for the output GeoJSON file. If not provided, auto-generates one.",
            "required": False,
            "default": None,
        },
    ],
    returns="Dict with output_path, feature_count, and buffer metadata",
    examples=[
        "Create a 500m buffer around all schools",
        "Buffer the river network by 100 meters",
        "Generate a 1km impact zone around the factory",
    ],
    tags=["buffer", "proximity", "zone", "distance"],
)
def buffer_analysis(
    input_path: str,
    distance: float,
    output_path: Optional[str] = None,
) -> dict:
    """Execute buffer analysis using GeoPandas with proper CRS handling."""
    import geopandas as gpd
    import pandas as pd
    import json
    import numpy as np
    from shapely.geometry import Point

    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Handle CSV input: auto-convert to GeoDataFrame
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(input_path, encoding="utf-8-sig")

        # Auto-detect coordinate columns
        lat_col = None
        lng_col = None
        lat_candidates = ["lat", "latitude", "y", "lat_wgs84", "point_y"]
        lng_candidates = ["lng", "lon", "long", "longitude", "x", "lon_wgs84", "point_x"]

        lower_cols = {c.lower(): c for c in df.columns}
        for candidate in lat_candidates:
            if candidate in lower_cols:
                lat_col = lower_cols[candidate]
                break
        for candidate in lng_candidates:
            if candidate in lower_cols:
                lng_col = lower_cols[candidate]
                break

        if not lat_col or not lng_col:
            raise ValueError(
                f"Could not detect coordinate columns in CSV. "
                f"Columns: {list(df.columns)}. "
                f"Expected columns like 'lat/latitude/y' and 'lng/longitude/x'."
            )

        geometry = [Point(xy) for xy in zip(df[lng_col], df[lat_col])]
        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    else:
        # Read standard GIS formats
        gdf = gpd.read_file(input_path)

        # Ensure we have a GeoDataFrame (not a plain DataFrame)
        if not isinstance(gdf, gpd.GeoDataFrame):
            raise ValueError(
                f"Failed to read as GeoDataFrame: {input_path}. "
                f"The file may not contain valid geometry data."
            )

    if gdf.empty:
        raise ValueError(f"Input file contains no features: {input_path}")

    original_crs = gdf.crs
    used_projection = False

    # If the data is in a geographic CRS (degrees), project to UTM for metric buffer
    if original_crs is not None and original_crs.is_geographic:
        utm_crs = _estimate_utm_crs(gdf)
        gdf_projected = gdf.to_crs(utm_crs)
        used_projection = True
    elif original_crs is None:
        # Assume WGS84 if no CRS is set
        gdf = gdf.set_crs("EPSG:4326")
        original_crs = gdf.crs
        utm_crs = _estimate_utm_crs(gdf)
        gdf_projected = gdf.to_crs(utm_crs)
        used_projection = True
    else:
        # Already in a projected CRS (meters)
        gdf_projected = gdf

    # Perform buffer in projected coordinates (meters)
    buffered = gdf_projected.copy()
    buffered["geometry"] = gdf_projected.geometry.buffer(distance)

    # Project back to original CRS (WGS84) for output
    if used_projection:
        buffered = buffered.to_crs("EPSG:4326")

    # Determine output path
    if not output_path:
        stem = path.stem
        output_path = str(path.parent / f"{stem}_buffer_{int(distance)}m.geojson")

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write output as GeoJSON
    buffered.to_file(str(out_path), driver="GeoJSON")

    # Compute bounding box of result
    bounds = buffered.total_bounds  # [minx, miny, maxx, maxy]

    return {
        "success": True,
        "output_path": str(out_path),
        "feature_count": len(buffered),
        "distance_meters": distance,
        "geometry_type": "Polygon",
        "used_projection": used_projection,
        "bbox": [float(b) for b in bounds],
        "message": (
            f"Created {distance}m buffer for {len(buffered)} features. "
            f"Output saved to: {out_path}"
        ),
    }
