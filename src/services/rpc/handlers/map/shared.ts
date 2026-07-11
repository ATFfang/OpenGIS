import { bboxToTuple, computeBBox } from '../_map_util';
import { useMapStore } from '@/stores/mapStore';
import { hasVectorGeoJSON, makeHandledVectorData, shouldHandleLayer, type GeoJSONFeature, type GeoJSONFeatureCollection, type GeometryType, type LayerStyle, type MapLayerDefinition, type ParsedVectorData } from '@/services/geo';
import { parseCSV, parseGeoJSON } from '@/services/geo/parsers';

export async function getFileInfo(
  api: any,
  filePath: string,
): Promise<{ size?: number }> {
  if (!api?.getFileInfo) return {}
  try {
    const result = await api.getFileInfo(filePath);
    const size = result?.success && typeof result.info?.size === 'number'
      ? result.info.size
      : undefined;
    return { size };
  } catch {
    return {};
  }
}

export async function readTextFileForMapLayer(
  api: any,
  filePath: string,
  sizeBytes?: number,
): Promise<string> {
  if (shouldHandleLayer(sizeBytes) && api?.readFileAsBuffer) {
    const result = await api.readFileAsBuffer(filePath);
    if (!result?.success || !result.buffer) {
      throw new Error(result?.error || 'readFileAsBuffer returned no buffer');
    }
    return new TextDecoder('utf-8').decode(result.buffer);
  }

  const result = await api.readFile(filePath);
  if (!result?.success || typeof result.content !== 'string') {
    throw new Error(result?.error || 'readFile returned no content');
  }
  return result.content;
}

export async function ensureFullVectorLayer(
  layer: MapLayerDefinition,
): Promise<MapLayerDefinition> {
  if (layer.data.kind !== 'vector') return layer;
  if (!layer.data.sampled) return layer;
  if (hasVectorGeoJSON(layer.data.dataHandle)) return layer;

  const filePath = layer.meta?.filePath;
  if (!filePath) return layer;
  const ext = getExtensionLower(filePath);
  if (!['.geojson', '.json', '.csv', '.tsv'].includes(ext)) return layer;

  const api = (globalThis as any).window?.electronAPI;
  if (!api?.readFile) return layer;

  try {
    const fileInfo = await getFileInfo(api, filePath);
    const text = await readTextFileForMapLayer(api, filePath, fileInfo.size ?? layer.meta.fileSize);
    const parsed = ext === '.csv' || ext === '.tsv'
      ? parseCSV(text, layer.name)
      : parseGeoJSON(text, layer.name);
    const size = fileInfo.size ?? layer.meta.fileSize ?? byteLength(text);
    const data = makeHandledVectorData(parsed, {
      handleId: `vector:${layer.id}:restored:${Date.now()}`,
      sizeBytes: size,
    });
    const restored: MapLayerDefinition = {
      ...layer,
      data,
      meta: {
        ...layer.meta,
        fileSize: size,
        filePath,
      },
    };
    useMapStore.getState().addLayer(restored);
    return restored;
  } catch (err) {
    console.warn('[mapHandlers] Failed to restore full vector data from file:', filePath, err);
    return layer;
  }
}

export function byteLength(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

export function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/, '');
}

export function estimateGeoJSONBytes(fc: GeoJSONFeatureCollection): number {
  try {
    return byteLength(JSON.stringify(fc));
  } catch {
    return 0;
  }
}

