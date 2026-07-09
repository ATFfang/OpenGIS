"""GIS data loader — unified pipeline for loading various GIS formats."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FileMetadata:
    """Metadata about a GIS file."""
    path: str
    name: str
    format_type: str       # "vector" or "raster"
    format_name: str       # e.g., "Shapefile", "GeoJSON", "GeoTIFF"
    file_size_mb: float
    crs: str | None
    bounds: tuple[float, float, float, float] | None  # (minx, miny, maxx, maxy)
    feature_count: int | None  # For vector
    fields: list[dict] | None  # For vector: [{"name": "...", "type": "..."}]
    band_count: int | None     # For raster
    resolution: tuple[float, float] | None  # For raster: (x_res, y_res)


@dataclass
class LoadResult:
    """Result of loading a GIS file."""
    type: str              # "geojson" | "vector_tiles" | "raster_tiles"
    data: Any = None       # GeoJSON dict for direct transfer
    tile_url: str | None = None  # URL for tile-based transfer
    metadata: FileMetadata | None = None


# Format detection mapping
VECTOR_EXTENSIONS = {
    ".shp": "Shapefile",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GeoPackage",
    ".kml": "KML",
    ".kmz": "KMZ",
    ".gml": "GML",
    ".csv": "CSV",
    ".xlsx": "Excel",
    ".fgb": "FlatGeobuf",
}

RASTER_EXTENSIONS = {
    ".tif": "GeoTIFF",
    ".tiff": "GeoTIFF",
    ".nc": "NetCDF",
    ".hdf5": "HDF5",
    ".h5": "HDF5",
    ".jp2": "JPEG2000",
    ".asc": "ASCII Grid",
}


class GISLoader:
    """
    Unified GIS data loading pipeline.

    Pipeline:
    File Path → Format Detection → Read (GDAL/Fiona/Rasterio)
    → CRS Validation → Simplification (if needed) → Serialization → Transfer
    """

    def detect_format(self, path: str) -> tuple[str, str]:
        """
        Detect file format from extension.

        Returns:
            (format_type, format_name) — e.g., ("vector", "Shapefile")
        """
        ext = Path(path).suffix.lower()

        if ext in VECTOR_EXTENSIONS:
            return ("vector", VECTOR_EXTENSIONS[ext])
        elif ext in RASTER_EXTENSIONS:
            return ("raster", RASTER_EXTENSIONS[ext])
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    def read_metadata(self, path: str) -> FileMetadata:
        """Read file metadata without loading full data."""
        format_type, format_name = self.detect_format(path)
        file_size_mb = os.path.getsize(path) / (1024 * 1024)

        if format_type == "vector":
            return self._read_vector_metadata(path, format_name, file_size_mb)
        else:
            return self._read_raster_metadata(path, format_name, file_size_mb)

    def _read_vector_metadata(
        self, path: str, format_name: str, file_size_mb: float
    ) -> FileMetadata:
        """Read vector file metadata using Fiona."""
        try:
            import fiona

            with fiona.open(path) as src:
                crs = str(src.crs) if src.crs else None
                bounds = src.bounds  # (minx, miny, maxx, maxy)
                feature_count = len(src)
                fields = [
                    {"name": name, "type": str(ftype)}
                    for name, ftype in src.schema["properties"].items()
                ]

                return FileMetadata(
                    path=path,
                    name=Path(path).name,
                    format_type="vector",
                    format_name=format_name,
                    file_size_mb=file_size_mb,
                    crs=crs,
                    bounds=bounds,
                    feature_count=feature_count,
                    fields=fields,
                    band_count=None,
                    resolution=None,
                )
        except ImportError:
            # Fallback without fiona
            return FileMetadata(
                path=path,
                name=Path(path).name,
                format_type="vector",
                format_name=format_name,
                file_size_mb=file_size_mb,
                crs=None,
                bounds=None,
                feature_count=None,
                fields=None,
                band_count=None,
                resolution=None,
            )

    def _read_raster_metadata(
        self, path: str, format_name: str, file_size_mb: float
    ) -> FileMetadata:
        """Read raster file metadata using Rasterio."""
        try:
            import rasterio

            with rasterio.open(path) as src:
                crs = str(src.crs) if src.crs else None
                bounds = src.bounds  # BoundingBox
                return FileMetadata(
                    path=path,
                    name=Path(path).name,
                    format_type="raster",
                    format_name=format_name,
                    file_size_mb=file_size_mb,
                    crs=crs,
                    bounds=(bounds.left, bounds.bottom, bounds.right, bounds.top),
                    feature_count=None,
                    fields=None,
                    band_count=src.count,
                    resolution=(src.res[0], src.res[1]),
                )
        except ImportError:
            return FileMetadata(
                path=path,
                name=Path(path).name,
                format_type="raster",
                format_name=format_name,
                file_size_mb=file_size_mb,
                crs=None,
                bounds=None,
                feature_count=None,
                fields=None,
                band_count=None,
                resolution=None,
            )

    async def load(self, path: str) -> LoadResult:
        """
        Load a GIS file with automatic strategy selection.

        Strategy:
        - < 10 MB vector: direct GeoJSON transfer
        - >= 10 MB vector: generate vector tiles
        - Raster: generate raster tiles
        """
        metadata = self.read_metadata(path)

        if metadata.format_type == "vector" and metadata.file_size_mb < 10:
            return await self._load_direct_geojson(path, metadata)
        elif metadata.format_type == "vector":
            # TODO: Implement vector tile generation
            return await self._load_direct_geojson(path, metadata)
        else:
            # TODO: Implement raster tile generation
            return LoadResult(
                type="raster_tiles",
                metadata=metadata,
                tile_url=None,
            )

    async def _load_direct_geojson(self, path: str, metadata: FileMetadata) -> LoadResult:
        """Load vector data and convert to GeoJSON for direct transfer."""
        try:
            import geopandas as gpd

            gdf = gpd.read_file(path)
            geojson = gdf.to_json()

            return LoadResult(
                type="geojson",
                data=geojson,
                metadata=metadata,
            )
        except ImportError:
            return LoadResult(
                type="geojson",
                data=None,
                metadata=metadata,
            )


# Singleton
gis_loader = GISLoader()
