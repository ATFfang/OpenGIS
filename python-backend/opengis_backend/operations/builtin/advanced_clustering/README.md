# 空间聚类分析

空间聚类分析 Operation，支持多种聚类算法、多种要素格式和多种数据源输入。

## 功能特性

### 多种聚类方法

| 方法 | 说明 | 适用场景 | 关键参数 |
|------|------|----------|----------|
| **DBSCAN** | 基于密度的聚类 | 发现任意形状簇，自动识别噪声点 | `eps_meters`, `min_samples` |
| **KMeans** | K均值聚类 | 快速聚类，适合球形分布数据 | `n_clusters` |
| **HDBSCAN** | 层次密度聚类 | 处理不同密度的簇，更稳定的参数选择 | `min_cluster_size`, `min_samples` |
| **OPTICS** | 可达距离聚类 | DBSCAN 的改进，适应不同密度 | `min_samples`, `max_eps_meters` |
| **Agglomerative** | 层次聚类 | 自上而下或自下而上的层次结构 | `n_clusters` 或 `distance_threshold_meters` |

### 多要素格式支持

| 几何类型 | 处理方式 | 说明 |
|---------|---------|------|
| **Point/MultiPoint** | 直接聚类 | 使用原始坐标 |
| **LineString/MultiLineString** | 质心提取 | 提取线要素中心点后聚类 |
| **Polygon/MultiPolygon** | 质心/代表点 | 提取面要素质心或内部代表点 |
| **混合几何** | 自动处理 | 按类型分别处理 |

### 多源数据输入

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| **GeoJSON** | `.geojson`, `.json` | 矢量数据标准格式 |
| **Shapefile** | `.shp` | GIS 标准格式 |
| **CSV** | `.csv` | 需指定坐标字段 (`coord_x`, `coord_y`) |
| **GeoPackage** | `.gpkg` | OGC 标准格式 |
| **KML/KMZ** | `.kml`, `.kmz` | Google Earth 格式 |

## 使用方式

### 输入参数

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `input_path` | string | ✅ | - | 输入数据文件路径 |
| `method` | string | ❌ | `dbscan` | 聚类方法 |
| `output_dir` | string | ❌ | 输入文件目录 | 输出目录 |
| `coord_x` | string | ❌ | - | CSV 文件的 X 坐标字段名 |
| `coord_y` | string | ❌ | - | CSV 文件的 Y 坐标字段名 |
| `coord_crs` | string | ❌ | `EPSG:4326` | 坐标参考系 |
| `centroid_method` | string | ❌ | `centroid` | 面/线要素质心提取方法 |
| `prefix` | string | ❌ | - | 输出文件名前缀 |
| `suffix` | string | ❌ | - | 输出文件名后缀 |
| `save_centers` | boolean | ❌ | `true` | 是否保存聚类中心点 |
| `save_stats` | boolean | ❌ | `true` | 是否保存统计信息 |

### DBSCAN 专用参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `eps_meters` | number | `500` | 邻域半径（米） |
| `min_samples` | integer | `5` | 核心点最小邻居数 |

### KMeans / Agglomerative 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `n_clusters` | integer | `5` | 聚类数量 |
| `distance_threshold_meters` | number | `null` | 距离阈值（仅 Agglomerative） |

### HDBSCAN 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_cluster_size` | integer | `15` | 最小聚类大小 |
| `min_samples` | integer | `5` | 最小样本数 |

### OPTICS 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_samples` | integer | `5` | 最小样本数 |
| `max_eps_meters` | number | `1000` | 最大邻域半径（米） |

## 输出

| 输出文件 | 说明 |
|---------|------|
| `{name}_clustered.geojson` | 带聚类标签的要素数据（保留原始几何） |
| `{name}_centers.geojson` | 聚类中心点（Point 几何） |
| `{name}_stats.json` | 聚类统计信息（JSON 格式） |

### 统计信息字段

- `method`: 使用的聚类方法
- `total_features`: 总要素数
- `total_clusters`: 聚类数量
- `noise_points`: 噪声点数量
- `cluster_rate`: 聚类比例
- `processing_time_seconds`: 处理耗时
- `geometry_type`: 原始几何类型
- `centroid_method`: 质心提取方法（仅线/面）
- `top_clusters`: 最大聚类列表

## 使用示例

### 1. DBSCAN 聚类 CSV 数据

```json
{
  "input_path": "data/shops.csv",
  "coord_x": "lng",
  "coord_y": "lat",
  "method": "dbscan",
  "eps_meters": 300,
  "min_samples": 5
}
```

### 2. KMeans 聚类 GeoJSON 点数据

```json
{
  "input_path": "data/pois.geojson",
  "method": "kmeans",
  "n_clusters": 10
}
```

### 3. HDBSCAN 聚类 Shapefile 面数据

```json
{
  "input_path": "data/buildings.shp",
  "method": "hdbscan",
  "centroid_method": "representative",
  "min_cluster_size": 20,
  "min_samples": 5
}
```

### 4. OPTICS 聚类线要素

```json
{
  "input_path": "data/roads.geojson",
  "method": "optics",
  "min_samples": 10,
  "max_eps_meters": 500
}
```

### 5. 层次聚类 GeoPackage

```json
{
  "input_path": "data/regions.gpkg",
  "method": "agglomerative",
  "n_clusters": 8,
  "centroid_method": "centroid"
}
```

## 聚类方法选择指南

| 场景 | 推荐方法 | 原因 |
|------|---------|------|
| 不知道聚类数量 | DBSCAN, HDBSCAN, OPTICS | 自动确定聚类数量 |
| 数据有噪声 | DBSCAN, HDBSCAN, OPTICS | 能识别噪声点 |
| 需要固定聚类数 | KMeans, Agglomerative | 直接指定数量 |
| 数据密度不均 | HDBSCAN, OPTICS | 适应不同密度 |
| 大数据集 | KMeans | 计算效率高 |
| 线/面要素 | 任意方法 | 自动提取质心 |

## 依赖

- Python 3.8+
- geopandas
- pandas
- numpy
- shapely
- scikit-learn
- hdbscan (可选，仅用于 HDBSCAN 方法)
