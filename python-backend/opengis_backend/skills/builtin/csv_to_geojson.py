"""
CSV to GeoJSON conversion skill.

Reads a CSV file with coordinate columns (lat/lng) and converts it
to a GeoJSON FeatureCollection. Supports auto-detection of coordinate
column names and custom delimiter.
"""

import json
import csv
import re
from pathlib import Path
from typing import Optional

from opengis_backend.skills.registry import skill

# Common coordinate column name patterns
LAT_PATTERNS = [
    r'^lat$', r'^latitude$', r'^lat_?d$', r'^y$', r'^纬度$',
    r'^lat_wgs84$', r'^point_y$', r'^lat_dd$',
]
LNG_PATTERNS = [
    r'^lng$', r'^lon$', r'^long$', r'^longitude$', r'^lng_?d$',
    r'^x$', r'^经度$', r'^lon_wgs84$', r'^point_x$', r'^lon_dd$',
]


def _detect_column(headers: list[str], patterns: list[str]) -> Optional[str]:
    """Detect a column name matching one of the given regex patterns."""
    for pattern in patterns:
        for h in headers:
            if re.match(pattern, h.strip(), re.IGNORECASE):
                return h
    return None


@skill(
    name="csv_to_geojson",
    display_name="CSV to GeoJSON",
    description="Convert a CSV file with coordinate columns (latitude/longitude) to GeoJSON format. "
                "Auto-detects coordinate columns by name (lat, latitude, y, lng, lon, longitude, x, etc.).",
    category="data",
    params=[
        {"name": "input_path", "type": "string", "required": True,
         "description": "Path to the input CSV file"},
        {"name": "output_path", "type": "string", "required": False,
         "description": "Path for the output GeoJSON file (default: same name with .geojson extension)"},
        {"name": "lat_column", "type": "string", "required": False,
         "description": "Override latitude column name (auto-detected if not provided)"},
        {"name": "lng_column", "type": "string", "required": False,
         "description": "Override longitude column name (auto-detected if not provided)"},
        {"name": "delimiter", "type": "string", "required": False,
         "description": "CSV delimiter (auto-detected if not provided, default: comma)"},
    ],
    returns="Dict with output_path, feature_count, and conversion metadata",
    examples=[
        "Convert a CSV with lat/lng columns to GeoJSON",
        "Convert city coordinates CSV to map-ready GeoJSON",
    ],
    tags=["csv", "geojson", "conversion", "import"],
)
def csv_to_geojson(
    input_path: str,
    output_path: Optional[str] = None,
    lat_column: Optional[str] = None,
    lng_column: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> dict:
    """Convert CSV to GeoJSON."""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {input_path}")

    # Read CSV
    with open(path, "r", encoding="utf-8-sig") as f:
        # Auto-detect delimiter if not provided
        if not delimiter:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

        reader = csv.DictReader(f, delimiter=delimiter)
        headers = reader.fieldnames or []

        if not headers:
            raise ValueError(f"CSV file has no headers: {input_path}")

        # Detect coordinate columns
        lat_col = lat_column or _detect_column(headers, LAT_PATTERNS)
        lng_col = lng_column or _detect_column(headers, LNG_PATTERNS)

        if not lat_col or not lng_col:
            raise ValueError(
                f"Could not detect coordinate columns in CSV. "
                f"Headers: {headers}. "
                f"Expected columns like 'lat/latitude/y' and 'lng/longitude/x'."
            )

        # Build GeoJSON features
        features = []
        skipped = 0

        for i, row in enumerate(reader):
            try:
                lat = float(row[lat_col])
                lng = float(row[lng_col])
            except (ValueError, TypeError):
                skipped += 1
                continue

            if lat < -90 or lat > 90 or lng < -180 or lng > 180:
                skipped += 1
                continue

            # Build properties (exclude coordinate columns)
            properties = {}
            for key, value in row.items():
                if key in (lat_col, lng_col):
                    continue
                # Try to parse numbers
                if value and value.strip():
                    try:
                        properties[key] = float(value) if "." in value else int(value)
                    except ValueError:
                        properties[key] = value
                else:
                    properties[key] = None

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lng, lat],
                },
                "properties": properties,
                "id": i,
            })

    if not features:
        raise ValueError(f"No valid coordinate rows found in CSV: {input_path}")

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    # Write output
    if not output_path:
        output_path = str(path.with_suffix(".geojson"))

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    return {
        "success": True,
        "output_path": str(out_path),
        "feature_count": len(features),
        "skipped_rows": skipped,
        "geometry_type": "Point",
        "lat_column": lat_col,
        "lng_column": lng_col,
        "message": f"Converted {len(features)} rows to GeoJSON (skipped {skipped} invalid rows).",
    }