export function applyPaintToLayerStyle(
  style: Partial<LayerStyle>,
  paint: Record<string, unknown> | undefined,
  options: { skipColor?: boolean; renderType?: LayerStyle['renderType'] } = {},
): void {
  if (!paint) return;

  const renderType = options.renderType ?? style.renderType;
  if (!options.skipColor) {
    const colorKeys =
      renderType === 'line'
        ? ['line-color', 'fill-color', 'circle-color']
        : renderType === 'circle'
          ? ['circle-color', 'fill-color']
          : ['fill-color', 'circle-color'];
    const color = firstDefinedPaintValue(paint, colorKeys);
    if (color !== null) style.color = color;
  }
  const opacityKeys =
    renderType === 'line'
      ? ['line-opacity']
      : renderType === 'circle'
        ? ['circle-opacity']
        : ['circle-opacity'];
  const opacity = firstDefinedNumber(paint, opacityKeys);
  if (opacity !== null) style.opacity = opacity;
  const fillOpacity = firstDefinedNumber(paint, ['fill-opacity']);
  if (fillOpacity !== null) style.fillOpacity = fillOpacity;
  const strokeColor = firstDefinedPaintValue(paint, [
    'stroke-color',
    'line-color',
    'circle-stroke-color',
  ]);
  if (strokeColor !== null) style.strokeColor = strokeColor;
  const strokeWidth = firstDefinedNumber(paint, [
    'stroke-width',
    'line-width',
    'circle-stroke-width',
  ]);
  if (strokeWidth !== null) style.strokeWidth = strokeWidth;
  const strokeOpacity = firstDefinedNumber(paint, [
    'stroke-opacity',
    'line-opacity',
    'circle-stroke-opacity',
  ]);
  if (strokeOpacity !== null) style.strokeOpacity = strokeOpacity;
  const dash = paint['line-dasharray'] ?? paint['stroke-dasharray'];
  if (Array.isArray(dash)) {
    const parsed = dash
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value >= 0);
    if (parsed.length > 0) style.lineDasharray = parsed;
  }
  const radius = firstDefinedNumber(paint, ['circle-radius']);
  if (radius !== null) style.radius = radius;
}

const STYLE_PAINT_KEYS = new Set([
  'circle-color',
  'circle-radius',
  'circle-opacity',
  'circle-stroke-color',
  'circle-stroke-width',
  'circle-stroke-opacity',
  'line-color',
  'line-width',
  'line-opacity',
  'line-dasharray',
  'fill-color',
  'fill-opacity',
  'fill-outline-color',
  'stroke-color',
  'stroke-width',
  'stroke-opacity',
  'stroke-dasharray',
]);

export function normalizeStylePaintInput<T extends { paint?: Record<string, unknown> } & Record<string, unknown>>(style: T): T {
  const paint = { ...(style.paint ?? {}) };
  let hasPaint = Boolean(style.paint);
  for (const key of STYLE_PAINT_KEYS) {
    if (Object.prototype.hasOwnProperty.call(style, key)) {
      paint[key] = style[key];
      hasPaint = true;
    }
  }
  return hasPaint ? { ...style, paint } : style;
}

function firstDefinedPaintValue(
  obj: Record<string, unknown>,
  keys: string[],
): any | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v) return v;
    if (Array.isArray(v) && v.length > 0) return v;
  }
  return null;
}

function firstDefinedNumber(
  obj: Record<string, unknown>,
  keys: string[],
): number | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

/** 从路径里拿小写扩展名（含点），没扩展名返回空串。 */
export function getExtensionLower(filePath: string): string {
  const base = filePath.split(/[\\/]/).pop() ?? '';
  const i = base.lastIndexOf('.');
  return i >= 0 ? base.slice(i).toLowerCase() : '';
}

/** 从路径里拿不带扩展名的文件名，作为默认 displayName。 */
export function basenameWithoutExt(filePath: string): string {
  const base = filePath.split(/[\\/]/).pop() ?? 'layer';
  const i = base.lastIndexOf('.');
  return i > 0 ? base.slice(0, i) : base;
}

/**
 * 给 list_layers / get_layer 用的精简表示。故意**不带 features**，
 * 只留 bbox/feature_count/fields 这些 LLM 决策需要的元信息。
 */
export function summarizeLayer(layer: MapLayerDefinition) {
  const isVector = layer.data.kind === 'vector';
  const vector = isVector ? (layer.data as ParsedVectorData) : null;
  return {
    layer_id: layer.id,
    name: layer.name,
    source_type: layer.sourceType,
    visible: layer.visible,
    bbox: bboxToTuple(layer.data.bbox),
    feature_count: vector ? vector.featureCount : 0,
    geometry_type: vector ? vector.geometryType : null,
    crs: layer.data.crs,
    fields: vector ? vector.fields.map((f) => ({ name: f.name, type: f.type })) : [],
    style: {
      render_type: layer.style.renderType,
      color: layer.style.color,
      opacity: layer.style.opacity,
      stroke_color: layer.style.strokeColor,
      stroke_width: layer.style.strokeWidth,
      stroke_opacity: layer.style.strokeOpacity,
      line_dasharray: layer.style.lineDasharray ?? null,
      fill_opacity: layer.style.fillOpacity,
      radius: layer.style.radius,
      size_variable: layer.style.sizeVariable ?? null,
      opacity_variable: layer.style.opacityVariable ?? null,
      sort_variable: layer.style.sortVariable ?? null,
      label: layer.style.label ?? null,
      icon: layer.style.icon ?? null,
      filter: layer.style.filter ?? null,
      legend: layer.style.legend ?? null,
      categorized: layer.style.categorized ?? null,
      graduated: layer.style.graduated ?? null,
      heatmap: layer.style.heatmap ?? null,
      cluster: layer.style.cluster ?? null,
      extrusion: layer.style.extrusion ?? null,
    },
    meta: {
      source_type: layer.meta?.sourceType ?? layer.sourceType,
      extension: layer.meta?.extension ?? null,
      file_name: layer.meta?.fileName ?? null,
      file_size: layer.meta?.fileSize ?? null,
      dynamic: Boolean(layer.meta?.dynamic),
    },
    sampled: vector ? Boolean(vector.sampled) : false,
    sample_feature_count: vector?.sampleFeatureCount ?? null,
    full_data_available: vector ? !vector.sampled || hasVectorGeoJSON(vector.dataHandle) : false,
    added_at: layer.addedAt,
  };
}

