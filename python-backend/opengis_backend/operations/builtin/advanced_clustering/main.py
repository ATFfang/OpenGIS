#!/usr/bin/env python3
"""
高级空间聚类分析 Operation
===========================
支持点、线、面多种要素类型的高级空间聚类分析。

功能特点：
- 支持点（Point）、线（LineString）、面（Polygon）要素
- 5种聚类方法：DBSCAN、K-Means、HDBSCAN、OPTICS、层次聚类
- 多源数据输入：GeoJSON、SHP、CSV、GPKG、KML
- 自动处理坐标系统
- 输出聚类标签、聚类中心、统计信息

标准 --input/--output JSON 协议接口。
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

# ── 常量 ──
EARTH_RADIUS = 6_371_000  # 地球平均半径，单位：米


def load_data(input_path, coord_x=None, coord_y=None, coord_crs='EPSG:4326'):
    """
    加载多格式数据
    
    支持格式：GeoJSON, SHP, CSV, GPKG, KML
    """
    ext = Path(input_path).suffix.lower()
    
    if ext == '.csv':
        if not coord_x or not coord_y:
            raise ValueError("CSV 格式需要指定 coord_x 和 coord_y 参数")
        df = pd.read_csv(input_path)
        gdf = gpd.GeoDataFrame(
            df, 
            geometry=gpd.points_from_xy(df[coord_x], df[coord_y]),
            crs=coord_crs
        )
        return gdf
    elif ext in ['.shp', '.gpkg', '.kml', '.kmz']:
        return gpd.read_file(input_path)
    elif ext in ['.geojson', '.json']:
        return gpd.read_file(input_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def extract_points(gdf, centroid_method='centroid'):
    """
    从任意几何类型提取点坐标
    
    参数：
    - centroid_method: 质心计算方法，'centroid'（质心）或 'representative'（代表点）
    
    返回：(点坐标数组, 几何类型标识)
    """
    geom_types = set(gdf.geometry.geom_type)
    
    if all(t in ['Point', 'MultiPoint'] for t in geom_types):
        # 点要素，直接使用
        gdf = gdf.explode(index_parts=False) if 'MultiPoint' in geom_types else gdf
        gdf = gdf[gdf.geometry.geom_type == 'Point'].copy()
        coords = np.array([(p.x, p.y) for p in gdf.geometry])
        return gdf, coords, 'point'
    
    elif all(t in ['LineString', 'MultiLineString'] for t in geom_types):
        # 线要素，提取质心或端点
        gdf = gdf.explode(index_parts=False)
        if centroid_method == 'centroid':
            centroids = gdf.geometry.centroid
        else:
            # 取线的中点作为代表点
            centroids = gdf.geometry.interpolate(0.5, normalized=True)
        
        # 创建新的点GeoDataFrame用于聚类
        points_gdf = gdf.copy()
        points_gdf['original_geometry'] = gdf.geometry
        points_gdf.geometry = centroids
        coords = np.array([(p.x, p.y) for p in points_gdf.geometry])
        return points_gdf, coords, 'line'
    
    elif all(t in ['Polygon', 'MultiPolygon'] for t in geom_types):
        # 面要素，提取质心或代表点
        gdf = gdf.explode(index_parts=False)
        if centroid_method == 'centroid':
            centroids = gdf.geometry.centroid
        else:
            # 使用representative_point确保点在多边形内部
            centroids = gdf.geometry.representative_point()
        
        points_gdf = gdf.copy()
        points_gdf['original_geometry'] = gdf.geometry
        points_gdf.geometry = centroids
        coords = np.array([(p.x, p.y) for p in points_gdf.geometry])
        return points_gdf, coords, 'polygon'
    
    else:
        # 混合几何类型，取所有要素的质心
        centroids = gdf.geometry.centroid
        points_gdf = gdf.copy()
        points_gdf['original_geometry'] = gdf.geometry
        points_gdf.geometry = centroids
        coords = np.array([(p.x, p.y) for p in points_gdf.geometry])
        return points_gdf, coords, 'mixed'


def perform_clustering(coords, method='dbscan', **kwargs):
    """
    执行空间聚类
    
    支持方法：dbscan, kmeans, hdbscan, optics, agglomerative
    """
    coords_rad = np.radians(coords)
    
    if method == 'dbscan':
        from sklearn.cluster import DBSCAN
        eps_meters = kwargs.get('eps_meters', 500)
        min_samples = kwargs.get('min_samples', 5)
        eps_rad = eps_meters / EARTH_RADIUS
        
        model = DBSCAN(
            eps=eps_rad, 
            min_samples=min_samples, 
            metric='haversine', 
            algorithm='ball_tree'
        )
        labels = model.fit_predict(coords_rad)
        
    elif method == 'kmeans':
        from sklearn.cluster import KMeans
        n_clusters = kwargs.get('n_clusters', 5)
        model = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = model.fit_predict(coords)
        
    elif method == 'hdbscan':
        try:
            from hdbscan import HDBSCAN
            min_cluster_size = kwargs.get('min_cluster_size', 15)
            min_samples = kwargs.get('min_samples', 5)
            model = HDBSCAN(
                min_cluster_size=min_cluster_size,
                min_samples=min_samples
            )
            labels = model.fit_predict(coords)
        except ImportError:
            raise ImportError("HDBSCAN 需要安装: pip install hdbscan")
    
    elif method == 'optics':
        from sklearn.cluster import OPTICS
        min_samples = kwargs.get('min_samples', 5)
        max_eps_meters = kwargs.get('max_eps_meters', 1000)
        max_eps_rad = max_eps_meters / EARTH_RADIUS
        
        model = OPTICS(
            min_samples=min_samples,
            max_eps=max_eps_rad,
            metric='haversine'
        )
        labels = model.fit_predict(coords_rad)
        
    elif method == 'agglomerative':
        from sklearn.cluster import AgglomerativeClustering
        n_clusters = kwargs.get('n_clusters', 5)
        distance_threshold_meters = kwargs.get('distance_threshold_meters', None)
        
        if distance_threshold_meters:
            # 基于距离阈值的层次聚类
            model = AgglomerativeClustering(
                distance_threshold=distance_threshold_meters,
                n_clusters=None,
                metric='haversine',
                linkage='average'
            )
            labels = model.fit_predict(coords_rad)
        else:
            # 固定聚类数量
            model = AgglomerativeClustering(n_clusters=n_clusters)
            labels = model.fit_predict(coords)
    
    else:
        raise ValueError(f"不支持的聚类方法: {method}")
    
    return labels


def main():
    # ── 读取输入 ──
    with open(sys.argv[2], 'r', encoding='utf-8') as f:
        wrapper = json.load(f)
    
    params = wrapper.get('params', wrapper)
    workspace = wrapper.get('workspace', os.getcwd())
    
    input_path = params['input_path']
    if not os.path.isabs(input_path):
        input_path = os.path.join(workspace, input_path)
    
    # ── 参数设置 ──
    output_dir = params.get('output_dir') or os.path.dirname(input_path)
    method = params.get('method', 'dbscan').lower()
    prefix = params.get('prefix', '')
    suffix = params.get('suffix', '')
    centroid_method = params.get('centroid_method', 'centroid')
    
    # CSV 专用参数
    coord_x = params.get('coord_x')
    coord_y = params.get('coord_y')
    coord_crs = params.get('coord_crs', 'EPSG:4326')
    
    # DBSCAN 参数
    eps_meters = params.get('eps_meters', 500)
    min_samples = params.get('min_samples', 5)
    
    # KMeans/Agglomerative 参数
    n_clusters = params.get('n_clusters', 5)
    distance_threshold_meters = params.get('distance_threshold_meters', None)
    
    # HDBSCAN 参数
    min_cluster_size = params.get('min_cluster_size', 15)
    
    # OPTICS 参数
    max_eps_meters = params.get('max_eps_meters', 1000)
    
    # 输出控制
    save_centers = params.get('save_centers', True)
    save_stats = params.get('save_stats', True)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # ── 加载数据 ──
    try:
        gdf = load_data(input_path, coord_x, coord_y, coord_crs)
    except Exception as e:
        output = {'success': False, 'error': f'数据加载失败: {str(e)}'}
        with open(sys.argv[4], 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)
        return
    
    # ── 提取点坐标 ──
    try:
        points_gdf, coords, geom_type = extract_points(gdf, centroid_method)
    except Exception as e:
        output = {'success': False, 'error': f'几何处理失败: {str(e)}'}
        with open(sys.argv[4], 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)
        return
    
    if len(coords) == 0:
        output = {'success': False, 'error': '文件中没有有效要素'}
        with open(sys.argv[4], 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)
        return
    
    # ── 执行聚类 ──
    try:
        labels = perform_clustering(
            coords, 
            method=method,
            eps_meters=eps_meters,
            min_samples=min_samples,
            n_clusters=n_clusters,
            min_cluster_size=min_cluster_size,
            max_eps_meters=max_eps_meters,
            distance_threshold_meters=distance_threshold_meters
        )
    except Exception as e:
        output = {'success': False, 'error': f'聚类失败: {str(e)}'}
        with open(sys.argv[4], 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)
        return
    
    points_gdf['cluster'] = labels.astype(int)
    
    n_clusters_found = int(len(set(labels)) - (1 if -1 in labels else 0))
    n_noise = int((labels == -1).sum())
    n_clustered = int((labels != -1).sum())
    
    # ── 计算聚类统计 ──
    cluster_stats = []
    centers_features = []
    
    for cid in range(n_clusters_found):
        sub = points_gdf[points_gdf['cluster'] == cid]
        cx, cy = float(sub.geometry.x.mean()), float(sub.geometry.y.mean())
        cnt = len(sub)
        
        # 计算聚类的空间范围（米）
        lng_min, lng_max = sub.geometry.x.min(), sub.geometry.x.max()
        lat_min, lat_max = sub.geometry.y.min(), sub.geometry.y.max()
        lat_mid = (lat_min + lat_max) / 2
        extent_lng_m = (lng_max - lng_min) * 111320 * np.cos(np.radians(lat_mid))
        extent_lat_m = (lat_max - lat_min) * 111320
        extent_m = max(extent_lng_m, extent_lat_m)
        
        stat = {
            'cluster_id': cid,
            'count': cnt,
            'center_lng': round(cx, 6),
            'center_lat': round(cy, 6),
            'extent_m': round(extent_m, 1),
        }
        cluster_stats.append(stat)
        
        centers_features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [cx, cy]},
            'properties': {
                'cluster_id': cid,
                'count': cnt,
                'extent_m': round(extent_m, 1),
            }
        })
    
    cluster_stats.sort(key=lambda x: -x['count'])
    
    # ── 保存结果 ──
    base = Path(input_path).stem
    results = {}
    
    # 聚类后的数据（恢复原始几何或保持点）
    if 'original_geometry' in points_gdf.columns:
        # 如果原始几何是线/面，恢复原始几何
        points_gdf = points_gdf.drop(columns=['geometry'])
        points_gdf = points_gdf.rename(columns={'original_geometry': 'geometry'})
        points_gdf = gpd.GeoDataFrame(points_gdf, geometry='geometry')
    
    output_path = os.path.join(output_dir, f"{prefix}{base}{suffix}_clustered.geojson")
    points_gdf.to_file(output_path, driver='GeoJSON', encoding='utf-8')
    results['clustered_path'] = output_path
    
    # 中心点
    if save_centers and centers_features:
        centers_path = os.path.join(output_dir, f"{prefix}{base}{suffix}_centers.geojson")
        with open(centers_path, 'w', encoding='utf-8') as f:
            json.dump({'type': 'FeatureCollection', 'features': centers_features}, f, ensure_ascii=False)
        results['centers_path'] = centers_path
    
    # 统计
    summary = {
        'input_file': input_path,
        'method': method.upper(),
        'geometry_type': geom_type,
        'parameters': {},
        'total_features': len(points_gdf),
        'n_clusters': n_clusters_found,
        'n_noise': n_noise,
        'n_clustered': n_clustered,
        'clustering_ratio': round(n_clustered / len(points_gdf), 4) if len(points_gdf) > 0 else 0,
        'clusters': cluster_stats,
    }
    
    # 记录使用的参数
    if method == 'dbscan':
        summary['parameters'] = {'eps_meters': eps_meters, 'min_samples': min_samples}
    elif method in ['kmeans', 'agglomerative']:
        summary['parameters'] = {'n_clusters': n_clusters}
        if distance_threshold_meters:
            summary['parameters']['distance_threshold_meters'] = distance_threshold_meters
    elif method == 'hdbscan':
        summary['parameters'] = {'min_cluster_size': min_cluster_size, 'min_samples': min_samples}
    elif method == 'optics':
        summary['parameters'] = {'min_samples': min_samples, 'max_eps_meters': max_eps_meters}
    
    if save_stats:
        stats_path = os.path.join(output_dir, f"{prefix}{base}{suffix}_stats.json")
        with open(stats_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        results['stats_path'] = stats_path
    
    # ── 输出 ──
    output = {
        'success': True,
        'n_features': len(points_gdf),
        'geometry_type': geom_type,
        'method': method.upper(),
        'n_clusters': n_clusters_found,
        'n_noise': n_noise,
        'n_clustered': n_clustered,
        'clustering_ratio': round(n_clustered / len(points_gdf), 4) if len(points_gdf) > 0 else 0,
        'top_clusters': cluster_stats[:10],
        **results,
    }
    with open(sys.argv[4], 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
