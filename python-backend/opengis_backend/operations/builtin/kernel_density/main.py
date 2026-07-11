#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核密度估计 Operation
对点/线/面要素生成核密度估计热力图。
支持多种核函数、自动带宽选择、权重字段、输出栅格/等值线/等值面。
"""

import sys
import os
import json
import argparse
import warnings
import traceback
import tempfile

import numpy as np
import geopandas as gpd
from scipy import ndimage
from shapely.geometry import Point, LineString, Polygon, MultiPolygon, mapping
from shapely.ops import unary_union

warnings.filterwarnings("ignore")


# ============ 多源数据加载 ============

def load_spatial_data(input_path):
    """加载多种格式的空间数据，自动识别格式"""
    ext = os.path.splitext(input_path)[1].lower()
    
    if ext in (".geojson", ".json"):
        gdf = gpd.read_file(input_path, engine="pyogrio")
    elif ext in (".shp",):
        gdf = gpd.read_file(input_path, engine="pyogrio")
    elif ext == ".gpkg":
        gdf = gpd.read_file(input_path, engine="pyogrio")
    elif ext == ".kml":
        gdf = gpd.read_file(input_path, driver="KML", engine="pyogrio")
    elif ext in (".csv", ".tsv"):
        import pandas as pd
        sep = "\t" if ext == ".tsv" else ","
        df = pd.read_csv(input_path, sep=sep, encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
        
        lat_patterns = ["lat", "latitude", "y", "纬度", "wgs_lat", "gcj_lat", "bd_lat"]
        lon_patterns = ["lon", "longitude", "lng", "long", "x", "经度", "wgs_lng", "gcj_lng", "bd_lng"]
        lat_col = next((c for c in df.columns if c.lower() in lat_patterns or c.lower().endswith("_lat")), None)
        lon_col = next((c for c in df.columns if c.lower() in lon_patterns or c.lower().endswith("_lng") or c.lower().endswith("_lon")), None)
        
        if not lat_col or not lon_col:
            raise ValueError(f"CSV 缺少经纬度列。可用列: {list(df.columns)}")
        
        df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
        df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
        df.dropna(subset=[lat_col, lon_col], inplace=True)
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs="EPSG:4326")
    elif ext in (".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_excel(input_path)
        df.columns = df.columns.str.strip()
        
        lat_patterns = ["lat", "latitude", "y", "纬度", "wgs_lat", "gcj_lat", "bd_lat"]
        lon_patterns = ["lon", "longitude", "lng", "long", "x", "经度", "wgs_lng", "gcj_lng", "bd_lng"]
        lat_col = next((c for c in df.columns if c.lower() in lat_patterns or c.lower().endswith("_lat")), None)
        lon_col = next((c for c in df.columns if c.lower() in lon_patterns or c.lower().endswith("_lng") or c.lower().endswith("_lon")), None)
        
        if not lat_col or not lon_col:
            raise ValueError(f"Excel 缺少经纬度列。可用列: {list(df.columns)}")
        
        df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
        df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
        df.dropna(subset=[lat_col, lon_col], inplace=True)
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs="EPSG:4326")
    elif ext == ".fgb":
        gdf = gpd.read_file(input_path, engine="pyogrio")
    else:
        gdf = gpd.read_file(input_path, engine="pyogrio")
    
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    
    if gdf.empty:
        raise ValueError("输入数据为空")
    
    return gdf


def compute_bandwidth_auto(gdf):
    """Scott's rule 自动计算带宽"""
    try:
        gdf_proj = gdf.to_crs(epsg=3857)
        coords = np.column_stack([
            gdf_proj.geometry.centroid.x,
            gdf_proj.geometry.centroid.y
        ])
        n = len(coords)
        std = np.std(coords, axis=0).mean()
        bandwidth = n ** (-1.0 / 6.0) * std * 1.5
        return max(bandwidth, 100)
    except Exception:
        return 1000


def get_bandwidth(params, gdf):
    """获取带宽参数"""
    val = params.get("bandwidth_meters")
    if val is not None:
        return float(val)
    val = params.get("bandwidth")
    if val is not None:
        return float(val)
    return compute_bandwidth_auto(gdf)


def get_cell_size(params, bandwidth):
    """获取网格大小参数"""
    val = params.get("cell_size_meters")
    if val is not None:
        return float(val)
    val = params.get("cell_size")
    if val is not None:
        return float(val)
    return bandwidth / 4.0


