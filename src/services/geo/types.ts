/**
 * Shared GIS data types used across the application.
 * This module defines the contract between geo services, stores, and UI components.
 */

// ─── Geometry Types ───────────────────────────────────────────────

export type GeometryType = 'Point' | 'MultiPoint' | 'LineString' | 'MultiLineString' | 'Polygon' | 'MultiPolygon' | 'GeometryCollection'

export interface BBox {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

// ─── GeoJSON (RFC 7946 subset) ────────────────────────────────────

export interface GeoJSONFeature {
  type: 'Feature'
  geometry: {
    type: GeometryType
    coordinates: any
  }
  properties: Record<string, any>
  id?: string | number
}

export interface GeoJSONFeatureCollection {
  type: 'FeatureCollection'
  features: GeoJSONFeature[]
}

export type GeoJSONFeatureId = string | number

export interface GeoJSONFeatureDiff {
  id: GeoJSONFeatureId
  newGeometry?: GeoJSONFeature['geometry']
  removeAllProperties?: boolean
  removeProperties?: string[]
  addOrUpdateProperties?: Array<{ key: string; value: any }>
}

export interface GeoJSONSourceDiff {
  removeAll?: boolean
  remove?: GeoJSONFeatureId[]
  add?: GeoJSONFeature[]
  update?: GeoJSONFeatureDiff[]
}

// ─── Data Source Descriptor ───────────────────────────────────────

export type DataSourceType = 'geojson' | 'csv' | 'shapefile' | 'geopackage' | 'kml' | 'geotiff' | '3dtiles' | 'pointcloud'

export interface DataSourceMeta {
  /** Original file name */
  fileName: string
  /** File extension (lowercase, with dot) */
  extension: string
  /** Detected source type */
  sourceType: DataSourceType
  /** File size in bytes */
  fileSize: number
  /** MIME type if available */
  mimeType?: string
  /**
   * Absolute filesystem path, if the layer originated from a file on disk.
   * Agent-generated / in-memory layers may omit this. Used by AssetExplorer
   * to reliably detect whether a file is already loaded (fileName alone is
   * ambiguous across directories).
   */
  filePath?: string
  /** Runtime-only dynamic layer metadata, usually driven by a resident worker. */
  dynamic?: {
    workerId?: string
    workerName?: string
    workerStartedAt?: number
    sequence?: number
    updatedAt?: number
    schemaChanged?: boolean
    mode?: 'full' | 'diff'
    updateable?: boolean
    sourceLayerId?: string
    highlight?: boolean
  }
}

// ─── Parsed Layer Data ────────────────────────────────────────────

export interface ParsedVectorData {
  kind: 'vector'
  geojson: GeoJSONFeatureCollection
  geometryType: GeometryType
  featureCount: number
  bbox: BBox
  /** CRS identifier (e.g. "EPSG:4326") */
  crs: string
  /** Attribute field names */
  fields: FieldDescriptor[]
  /**
   * Large file-backed layers keep only a lightweight sample in `geojson` and
   * store the full FeatureCollection in the renderer-session registry.
   */
  dataHandle?: string
  /** True when `geojson` is a bounded sample rather than the full dataset. */
  sampled?: boolean
  /** Number of features retained in the sample `geojson`. */
  sampleFeatureCount?: number
  /** Original full-data byte size used when the handle was created. */
  handleSizeBytes?: number
  /** Runtime-only MapLibre GeoJSONSource.updateData diff hint. */
  runtimeDiff?: GeoJSONSourceDiff
  /** True when runtimeDiff can safely be passed to MapLibre updateData. */
  runtimeDiffUpdateable?: boolean
}

export interface ParsedRasterData {
  kind: 'raster'
  /**
   * 如何把这个 raster 喂给 MapLibre。
   *  - 'image'    : 已经渲染成 PNG 的 data URL / blob URL，走 ImageSource（推荐：
   *                 本地 GeoTIFF 解完色带后给这类；体积小、可交互快）
   *  - 'tile-xyz' : XYZ 瓦片模板 URL
   *  - 'tile-wmts': WMTS 能力 URL（目前按 tile-xyz 同等对待，留字段）
   *  - 'cog'     : Cloud-Optimized GeoTIFF 的 HTTP URL（未实装）
   */
  source: 'image' | 'tile-xyz' | 'tile-wmts' | 'cog'
  /** `source='image'` 时必填：已经渲染好的 PNG data/blob URL */
  imageUrl?: string
  /** `source='tile-xyz' | 'tile-wmts'` 时必填 */
  tileUrl?: string
  bbox: BBox
  width: number
  height: number
  bandCount: number
  crs: string
  /**
   * 像素 nodata 值（导入时会被渲染成透明）。GeoTIFF parser 会从 tag 里读。
   */
  noDataValue?: number | null
  /** 每个 band 的真实统计值，用于分档 / 颜色映射 UI。 */
  bandStats?: Array<{ min: number; max: number; mean?: number | null; p2?: number | null; p98?: number | null }>
  /**
   * 实际编码成 imageUrl 的四角坐标（WGS84，顺时针 NW→NE→SE→SW）。
   * MapLibre ImageSource 需要它而不是 bbox；非地理 tiff 会反推成一个矩形。
   */
  imageCoordinates?: [
    [number, number],
    [number, number],
    [number, number],
    [number, number],
  ]
  /** Source raster path when this image can be re-rendered with a new color ramp. */
  sourcePath?: string
  /** Session-local buffer handle for File API loaded rasters without a stable disk path. */
  sourceBufferId?: string
  /** Backend raster registry id when this layer is served as local XYZ tiles. */
  rasterId?: string
  /** Rendering style used to produce `imageUrl`. */
  rasterStyle?: RasterStyleSettings
  /** True when frontend can re-read `sourcePath` and re-render this layer. */
  rerenderable?: boolean
}

/**
 * 3D Tiles 数据（OGC 3D Tiles：tileset.json + b3dm/i3dm/pnts + glTF）。
 *
 * MapLibre 原生无法渲染 3D Tiles —— 这类图层由 deck.gl 的 `Tile3DLayer`
 * （底层 loaders.gl `Tiles3DLoader`）通过共享 MapLibre 相机的 `MapboxOverlay`
 * 叠加渲染，不走任何 MapLibre source。
 */
export interface ParsedTiles3DData {
  kind: 'tiles3d'
  /** tileset.json 的 HTTP URL（通常由后端文件服务端点提供）。 */
  tilesetUrl: string
  /** 可选的经纬度范围，供 fitBounds 使用。 */
  bbox?: BBox
  /** 非地理配准 tileset 的摆放矩阵（column-major 4x4，共 16 个数）。 */
  modelMatrix?: number[]
  /** 点云类瓦片（pnts）的点数估计，用于 UI 展示。 */
  pointCount?: number
  /** CRS 标识（3D Tiles 通常为 EPSG:4979 / ECEF，多数已内嵌）。 */
  crs?: string
}

/**
 * 裸点云数据（.las / .laz），由 deck.gl `PointCloudLayer` + loaders.gl
 * `LASLoader` 渲染，同样挂在共享的 `MapboxOverlay` 上。
 */
export interface ParsedPointCloudData {
  kind: 'pointcloud'
  /** 点云文件的 HTTP URL（由后端文件服务端点提供）。 */
  url: string
  /** 文件格式。 */
  format: 'las' | 'laz'
  /**
   * 坐标参考方式：
   *  - 'lnglat' : 点坐标已是经纬度（WGS84），直接落位
   *  - 'meter-offset' : 点坐标是相对某锚点的米偏移，需要 `origin`
   */
  coordinate?: 'lnglat' | 'meter-offset'
  /** `coordinate='meter-offset'` 时的锚点经纬度 [lng, lat, altitude?]。 */
  origin?: [number, number, number?]
  bbox?: BBox
  pointCount?: number
  /** CRS 标识（点云原始坐标系，用于展示与后续重投影）。 */
  crs?: string
}

export type ParsedData = ParsedVectorData | ParsedRasterData | ParsedTiles3DData | ParsedPointCloudData

export interface FieldDescriptor {
  name: string
  type: 'string' | 'number' | 'boolean' | 'date' | 'unknown'
  /** Number of null/undefined values */
  nullCount: number
  /** Sample values for preview */
  sampleValues: any[]
}

// ─── Layer Definition (for MapStore) ──────────────────────────────

export type LayerRenderType =
  | 'fill'
  | 'line'
  | 'circle'
  | 'symbol'
  | 'heatmap'
  | 'raster'
  /**
   * 分级专题（choropleth）：按某个数值字段分档（分位数/等距/自定义断点），
   * 每档映射一个颜色。默认对 Polygon fill；Point 等价变成 circle 的颜色分档。
   */
  | 'graduated'
  /**
   * 分类专题：按某个字符串字段的唯一值映射颜色，常用于区划/类型这种离散
   * 字段。
   */
  | 'categorized'
  /**
   * 点聚合：同一 source 开 cluster + 三个 circle 档位 + count 数字。
   * 仅对 Point 几何有效。
   */
  | 'cluster'
  /**
   * 3D 拔起：按某个数值字段把 Polygon 拔出高度（fill-extrusion）。
   */
  | 'extrusion'
  /**
   * OGC 3D Tiles（tileset.json）：由 deck.gl Tile3DLayer 叠加渲染，
   * 支持 b3dm/i3dm 网格与 pnts 点云。不走 MapLibre source。
   */
  | 'tiles3d'
  /**
   * 裸点云（.las/.laz）：由 deck.gl PointCloudLayer 叠加渲染。
   */
  | 'pointcloud'

/** 分档策略 —— graduated renderer 使用 */
export type ClassificationMethod =
  | 'quantile'        // 分位数（n 档每档要素数相近）
  | 'equal-interval'  // 等距
  | 'jenks'           // 自然断点（简化实现，退回 quantile 也可接受）
  | 'manual'          // 使用用户传入的 breaks

export interface GraduatedClassification {
  field: string
  method: ClassificationMethod
  /**
   * 分档数（method='manual' 时由 breaks.length 决定，此字段忽略）。
   * 默认 5。
   */
  classes?: number
  /**
   * 当 method='manual' 时必填；否则作为计算结果被写回这个字段，
   * 长度 = classes - 1。
   */
  breaks?: number[]
  /**
   * 颜色渐变，长度需要等于档数；若省略按 classes 生成一个默认 viridis-like
   * 渐变。
   */
  palette?: string[]
}

export interface CategorizedClassification {
  field: string
  /**
   * 手动指定的 value→color 映射表；若省略，renderer 会在数据里扫前 N 个
   * 唯一值自动配色。
   */
  colors?: Record<string, string>
  /**
   * 自动模式下取前多少个唯一值，默认 12，超出归入 "其它"。
   */
  maxCategories?: number
  /** "其它" 档的颜色，默认 #9ca3af。 */
  otherColor?: string
  /** Optional fixed category order / whitelist. */
  categories?: string[]
}

/**
 * Numeric visual variable layered on top of the primary renderer.
 * It lets a layer keep its categorized/graduated color semantics while mapping
 * another numeric field to size or opacity.
 */
export interface NumericVisualVariable {
  field: string
  method?: ClassificationMethod
  classes?: number
  breaks?: number[]
  /**
   * Output values per class. For size this is pixels; for opacity this is 0-1.
   * If omitted, renderers interpolate from range.
   */
  values?: number[]
  /** Inclusive output range used when values are omitted. */
  range?: [number, number]
}

export interface SortVisualVariable {
  field: string
  /** Higher values are drawn above lower values by default. */
  order?: 'ascending' | 'descending'
}

export type LayerFilterOperator = '=' | '!=' | '>' | '<' | '>=' | '<=' | 'contains' | 'in'

export interface LayerAttributeFilter {
  field: string
  op: LayerFilterOperator
  value?: unknown
}

export interface LayerFilterSpec {
  attribute?: LayerAttributeFilter[]
}

export interface LegendSpec {
  visible?: boolean
  title?: string
  labels?: Record<string, string>
  order?: string[]
}

export interface HeatmapSettings {
  /**
   * 权重字段。未设置时所有点权重 1。字段必须是数值。
   */
  weightField?: string
  /** 影响半径（像素），默认 30。 */
  radius?: number
  /** 整体强度 0-1，默认 1。 */
  intensity?: number
}

export interface ClusterSettings {
  /** 聚合半径（像素），默认 50。 */
  radius?: number
  /**
   * zoom 到多大就不再聚合（展开为原始点），默认 14。
   */
  maxZoom?: number
}

export interface ExtrusionSettings {
  /** 拔高的字段（数值）。 */
  heightField: string
  /** 高度乘数，默认 1。 */
  heightMultiplier?: number
  /** 基准高度字段，默认 0。 */
  baseField?: string
}

export interface Tiles3DSettings {
  /** pnts 点云瓦片的点大小（像素），默认 1.5。 */
  pointSize?: number
  /** 叠加着色（十六进制），未设置时使用瓦片自带颜色。 */
  color?: string
  /**
   * 最大屏幕空间误差（SSE）。越大越省性能、越粗糙；越小越精细、越耗性能。
   * 默认 16。
   */
  maximumScreenSpaceError?: number
}

export interface PointCloudSettings {
  /** 点大小（像素），默认 1.5。 */
  pointSize?: number
  /** 着色（十六进制），未设置时使用点自带颜色或默认白色。 */
  color?: string
}

export type RasterColorRampName =
  | 'viridis'
  | 'magma'
  | 'plasma'
  | 'inferno'
  | 'turbo'
  | 'gray'
  | 'terrain'
  | 'spectral'
  | 'custom'

export interface RasterColorStop {
  /** Stop position. Values in [0, 1] are normalized; other values are source pixel values. */
  value: number
  /** CSS hex color. */
  color: string
  /** Per-stop alpha in [0, 1]. */
  opacity?: number
}

export interface RasterStyleSettings {
  /** Single-band display band, 1-based. RGB rasters ignore this unless mode='singleband'. */
  band?: number
  /** Palette name, or 'custom' when `stops` is provided. */
  ramp?: RasterColorRampName
  /** Custom color/alpha stops. Supports normalized [0, 1] or source pixel values. */
  stops?: RasterColorStop[]
  /** Coordinate space for custom stops. Agent tools default to source values; UI defaults to normalized. */
  stopsUnit?: 'normalized' | 'source'
  /** Stretch min/max in source pixel values. Defaults to robust P2/P98 stats. */
  min?: number
  max?: number
  /** Overall alpha applied after per-stop alpha. */
  opacity?: number
  /** Reverse the ramp direction. */
  reverse?: boolean
  /** Force RGB or single-band rendering. Defaults to RGB for 3+ band rasters. */
  mode?: 'auto' | 'singleband' | 'rgb'
}

export interface LayerStyle {
  renderType: LayerRenderType
  color: string
  opacity: number
  strokeColor: string
  strokeWidth: number
  strokeOpacity?: number
  /** Line dash pattern, e.g. [2, 2] for dashed lines. */
  lineDasharray?: number[]
  radius?: number
  /** For fill layers */
  fillOpacity?: number
  /** Optional display filter applied at render time. */
  filter?: LayerFilterSpec
  /** Field-driven point radius / line width / polygon boundary width. */
  sizeVariable?: NumericVisualVariable
  /** Field-driven fill / line / circle opacity. */
  opacityVariable?: NumericVisualVariable
  /** Feature-level draw order inside one layer. */
  sortVariable?: SortVisualVariable
  /** Stable legend metadata shared by map UI, layout composer, and agent tools. */
  legend?: LegendSpec

