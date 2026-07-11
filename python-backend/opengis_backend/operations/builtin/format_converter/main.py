#!/usr/bin/env python3
"""
format_converter - 多格式地理要素数据转换工具

支持 GeoJSON, Shapefile, CSV, Excel, GeoPackage, KML, GeoJSONSeq, FlatGeobuf 等格式互转。
CSV/Excel 输入时自动识别坐标列，支持点、线、面要素转换。
"""

import sys
import os
import json
import time
import argparse
import subprocess
from pathlib import Path

# ── 自动安装缺失依赖 ──────────────────────────────────────────────
def _ensure_deps():
    required = {
        'geopandas': 'geopandas',
        'pandas': 'pandas',
        'shapely': 'shapely',
        'fiona': 'fiona',
        'pyproj': 'pyproj',
    }
    optional = {
        'openpyxl': 'openpyxl',      # Excel 读写
        'chardet': 'chardet',        # 编码检测
        'geopy': 'geopy',            # 地理编码（可选）
        'lxml': 'lxml',              # KML / HTML 支持（可选）
    }
    missing_required = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing_required.append(pkg)
    if missing_required:
        print(f"[format_converter] 安装必需依赖: {', '.join(missing_required)}", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
             '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple',
             '--trusted-host', 'pypi.tuna.tsinghua.edu.cn'] + missing_required,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    # optional deps: install individually, never crash on failure
    for mod, pkg in optional.items():
        try:
            __import__(mod)
        except ImportError:
            try:
                print(f"[format_converter] 安装可选依赖: {pkg}", file=sys.stderr)
                subprocess.check_call(
                    [sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                     '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple',
                     '--trusted-host', 'pypi.tuna.tsinghua.edu.cn', pkg],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
            except Exception as e:
                print(f"[format_converter] 可选依赖 {pkg} 安装失败（不影响核心功能）: {e}", file=sys.stderr)

_ensure_deps()


def detect_encoding(file_path):
    """检测文件编码"""
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw = f.read(min(1024 * 100, os.path.getsize(file_path)))
        result = chardet.detect(raw)
        encoding = result.get('encoding', 'utf-8')
        if encoding and encoding.lower() in ['gb2312', 'gbk', 'gb18030']:
            return 'gb18030'
        return encoding or 'utf-8'
    except ImportError:
        for enc in ['utf-8-sig', 'utf-8', 'gb18030', 'latin1']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    f.read(1024)
                return enc
            except (UnicodeDecodeError, UnicodeError):
                continue
        return 'latin1'


def detect_csv_delimiter(file_path, encoding='utf-8'):
    """自动检测 CSV 分隔符"""
    import csv as csv_mod
    with open(file_path, 'r', encoding=encoding) as f:
        sample = f.read(8192)
    try:
        dialect = csv_mod.Sniffer().sniff(sample, delimiters=',;\t|')
        return dialect.delimiter
    except csv_mod.Error:
        return ','


def detect_coord_columns(df):
    """自动检测坐标列"""
    x_candidates = ['longitude', 'lon', 'lng', 'x', 'long', '经度', 'lng_wgs84', 'lon_wgs84']
    y_candidates = ['latitude', 'lat', 'y', '纬度', 'lat_wgs84']
    
    cols_lower = {c.lower().strip(): c for c in df.columns}
    
    for candidate in x_candidates:
        if candidate in cols_lower:
            for y_candidate in y_candidates:
                if y_candidate in cols_lower:
                    return cols_lower[candidate], cols_lower[y_candidate]
    
    import pandas as pd
    numeric_cols = df.select_dtypes(include=[pd.np.number if hasattr(pd, 'np') else 'number']).columns.tolist()
    for i, col1 in enumerate(numeric_cols):
        for col2 in numeric_cols[i+1:]:
            vals1 = pd.to_numeric(df[col1], errors='coerce')
            vals2 = pd.to_numeric(df[col2], errors='coerce')
            valid = vals1.notna() & vals2.notna()
            if valid.sum() == 0:
                continue
            if (vals1[valid].between(-180, 180).all() and vals2[valid].between(-90, 90).all()):
                return col1, col2
            if (vals2[valid].between(-180, 180).all() and vals1[valid].between(-90, 90).all()):
                return col2, col1
    return None, None


def read_csv_file(path, coord_x=None, coord_y=None, crs='EPSG:4326', encoding=None, wkt_col=None):
    """读取 CSV 文件"""
    import pandas as pd
    import geopandas as gpd
    from shapely import wkt
    
    enc = encoding or detect_encoding(path)
    sep = detect_csv_delimiter(path, enc)
    df = pd.read_csv(path, encoding=enc, sep=sep, low_memory=False)
    
    if wkt_col and wkt_col in df.columns:
        geom_series = df[wkt_col].apply(wkt.loads)
        df = df.drop(columns=[wkt_col])
        gdf = gpd.GeoDataFrame(df, geometry=geom_series, crs=crs)
        return gdf
    
    if coord_x and coord_x in df.columns and coord_y and coord_y in df.columns:
        df[coord_x] = pd.to_numeric(df[coord_x], errors='coerce')
        df[coord_y] = pd.to_numeric(df[coord_y], errors='coerce')
        df = df.dropna(subset=[coord_x, coord_y])
        gdf = gpd.GeoDataFrame(
            df, 
            geometry=gpd.points_from_xy(df[coord_x], df[coord_y]),
            crs=crs
        )
        return gdf
    
    x_col, y_col = detect_coord_columns(df)
    if x_col and y_col:
        df[x_col] = pd.to_numeric(df[x_col], errors='coerce')
        df[y_col] = pd.to_numeric(df[y_col], errors='coerce')
        df = df.dropna(subset=[x_col, y_col])
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df[x_col], df[y_col]),
            crs=crs
        )
        return gdf
    
    raise ValueError(f"CSV 中未找到坐标列。请指定 coord_x 和 coord_y 参数，或确保数据中有经度/纬度列。")