def extract_points(gdf):
    """从任意几何类型提取点坐标（重心或端点）"""
    points = []
    weights = []
    
    for idx, row in gdf.iterrows():
        geom = row.geometry
        w = row.get("_weight", 1.0)
        
        if geom is None or geom.is_empty:
            continue
        
        if geom.geom_type in ("Point", "MultiPoint"):
            if geom.geom_type == "Point":
                points.append((geom.x, geom.y))
                weights.append(w)
            else:
                for p in geom.geoms:
                    points.append((p.x, p.y))
                    weights.append(w)
        
        elif geom.geom_type in ("LineString", "MultiLineString"):
            if geom.geom_type == "LineString":
                coords = list(geom.coords)
            else:
                coords = []
                for line in geom.geoms:
                    coords.extend(list(line.coords))
            from shapely.geometry import LineString as LS
            if len(coords) >= 2:
                line = LS(coords)
                total_len = line.length
                n_samples = max(int(total_len / 50), 5)
                for i in range(n_samples + 1):
                    frac = i / n_samples
                    pt = line.interpolate(fraction=frac, normalized=True)
                    points.append((pt.x, pt.y))
                    weights.append(w)
        
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            if geom.geom_type == "Polygon":
                polygons = [geom]
            else:
                polygons = list(geom.geoms)
            
            for poly in polygons:
                minx, miny, maxx, maxy = poly.bounds
                dx = (maxx - minx) / 20
                dy = (maxy - miny) / 20
                
                xs = np.arange(minx, maxx, dx)
                ys = np.arange(miny, maxy, dy)
                
                count = 0
                for x in xs:
                    for y in ys:
                        pt = Point(x, y)
                        if poly.contains(pt) or poly.buffer(dx * 0.1).contains(pt):
                            points.append((x, y))
                            weights.append(w)
                            count += 1
                
                if count == 0:
                    centroid = poly.centroid
                    points.append((centroid.x, centroid.y))
                    weights.append(w)
    
    return np.array(points), np.array(weights)


# ============ 核函数 ============

def kernel_gaussian(u):
    return (1 / (2 * np.pi)) * np.exp(-0.5 * u ** 2)

def kernel_epanechnikov(u):
    return np.where(np.abs(u) <= 1, 0.75 * (1 - u ** 2), 0)

def kernel_uniform(u):
    return np.where(np.abs(u) <= 1, 0.5, 0)

def kernel_triangular(u):
    return np.where(np.abs(u) <= 1, 1 - np.abs(u), 0)

def kernel_quartic(u):
    return np.where(np.abs(u) <= 1, (15 / 16) * (1 - u ** 2) ** 2, 0)

def kernel_cosine(u):
    return np.where(np.abs(u) <= 1, (np.pi / 4) * np.cos(np.pi / 2 * u), 0)

KERNELS = {
    "gaussian": kernel_gaussian,
    "epanechnikov": kernel_epanechnikov,
    "uniform": kernel_uniform,
    "triangular": kernel_triangular,
    "quartic": kernel_quartic,
    "cosine": kernel_cosine,
}


def compute_kde(points, weights, bandwidth, cell_size, extent=None, kernel_func=kernel_gaussian, max_grid_cells=1000000):
    """计算核密度估计"""
    if len(points) == 0:
        raise ValueError("没有有效的点数据")
    
    xs = points[:, 0]
    ys = points[:, 1]
    
    if extent is None:
        margin = bandwidth * 2
        xmin, xmax = xs.min() - margin, xs.max() + margin
        ymin, ymax = ys.min() - margin, ys.max() + margin
    else:
        xmin, ymin, xmax, ymax = extent
    
    nx = max(int((xmax - xmin) / cell_size), 1)
    ny = max(int((ymax - ymin) / cell_size), 1)
    
    total_cells = nx * ny
    if total_cells > max_grid_cells:
        scale = (total_cells / max_grid_cells) ** 0.5
        cell_size = cell_size * scale
        nx = max(int((xmax - xmin) / cell_size), 1)
        ny = max(int((ymax - ymin) / cell_size), 1)
    
    xi = np.linspace(xmin, xmax, nx)
    yi = np.linspace(ymin, ymax, ny)
    grid_x, grid_y = np.meshgrid(xi, yi)
    
    density = np.zeros_like(grid_x, dtype=np.float64)
    
    for i in range(len(points)):
        px, py = points[i]
        w = weights[i] if i < len(weights) else 1.0
        
        dist = np.sqrt((grid_x - px) ** 2 + (grid_y - py) ** 2)
        u = dist / bandwidth
        k = kernel_func(u)
        density += w * k / (bandwidth ** 2)
    
    density = density / weights.sum()
    
    return density, xi, yi, (xmin, ymin, xmax, ymax)