  // ── 下列字段仅在对应 renderType 启用时被消费。都是可选字段，
  //    老图层加载不受影响。
  /** `renderType='graduated'` 时必填 */
  graduated?: GraduatedClassification
  /** `renderType='categorized'` 时必填 */
  categorized?: CategorizedClassification
  /** `renderType='heatmap'` 时可选，给默认 */
  heatmap?: HeatmapSettings
  /** `renderType='cluster'` 时可选 */
  cluster?: ClusterSettings
  /** `renderType='extrusion'` 时必填 heightField */
  extrusion?: ExtrusionSettings
  /** `renderType='raster'` 时可选 — color ramp / stretch settings. */
  raster?: RasterStyleSettings
  /** `renderType='tiles3d'` 时可选 — 3D Tiles 渲染设置。 */
  tiles3d?: Tiles3DSettings
  /** `renderType='pointcloud'` 时可选 — 点云渲染设置。 */
  pointcloud?: PointCloudSettings
  /** `renderType='symbol'` 时可选 — 图标配置 */
  icon?: string  // 'circle' | 'emoji:📍' | 'svg:pin' | 'path:/abs/icon.svg'
  /** `renderType='symbol'` 时可选 — 文字标注配置 */
  label?: {
    field: string
    fontSize?: number
    color?: string
    offset?: [number, number]
    haloColor?: string
    haloWidth?: number
  }
}

export interface MapLayerDefinition {
  id: string
  name: string
  sourceType: DataSourceType
  visible: boolean
  style: LayerStyle
  data: ParsedData
  meta: DataSourceMeta
  /** Timestamp when the layer was added */
  addedAt: number
  /** If set, this layer is managed by an extension (base sync skips it) */
  extension?: string
}

// ─── Basemap Configuration ────────────────────────────────────────

export interface BasemapSource {
  id: string
  name: string
  type: 'vector-style' | 'raster-tiles'
  /** MapLibre style JSON URL (for vector-style) or tile URL template (for raster-tiles) */
  url: string
  /** Attribution text */
  attribution?: string
  /** Preview thumbnail URL */
  thumbnail?: string
}

export const BUILTIN_BASEMAPS: BasemapSource[] = [
  {
    id: 'osm-streets',
    name: 'OpenStreetMap Streets',
    type: 'raster-tiles',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors',
  },
  {
    id: 'carto-dark',
    name: 'CARTO Dark Matter',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'carto-dark-nolabels',
    name: 'CARTO Dark (No Labels)',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'carto-light',
    name: 'CARTO Positron',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'carto-light-nolabels',
    name: 'CARTO Positron (No Labels)',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'carto-voyager',
    name: 'CARTO Voyager',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'carto-voyager-nolabels',
    name: 'CARTO Voyager (No Labels)',
    type: 'vector-style',
    url: 'https://basemaps.cartocdn.com/gl/voyager-nolabels-gl-style/style.json',
    attribution: '© CARTO',
  },
  {
    id: 'osm-raster',
    name: 'OpenStreetMap Raster',
    type: 'raster-tiles',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors',
  },
]