// ── query_features 过滤原语 ────────────────────────────────────────

export type AttrFilter = { field: string; op: '=' | '!=' | '>' | '<' | '>=' | '<=' | 'contains' | 'in'; value?: unknown };

export function matchesAttributes(feature: GeoJSONFeature, filters: AttrFilter[]): boolean {
  if (filters.length === 0) return true;
  const props = (feature.properties ?? {}) as Record<string, unknown>;
  for (const f of filters) {
    const v = props[f.field];
    switch (f.op) {
      case '=':
        // 宽松相等：数值与字符串表示视为相等，避免 "5" != 5 带来的坑
        if (String(v) !== String(f.value)) return false;
        break;
      case '!=':
        if (String(v) === String(f.value)) return false;
        break;
      case '>':
        if (!(typeof v === 'number' && typeof f.value === 'number' && v > f.value)) {
          if (Number(v) <= Number(f.value) || !Number.isFinite(Number(v))) return false;
        }
        break;
      case '<':
        if (!(typeof v === 'number' && typeof f.value === 'number' && v < f.value)) {
          if (Number(v) >= Number(f.value) || !Number.isFinite(Number(v))) return false;
        }
        break;
      case '>=':
        if (Number(v) < Number(f.value) || !Number.isFinite(Number(v))) return false;
        break;
      case '<=':
        if (Number(v) > Number(f.value) || !Number.isFinite(Number(v))) return false;
        break;
      case 'contains':
        if (typeof v !== 'string' || typeof f.value !== 'string' || !v.includes(f.value)) {
          return false;
        }
        break;
      case 'in': {
        const values = Array.isArray(f.value) ? f.value.map(String) : [String(f.value)];
        if (!values.includes(String(v))) return false;
        break;
      }
    }
  }
  return true;
}

export function renderTypeFromStyleType(
  type: 'circle' | 'line' | 'fill' | 'raster' | 'symbol',
  geometryType: GeometryType,
): LayerStyle['renderType'] {
  if (type === 'raster') return 'raster'
  if (type === 'symbol') return 'symbol'
  if (type === 'circle' || type === 'line' || type === 'fill') return type
  if (String(geometryType).includes('Line')) return 'line'
  if (String(geometryType).includes('Polygon')) return 'fill'
  return 'circle'
}

/**
 * feature 的 geometry bbox 和 query bbox 有交集即视为命中。
 * 只查 feature 的 bbox 而不做精确相交——对 LLM 决策足够。
 */
export function featureIntersectsBBox(
  feature: GeoJSONFeature,
  queryBBox: [number, number, number, number],
): boolean {
  const fc: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [feature],
  };
  const b = computeBBox(fc);
  const [qMinX, qMinY, qMaxX, qMaxY] = queryBBox;
  return !(b.maxX < qMinX || b.minX > qMaxX || b.maxY < qMinY || b.minY > qMaxY);
}

/**
 * point 命中 feature 的 bbox 即视为命中 —— 对 Point feature 来说是等价的，
 * 对 Polygon 来说是近似；精确 point-in-polygon 留给 geo 分析 skill。
 */
export function featureContainsPoint(
  feature: GeoJSONFeature,
  point: [number, number],
): boolean {
  const fc: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [feature],
  };
  const b = computeBBox(fc);
  const [x, y] = point;
  return x >= b.minX && x <= b.maxX && y >= b.minY && y <= b.maxY;
}