def classify_values(values, n_classes=10, method="quantile"):
    """对值进行分级"""
    values = np.asarray(values, dtype=np.float64)
    valid = values[np.isfinite(values)]
    
    if len(valid) == 0:
        return np.linspace(0, 1, n_classes + 1)
    
    if method == "quantile":
        quantiles = np.linspace(0, 100, n_classes + 1)
        breaks = np.percentile(valid, quantiles)
    elif method == "equal_interval":
        breaks = np.linspace(valid.min(), valid.max(), n_classes + 1)
    elif method == "jenks":
        breaks = np.percentile(valid, np.linspace(0, 100, n_classes + 1))
    elif method == "std":
        mean = valid.mean()
        std = valid.std()
        breaks = np.linspace(mean - 2 * std, mean + 2 * std, n_classes + 1)
    else:
        breaks = np.linspace(valid.min(), valid.max(), n_classes + 1)
    
    breaks[0] = valid.min()
    breaks[-1] = valid.max()
    
    return np.unique(breaks)


def density_to_polygons(density, xi, yi, extent, n_classes=10, classify_method="quantile"):
    """将密度栅格转换为等值面"""
    from matplotlib.figure import Figure
    
    xmin, ymin, xmax, ymax = extent
    fig = Figure()
    ax = fig.add_subplot(111)
    
    levels = classify_values(density, n_classes, classify_method)
    
    contourf = ax.contourf(xi, yi, density, levels=levels)
    
    polygons = []
    for i, collection in enumerate(contourf.collections):
        for path in collection.get_paths():
            vertices = path.vertices
            if len(vertices) >= 3:
                poly = Polygon(vertices)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_valid and not poly.is_empty:
                    low = float(levels[i])
                    high = float(levels[i + 1])
                    polygons.append({
                        "geometry": poly,
                        "density_low": low,
                        "density_high": high,
                        "density_mean": (low + high) / 2,
                        "class_id": i
                    })
    
    import matplotlib.pyplot as plt
    plt.close(fig)
    
    return polygons


def density_to_contours(density, xi, yi, extent, n_contours=10, classify_method="quantile"):
    """将密度栅格转换为等值线"""
    from matplotlib.figure import Figure
    
    fig = Figure()
    ax = fig.add_subplot(111)
    
    levels = classify_values(density, n_contours, classify_method)
    
    contour = ax.contour(xi, yi, density, levels=levels)
    
    lines = []
    for i, collection in enumerate(contour.collections):
        for path in collection.get_paths():
            vertices = path.vertices
            if len(vertices) >= 2:
                line = LineString(vertices)
                if not line.is_empty:
                    lines.append({
                        "geometry": line,
                        "density_value": float(levels[i]),
                        "class_id": i
                    })
    
    import matplotlib.pyplot as plt
    plt.close(fig)
    
    return lines


def write_geotiff(density, xi, yi, extent, output_path, crs="EPSG:3857"):
    """写入 GeoTIFF"""
    import rasterio
    from rasterio.transform import from_bounds
    
    xmin, ymin, xmax, ymax = extent
    transform = from_bounds(xmin, ymin, xmax, ymax, len(xi), len(yi))
    
    density_t = density.astype(np.float32)
    
    with rasterio.open(
        output_path, "w",
        driver="GTiff",
        height=density_t.shape[0],
        width=density_t.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=0,
        compress="deflate"
    ) as dst:
        dst.write(density_t, 1)


def write_geojson(features, output_path, src_crs="EPSG:3857", dst_crs=None):
    """写入 GeoJSON"""
    if not features:
        return None
    
    geometries = [f["geometry"] for f in features]
    attrs = {k: [f.get(k) for f in features] for k in features[0].keys() if k != "geometry"}
    
    gdf = gpd.GeoDataFrame(attrs, geometry=geometries, crs=src_crs)
    
    if dst_crs:
        gdf = gdf.to_crs(dst_crs)
    
    gdf.to_file(output_path, driver="GeoJSON", engine="pyogrio")
    return output_path


# ============ 主逻辑 ============