def read_excel_file(path, coord_x=None, coord_y=None, crs='EPSG:4326', sheet_name=None, encoding=None, wkt_col=None):
    """读取 Excel 文件"""
    import pandas as pd
    import geopandas as gpd
    from shapely import wkt
    
    df = pd.read_excel(path, sheet_name=sheet_name or 0)
    
    if wkt_col and wkt_col in df.columns:
        geom_series = df[wkt_col].apply(wkt.loads)
        df = df.drop(columns=[wkt_col])
        gdf = gpd.GeoDataFrame(df, geometry=geom_series, crs=crs)
        return gdf
    
    if coord_x and coord_x in df.columns and coord_y and coord_y in df.columns:
        df[coord_x] = pd.to_numeric(df[coord_x], errors='coerce')
        df[coord_y] = pd.to_numeric(df[coord_y], errors='coerce')
        df = df.dropna(subset=[coord_x, coord_y])
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df[coord_x], df[coord_y]),
            crs=crs
        )
        return gdf
    
    x_col, y_col = detect_coord_columns(df)
    if x_col and y_col:
        df[x_col] = pd.to_numeric(df[x_col], errors='coerce')
        df[y_col] = pd.to_numeric(df[y_col], errors='coerce')
        df = df.dropna(subset=[x_col, y_col])
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df[x_col], df[y_col]),
            crs=crs
        )
        return gdf
    
    raise ValueError(f"Excel 中未找到坐标列。请指定 coord_x 和 coord_y 参数。")


def read_generic(path, coord_x=None, coord_y=None, crs='EPSG:4326', sheet_name=None, encoding=None, layer_name=None, wkt_col=None):
    """根据文件扩展名自动读取"""
    import geopandas as gpd
    
    ext = Path(path).suffix.lower()
    
    if ext in ['.geojson', '.json']:
        return gpd.read_file(path)
    elif ext in ['.shp']:
        return gpd.read_file(path)
    elif ext in ['.gpkg']:
        return gpd.read_file(path, layer=layer_name)
    elif ext in ['.kml']:
        return gpd.read_file(path, driver='KML')
    elif ext in ['.geojsonl', '.geojsonseq']:
        return gpd.read_file(path, driver='GeoJSONSeq')
    elif ext in ['.fgb']:
        return gpd.read_file(path, driver='FlatGeobuf')
    elif ext in ['.csv']:
        return read_csv_file(path, coord_x, coord_y, crs, encoding, wkt_col)
    elif ext in ['.xls', '.xlsx']:
        return read_excel_file(path, coord_x, coord_y, crs, sheet_name, encoding, wkt_col)
    elif ext in ['.gml']:
        return gpd.read_file(path)
    elif ext in ['.tab']:
        return gpd.read_file(path)
    else:
        try:
            return gpd.read_file(path)
        except Exception:
            raise ValueError(f"不支持的文件格式: {ext}")


