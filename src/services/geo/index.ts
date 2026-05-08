/**
 * Geo services barrel — public API for the geo service layer.
 */
export { loadGeoFile, loadGeoFiles, isSupportedExtension, getSupportedExtensions } from './GeoFileService'
export { getDefaultStyle, getNextLayerColor, resetLayerColorIndex } from './defaultStyles'
export type {
  MapLayerDefinition,
  ParsedData,
  ParsedVectorData,
  ParsedRasterData,
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
  ClassificationMethod,
  GraduatedClassification,
  CategorizedClassification,
} from './types'
export { BUILTIN_BASEMAPS } from './types'