def run_kernel_density(workspace, params):
    try:
        input_path = params.get("input_path")
        if not input_path:
            return {"success": False, "error": "缺少 input_path 参数"}
        
        if not os.path.isabs(input_path):
            input_path = os.path.join(workspace, input_path)
        
        if not os.path.exists(input_path):
            return {"success": False, "error": f"输入文件不存在: {input_path}"}
        
        output_dir = params.get("output_dir", "kde_output")
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(workspace, output_dir)
        os.makedirs(output_dir, exist_ok=True)
        
        output_raster = params.get("output_raster", True)
        output_contours = params.get("output_contours", False)
        output_polygons = params.get("output_polygons", False)
        n_contours = params.get("n_contours", 10)
        kernel_name = params.get("kernel", "gaussian")
        classify_method = params.get("classify_method", "quantile")
        weight_field = params.get("weight_field")
        normalize = params.get("normalize", False)
        target_crs = params.get("crs")
        max_grid_cells = params.get("max_grid_cells", 1000000)
        
        if kernel_name not in KERNELS:
            return {"success": False, "error": f"未知核函数: {kernel_name}，可选: {list(KERNELS.keys())}"}
        
        kernel_func = KERNELS[kernel_name]
        
        gdf = load_spatial_data(input_path)
        n_features = len(gdf)
        
        gdf["_weight"] = 1.0
        if weight_field and weight_field in gdf.columns:
            gdf["_weight"] = gdf[weight_field].fillna(1.0)
        
        points, weights = extract_points(gdf)
        
        if len(points) == 0:
            return {"success": False, "error": "无法从输入数据中提取有效点坐标"}
        
        gdf_proj = gpd.GeoDataFrame(
            {"geometry": [Point(p) for p in points], "_weight": weights},
            crs=gdf.crs
        ).to_crs(epsg=3857)
        proj_points = np.column_stack([
            gdf_proj.geometry.x,
            gdf_proj.geometry.y
        ])
        
        bandwidth = get_bandwidth(params, gdf)
        cell_size = get_cell_size(params, bandwidth)
        
        density, xi, yi, extent = compute_kde(
            proj_points, weights, bandwidth, cell_size,
            kernel_func=kernel_func,
            max_grid_cells=max_grid_cells
        )
        
        if normalize and density.max() > 0:
            density = density / density.max()
        
        stats = {
            "min": float(density.min()),
            "max": float(density.max()),
            "mean": float(density.mean()),
            "std": float(density.std()),
            "nonzero_cells": int((density > 0).sum()),
            "total_cells": density.size
        }
        
        output_files = []
        raster_path = None
        polygons_path = None
        contours_path = None
        
        if output_raster:
            raster_path = os.path.join(output_dir, "kde_raster.tif")
            try:
                write_geotiff(density, xi, yi, extent, raster_path, crs="EPSG:3857")
                output_files.append(raster_path)
            except Exception as e:
                return {"success": False, "error": f"写入 GeoTIFF 失败: {str(e)}"}
        
        if output_polygons:
            polygons_path = os.path.join(output_dir, "kde_polygons.geojson")
            try:
                poly_features = density_to_polygons(
                    density, xi, yi, extent, n_classes=n_contours, classify_method=classify_method
                )
                if poly_features:
                    write_geojson(poly_features, polygons_path, src_crs="EPSG:3857", dst_crs=target_crs)
                    output_files.append(polygons_path)
            except Exception as e:
                polygons_path = None
        
        if output_contours:
            contours_path = os.path.join(output_dir, "kde_contours.geojson")
            try:
                contour_features = density_to_contours(
                    density, xi, yi, extent, n_contours=n_contours, classify_method=classify_method
                )
                if contour_features:
                    write_geojson(contour_features, contours_path, src_crs="EPSG:3857", dst_crs=target_crs)
                    output_files.append(contours_path)
            except Exception as e:
                contours_path = None
        
        return {
            "success": True,
            "n_features": n_features,
            "bandwidth": bandwidth,
            "cell_size": cell_size,
            "kernel": kernel_name,
            "grid_size": [len(xi), len(yi)],
            "density_range": [float(density.min()), float(density.max())],
            "raster_path": raster_path,
            "polygons_path": polygons_path,
            "contours_path": contours_path,
            "statistics": stats,
            "output_files": output_files,
            "n_points_sampled": len(points)
        }
    
    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


def main():
    parser = argparse.ArgumentParser(description="核密度估计 Operation")
    parser.add_argument("--input", required=True, help="输入 JSON 参数文件路径")
    parser.add_argument("--output", required=True, help="输出 JSON 结果文件路径")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        wrapper = json.load(f)

    workspace = wrapper.get("workspace", ".")
    params = wrapper.get("params", {})
    result = run_kernel_density(workspace, params)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