def write_output(gdf, output_path, output_format='auto', include_geometry_csv=False, layer_name=None, encoding='utf-8'):
    """写出 GeoDataFrame 到指定格式"""
    import geopandas as gpd
    import pandas as pd
    
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    
    # 标准化格式别名
    format_aliases = {
        'shp': 'shapefile', 'esri': 'shapefile', 'esrishapefile': 'shapefile',
        'geojson': 'geojson', 'json': 'geojson',
        'csv': 'csv',
        'xls': 'excel', 'xlsx': 'excel', 'excel': 'excel',
        'gpkg': 'geopackage', 'geopackage': 'geopackage',
        'kml': 'kml',
        'geojsonl': 'geojsonseq', 'geojsonseq': 'geojsonseq', 'ndjson': 'geojsonseq',
        'fgb': 'flatgeobuf', 'flatgeobuf': 'flatgeobuf',
        'gml': 'gml',
        'tab': 'tab', 'mapinfo': 'tab',
        'geobuf': 'flatgeobuf',
    }
    output_format = format_aliases.get(output_format.lower(), output_format)
    
    if output_format == 'auto':
        ext = out.suffix.lower()
        fmt_map = {
            '.geojson': 'geojson', '.json': 'geojson',
            '.shp': 'shapefile',
            '.geojson': 'geojson', '.json': 'geojson',
            '.shp': 'shapefile',
            '.csv': 'csv',
            '.xls': 'excel', '.xlsx': 'excel',
            '.gpkg': 'geopackage',
            '.kml': 'kml',
            '.geojsonl': 'geojsonseq', '.geojsonseq': 'geojsonseq',
            '.fgb': 'flatgeobuf', '.geobuf': 'flatgeobuf',
            '.gml': 'gml',
            '.tab': 'tab',
        }
        output_format = fmt_map.get(ext, 'geojson')
    
    if output_format == 'geojson':
        gdf.to_file(output_path, driver='GeoJSON')
    elif output_format == 'shapefile':
        gdf.to_file(output_path, driver='ESRI Shapefile')
    elif output_format == 'csv':
        df = pd.DataFrame(gdf.drop(columns='geometry'))
        if include_geometry_csv and gdf.geometry.notna().any():
            df['geometry_wkt'] = gdf.geometry.to_wkt()
        df.to_csv(output_path, index=False, encoding=encoding)
    elif output_format == 'excel':
        df = pd.DataFrame(gdf.drop(columns='geometry'))
        if include_geometry_csv and gdf.geometry.notna().any():
            df['geometry_wkt'] = gdf.geometry.to_wkt()
        df.to_excel(output_path, index=False)
    elif output_format == 'geopackage':
        gdf.to_file(output_path, driver='GPKG', layer=layer_name or 'data')
    elif output_format == 'kml':
        gdf.to_file(output_path, driver='KML')
    elif output_format == 'geojsonseq':
        gdf.to_file(output_path, driver='GeoJSONSeq')
    elif output_format == 'flatgeobuf':
        gdf.to_file(output_path, driver='FlatGeobuf')
    elif output_format == 'gml':
        gdf.to_file(output_path, driver='GML')
    elif output_format == 'tab':
        gdf.to_file(output_path, driver='MapInfo File')
    else:
        raise ValueError(f"不支持的输出格式: {output_format}")
    
    return output_path


def convert(input_path, output_path=None, output_format='auto', coord_x=None, coord_y=None,
            coord_crs='EPSG:4326', target_crs=None, select_columns=None, rename_columns=None,
            include_geometry_csv=False, sheet_name=None, layer_name=None, encoding=None, wkt_col=None):
    """执行格式转换"""
    start = time.time()
    
    gdf = read_generic(input_path, coord_x, coord_y, coord_crs, sheet_name, encoding, layer_name, wkt_col)
    
    input_count = len(gdf)
    geom_types = []
    if gdf.geometry.notna().any():
        geom_types = [t for t in gdf.geometry.geom_type.unique() if t]
    input_crs = str(gdf.crs) if gdf.crs else '未知'
    
    if target_crs and gdf.crs:
        if str(gdf.crs) != target_crs:
            gdf = gdf.to_crs(target_crs)
    
    if select_columns:
        keep = [c for c in select_columns if c in gdf.columns]
        gdf = gdf[['geometry'] + keep]
    
    if rename_columns:
        gdf = gdf.rename(columns=rename_columns)
    
    if not output_path:
        in_p = Path(input_path)
        ext_map = {
            'geojson': '.geojson', 'shapefile': '.shp', 'csv': '.csv',
            'excel': '.xlsx', 'geopackage': '.gpkg', 'kml': '.kml',
            'geojsonseq': '.geojsonl', 'flatgeobuf': '.fgb', 'gml': '.gml',
        }
        fmt = output_format if output_format != 'auto' else 'geojson'
        ext = ext_map.get(fmt, '.geojson')
        output_path = str(in_p.parent / f"{in_p.stem}_converted{ext}")
    
    write_output(gdf, output_path, output_format, include_geometry_csv, layer_name, encoding or 'utf-8')
    
    elapsed = time.time() - start
    return {
        "success": True,
        "input_path": input_path,
        "output_path": output_path,
        "input_format": Path(input_path).suffix.lower(),
        "output_format": output_format,
        "feature_count": input_count,
        "geometry_types": geom_types,
        "input_crs": input_crs,
        "output_crs": target_crs or input_crs,
        "columns": [c for c in gdf.columns if c != 'geometry'],
        "elapsed_seconds": round(elapsed, 2)
    }


