# format_converter - 多格式要素数据转换

## 功能

支持多种地理数据格式的相互转换，覆盖点、线、面要素。

## 支持格式

| 格式 | 读取 | 写出 | 说明 |
|------|------|------|------|
| **GeoJSON** | ✅ | ✅ | 最通用的地理数据格式 |
| **Shapefile** | ✅ | ✅ | GIS 标准格式 |
| **CSV** | ✅ | ✅ | 点要素自动识别坐标列 |
| **Excel** | ✅ | ✅ | 点要素自动识别坐标列 |
| **GeoPackage** | ✅ | ✅ | 现代 GIS 格式 |
| **KML** | ✅ | ✅ | Google Earth 格式 |
| **GeoJSONSeq** | ✅ | ✅ | 流式 GeoJSON |
| **FlatGeobuf** | ✅ | ✅ | 高性能二进制格式 |

## 特性

- **自动识别坐标列**：CSV/Excel 中自动识别 lng/lat、lon/latitude、经度/纬度 等列名
- **WKT/WKB 支持**：非点要素自动解析 geometry/wkt 列
- **智能编码检测**：支持 UTF-8、GBK、GB2312、GB18030 等编码
- **坐标系转换**：支持任意坐标系互转
- **批量转换**：支持目录下所有文件批量转换
- **列选择/重命名**：支持选择特定列或重命名列

## 参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `input_path` | string | ✅ | 输入文件路径 |
| `output_path` | string | ❌ | 输出文件路径（可选） |
| `output_format` | string | ❌ | 输出格式 |
| `coord_x` | string | ❌ | X 坐标列名（自动识别） |
| `coord_y` | string | ❌ | Y 坐标列名（自动识别） |
| `coord_crs` | string | ❌ | 输入坐标系（默认 EPSG:4326） |
| `target_crs` | string | ❌ | 目标坐标系 |
| `select_columns` | array | ❌ | 选择输出的列 |
| `rename_columns` | object | ❌ | 列重命名映射 |
| `include_geometry` | bool | ❌ | CSV/Excel 是否包含几何列 |
| `sheet_name` | string | ❌ | Excel 工作表名 |
| `layer_name` | string | ❌ | GeoPackage 图层名 |
| `encoding` | string | ❌ | 指定编码 |
| `batch` | bool | ❌ | 批量转换模式 |
| `file_pattern` | string | ❌ | 批量文件模式 |

## 示例

### CSV 转 GeoJSON
```json
{
  "input_path": "data.csv",
  "output_format": "geojson",
  "coord_x": "lng",
  "coord_y": "lat"
}
```

### Shapefile 转 Excel
```json
{
  "input_path": "boundary.shp",
  "output_format": "excel",
  "include_geometry": true
}
```

### 批量转换
```json
{
  "input_path": "./data/",
  "output_format": "geojson",
  "batch": true,
  "file_pattern": "*.csv"
}
```