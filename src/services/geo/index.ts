/**
 * Geo services barrel — public API for the geo service layer.
 */
export { loadGeoFile, loadGeoFiles, isSupportedExtension, getSupportedExtensions } from './GeoFileService'
export { getDefaultStyle, getNextLayerColor, resetLayerColorIndex } from './defaultStyles'
export { detectGeometryType, summarizeGeometryTypes } from './geometry'
export type { GeometryTypeSummary } from './geometry'
export {
  LARGE_LAYER_THRESHOLD_BYTES,
  LARGE_LAYER_SAMPLE_FEATURES,
  hasVectorGeoJSON,
  makeHandledVectorData,
  makeSampledVectorData,
  registerVectorGeoJSON,
  releaseVectorGeoJSON,
  resolveVectorGeoJSON,
  shouldHandleLayer,
  stripVectorHandle,
} from './layerDataRegistry'
export type {
  MapLayerDefinition,
  ParsedData,
  ParsedVectorData,
  ParsedRasterData,
  ParsedTiles3DData,
  ParsedPointCloudData,
  Tiles3DSettings,
  PointCloudSettings,
  DataSourceType,
  DataSourceMeta,
  GeometryType,
  BBox,
  FieldDescriptor,
  LayerStyle,
  LayerRenderType,
  BasemapSource,
  GeoJSONFeature,
  GeoJSONFeatureCollection,
  GeoJSONSourceDiff,
  ClassificationMethod,
  GraduatedClassification,
  CategorizedClassification,
  ExtrusionSettings,
  NumericVisualVariable,
  SortVisualVariable,
  LayerFilterSpec,
  LayerAttributeFilter,
  LegendSpec,
  RasterColorRampName,
  RasterColorStop,
  RasterStyleSettings,
} from './types'
export { BUILTIN_BASEMAPS } from './types'