def batch_convert(input_dir, output_dir=None, output_format='geojson', file_pattern='*', **kwargs):
    """批量转换目录下文件"""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise ValueError(f"输入目录不存在: {input_dir}")
    
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = input_dir
    
    ext_map = {
        'geojson': '.geojson', 'shapefile': '.shp', 'csv': '.csv',
        'excel': '.xlsx', 'geopackage': '.gpkg', 'kml': '.kml',
        'geojsonseq': '.geojsonl', 'flatgeobuf': '.fgb',
    }
    ext = ext_map.get(output_format, '.geojson')
    
    results = []
    files = sorted(input_dir.glob(file_pattern))
    
    for f in files:
        if f.is_file() and f.suffix.lower() in ['.geojson','.json','.shp','.csv','.xls','.xlsx','.gpkg','.kml','.geojsonl','.fgb','.gml','.tab']:
            try:
                out_path = str(output_dir / f"{f.stem}{ext}")
                r = convert(str(f), out_path, output_format, **kwargs)
                results.append(r)
            except Exception as e:
                results.append({"success": False, "input_path": str(f), "error": str(e)})
    
    return {
        "success": True,
        "total": len(results),
        "converted": sum(1 for r in results if r.get("success")),
        "failed": sum(1 for r in results if not r.get("success")),
        "results": results
    }


if __name__ == "__main__":
    if "--input" in sys.argv and "--output" in sys.argv:
        input_idx = sys.argv.index("--input") + 1
        output_idx = sys.argv.index("--output") + 1
        
        with open(sys.argv[input_idx], 'r', encoding='utf-8') as f:
            payload = json.load(f)
        
        workspace = payload.get("workspace", ".")
        p = payload.get("params", {})
        
        input_path = p.get("input_path")
        if not input_path:
            print(json.dumps({"success": False, "error": "缺少必需参数: input_path"}, ensure_ascii=False))
            sys.exit(1)
        
        if not os.path.isabs(input_path):
            input_path = os.path.join(workspace, input_path)
        
        output_path = p.get("output_path")
        if output_path and not os.path.isabs(output_path):
            output_path = os.path.join(workspace, output_path)
        
        try:
            if p.get("batch"):
                result = batch_convert(
                    input_dir=input_path,
                    output_dir=output_path,
                    output_format=p.get("output_format", "geojson"),
                    file_pattern=p.get("file_pattern", "*"),
                    coord_x=p.get("coord_x"),
                    coord_y=p.get("coord_y"),
                    coord_crs=p.get("coord_crs", "EPSG:4326"),
                    target_crs=p.get("target_crs"),
                    select_columns=p.get("select_columns"),
                    rename_columns=p.get("rename_columns"),
                    include_geometry_csv=p.get("include_geometry", False),
                    sheet_name=p.get("sheet_name"),
                    layer_name=p.get("layer_name"),
                    encoding=p.get("encoding"),
                    wkt_col=p.get("wkt_col"),
                )
            else:
                result = convert(
                    input_path=input_path,
                    output_path=output_path,
                    output_format=p.get("output_format", "auto"),
                    coord_x=p.get("coord_x"),
                    coord_y=p.get("coord_y"),
                    coord_crs=p.get("coord_crs", "EPSG:4326"),
                    target_crs=p.get("target_crs"),
                    select_columns=p.get("select_columns"),
                    rename_columns=p.get("rename_columns"),
                    include_geometry_csv=p.get("include_geometry", False),
                    sheet_name=p.get("sheet_name"),
                    layer_name=p.get("layer_name"),
                    encoding=p.get("encoding"),
                    wkt_col=p.get("wkt_col"),
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}, ensure_ascii=False, indent=2))
            sys.exit(1)
    else:
        print("Usage: python main.py --input <input.json> --output <output.json>")
        sys.exit(1)
