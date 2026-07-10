import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { parseParams } from '../_util';
import { hydrateMapLayersForRpc, useMapStore } from '@/stores/mapStore';
import { getDefaultStyle, resolveVectorGeoJSON, type CategorizedClassification, type GeoJSONFeature, type GraduatedClassification, type LayerStyle, type MapLayerDefinition, type NumericVisualVariable, type ParsedVectorData } from '@/services/geo';
import { bboxToTuple, computeBBox, detectGeometryType } from '../_map_util';
import { GetLegendSpecSchema, HighlightFeaturesSchema, SetLayerFilterSchema, SetLayerLabelSchema, SetLayerOrderSchema, SetLayerRendererSchema, SetLayerStyleSchema, SetLayerVisibilitySchema, UpdateLegendSpecSchema, UpdateVisualVariablesSchema } from '../schemas';
import { applyPaintToLayerStyle, ensureFullVectorLayer, estimateGeoJSONBytes, matchesAttributes } from './shared';

export const styleHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.set_layer_renderer': async (params) => {
    const parsed = parseParams(
      SetLayerRendererSchema,
      params,
      'rpc.ui.map.set_layer_renderer',
    );
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    let layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_renderer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_renderer' },
      );
    }
    if (layer.data.kind === 'vector') {
      layer = await ensureFullVectorLayer(layer);
    }

    const nextStyle: Partial<LayerStyle> = {
      renderType: parsed.renderer,
      graduated: undefined,
      categorized: undefined,
      heatmap: undefined,
      cluster: undefined,
      extrusion: undefined,
    };

    switch (parsed.renderer) {
      case 'graduated':
        if (!parsed.graduated) {
          throw RpcError.invalidParams(
            'set_layer_renderer: graduated config is required when renderer=graduated',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.graduated = normalizeGraduatedConfig(layer, parsed.graduated);
        break;
      case 'categorized':
        if (!parsed.categorized) {
          throw RpcError.invalidParams(
            'set_layer_renderer: categorized config is required when renderer=categorized',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.categorized = normalizeCategorizedConfig(layer, parsed.categorized);
        break;
      case 'heatmap':
        nextStyle.heatmap = parsed.heatmap ?? {};
        break;
      case 'cluster':
        nextStyle.cluster = parsed.cluster ?? {};
        break;
      case 'extrusion':
        if (!parsed.extrusion) {
          throw RpcError.invalidParams(
            'set_layer_renderer: extrusion config is required when renderer=extrusion',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.extrusion = parsed.extrusion;
        break;
      // fill/line/circle/raster: no extra config
      default:
        break;
    }

    if (parsed.sizeVariable !== undefined) {
      nextStyle.sizeVariable = parsed.sizeVariable
        ? normalizeVisualVariable(layer, parsed.sizeVariable, 'sizeVariable')
        : undefined;
    }
    if (parsed.opacityVariable !== undefined) {
      nextStyle.opacityVariable = parsed.opacityVariable
        ? normalizeVisualVariable(layer, parsed.opacityVariable, 'opacityVariable')
        : undefined;
    }

    store.updateLayerStyle(parsed.layer_id, nextStyle);
    return {
      layer_id: parsed.layer_id,
      renderer: parsed.renderer,
      graduated: nextStyle.graduated ?? null,
      categorized: nextStyle.categorized ?? null,
      size_variable: nextStyle.sizeVariable ?? null,
      opacity_variable: nextStyle.opacityVariable ?? null,
    };
  },

  'rpc.ui.map.set_layer_style': async (params) => {
    const parsed = parseParams(SetLayerStyleSchema, params, 'rpc.ui.map.set_layer_style');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_style: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_style' },
      );
    }

    // Python display.py::update_layer_style 发来的 paint 里通常只有
    // `fill-color` / `fill-opacity`，但 MapLibre paint 可能携带 stroke /
    // radius 等更多字段。这里把所有能映射到 LayerStyle 的字段都透传，
    // 避免之前只取 color/opacity 造成的样式丢失。
    const paint = (parsed.style.paint ?? {}) as Record<string, unknown>;

    // graduated / categorized 专题层的颜色由分级/分类配置决定，
    // 直接改 style.color 是无效语义且会让 style.color 与实际渲染不一致，
    // 因此对专题层屏蔽基础 color 的写入（opacity/stroke 等仍可改）。
    const isThematic =
      layer.style.renderType === 'graduated' || layer.style.renderType === 'categorized';

    const styleUpdates: Partial<LayerStyle> = {};
    applyPaintToLayerStyle(styleUpdates, paint, {
      skipColor: isThematic,
      renderType: layer.style.renderType,
    });

    if (Object.keys(styleUpdates).length > 0) {
      store.updateLayerStyle(parsed.layer_id, styleUpdates);
    }

    return { layer_id: parsed.layer_id, applied: styleUpdates };
  },

  'rpc.ui.map.set_layer_filter': async (params) => {
    const parsed = parseParams(SetLayerFilterSchema, params, 'rpc.ui.map.set_layer_filter');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_filter: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_filter' },
      );
    }
    store.updateLayerStyle(parsed.layer_id, { filter: parsed.filter ?? undefined });
    return { layer_id: parsed.layer_id, filter: parsed.filter ?? null };
  },

  'rpc.ui.map.update_visual_variables': async (params) => {
    const parsed = parseParams(UpdateVisualVariablesSchema, params, 'rpc.ui.map.update_visual_variables');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `update_visual_variables: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.update_visual_variables' },
      );
    }
    const updates: Partial<LayerStyle> = {};
    if (parsed.size_variable !== undefined) {
      updates.sizeVariable = parsed.size_variable
        ? normalizeVisualVariable(layer, parsed.size_variable, 'size_variable')
        : undefined;
    }
    if (parsed.opacity_variable !== undefined) {
      updates.opacityVariable = parsed.opacity_variable
        ? normalizeVisualVariable(layer, parsed.opacity_variable, 'opacity_variable')
        : undefined;
    }
    store.updateLayerStyle(parsed.layer_id, updates);
    const nextLayer = useMapStore.getState().getLayerById(parsed.layer_id);
    return {
      layer_id: parsed.layer_id,
      size_variable: nextLayer?.style.sizeVariable ?? null,
      opacity_variable: nextLayer?.style.opacityVariable ?? null,
    };
  },

  'rpc.ui.map.set_layer_label': async (params) => {
    const parsed = parseParams(SetLayerLabelSchema, params, 'rpc.ui.map.set_layer_label');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_label: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_label' },
      );
    }
    const nextStyle: Partial<LayerStyle> = {};
    if (parsed.visible === false) {
      nextStyle.label = undefined;
      nextStyle.icon = undefined;
    } else {
      const previous = layer.style.label;
      const field = parsed.field ?? previous?.field;
      if (!field) {
        throw RpcError.invalidParams(
          'set_layer_label: field is required when enabling labels',
          { method: 'rpc.ui.map.set_layer_label' },
        );
      }
      nextStyle.label = {
        field,
        fontSize: parsed.font_size ?? previous?.fontSize,
        color: parsed.color ?? previous?.color,
        offset: parsed.offset ?? previous?.offset,
        haloColor: parsed.halo_color ?? previous?.haloColor,
        haloWidth: parsed.halo_width ?? previous?.haloWidth,
      };
      if (parsed.icon !== undefined) nextStyle.icon = parsed.icon;
    }
    store.updateLayerStyle(parsed.layer_id, nextStyle);
    return { layer_id: parsed.layer_id, label: nextStyle.label ?? null, icon: nextStyle.icon ?? layer.style.icon ?? null };
  },

  'rpc.ui.map.highlight_features': async (params) => {
    const parsed = parseParams(HighlightFeaturesSchema, params, 'rpc.ui.map.highlight_features');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    let layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `highlight_features: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.highlight_features' },
      );
    }
    if (layer.data.kind !== 'vector') {
      throw RpcError.invalidParams(
        `highlight_features: layer '${parsed.layer_id}' is not a vector layer`,
        { method: 'rpc.ui.map.highlight_features' },
      );
    }
    layer = await ensureFullVectorLayer(layer);
    if (layer.data.kind !== 'vector') {
      throw RpcError.invalidParams(
        `highlight_features: layer '${parsed.layer_id}' is not a vector layer`,
        { method: 'rpc.ui.map.highlight_features' },
      );
    }

    const filter = parsed.filter ?? {};
    const attrFilters = filter.attribute ?? [];
    const features = resolveVectorGeoJSON(layer.data).features
      .filter((feature) => matchesAttributes(feature, attrFilters as any)) as GeoJSONFeature[];
    const highlightId = `highlight_${parsed.layer_id}`;
    if (features.length === 0) {
      store.removeLayer(highlightId);
      return { layer_id: parsed.layer_id, highlight_layer_id: null, feature_count: 0 };
    }

    const fc = { type: 'FeatureCollection' as const, features };
    const geometryType = detectGeometryType(fc);
    const bbox = computeBBox(fc);
    const style = getDefaultStyle(geometryType);
    style.color = '#f59e0b';
    style.opacity = 0.92;
    style.strokeColor = '#111827';
    style.strokeWidth = Math.max(style.strokeWidth ?? 1, 2);
    style.radius = Math.max(style.radius ?? 5, 7);
    applyPaintToLayerStyle(style, parsed.style?.paint);

    const vector: ParsedVectorData = {
      kind: 'vector',
      geojson: fc,
      geometryType,
      featureCount: features.length,
      bbox,
      crs: layer.data.crs,
      fields: layer.data.fields,
    };
    const definition: MapLayerDefinition = {
      id: highlightId,
      name: parsed.name ?? `${layer.name} highlight`,
      sourceType: 'geojson',
      visible: true,
      style,
      data: vector,
      meta: {
        fileName: `${highlightId}.geojson`,
        extension: '.geojson',
        sourceType: 'geojson',
        fileSize: estimateGeoJSONBytes(fc),
        dynamic: { sourceLayerId: parsed.layer_id, highlight: true },
      },
      addedAt: Date.now(),
    };
    store.addLayer(definition);
    return {
      layer_id: parsed.layer_id,
      highlight_layer_id: highlightId,
      feature_count: features.length,
      bbox: bboxToTuple(bbox),
    };
  },

  'rpc.ui.map.set_layer_order': async (params) => {
    const parsed = parseParams(SetLayerOrderSchema, params, 'rpc.ui.map.set_layer_order');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layers = store.layers;
    const fromIndex = layers.findIndex((layer) => layer.id === parsed.layer_id);
    if (fromIndex < 0) {
      throw RpcError.invalidParams(
        `set_layer_order: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_order' },
      );
    }
    let toIndex = parsed.position === 'bottom' ? 0 : layers.length - 1;
    if (parsed.position === 'above' || parsed.position === 'below') {
      if (!parsed.target_layer_id) {
        throw RpcError.invalidParams(
          'set_layer_order: target_layer_id is required for above/below',
          { method: 'rpc.ui.map.set_layer_order' },
        );
      }
      const targetIndex = layers.findIndex((layer) => layer.id === parsed.target_layer_id);
      if (targetIndex < 0) {
        throw RpcError.invalidParams(
          `set_layer_order: target_layer_id '${parsed.target_layer_id}' not found`,
          { method: 'rpc.ui.map.set_layer_order' },
        );
      }
      toIndex = parsed.position === 'above' ? targetIndex + 1 : targetIndex;
      if (fromIndex < targetIndex && parsed.position === 'above') toIndex -= 1;
      if (fromIndex < targetIndex && parsed.position === 'below') toIndex -= 1;
    }
    toIndex = Math.max(0, Math.min(layers.length - 1, toIndex));
    store.reorderLayers(fromIndex, toIndex);
    return { layer_id: parsed.layer_id, from_index: fromIndex, to_index: toIndex };
  },

  'rpc.ui.map.get_legend_spec': async (params) => {
    const parsed = parseParams(GetLegendSpecSchema, params, 'rpc.ui.map.get_legend_spec');
    await hydrateMapLayersForRpc();
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `get_legend_spec: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.get_legend_spec' },
      );
    }
    return { layer_id: parsed.layer_id, legend: layer.style.legend ?? null };
  },

  'rpc.ui.map.update_legend_spec': async (params) => {
    const parsed = parseParams(UpdateLegendSpecSchema, params, 'rpc.ui.map.update_legend_spec');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    if (!store.getLayerById(parsed.layer_id)) {
      throw RpcError.invalidParams(
        `update_legend_spec: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.update_legend_spec' },
      );
    }
    store.updateLayerStyle(parsed.layer_id, { legend: parsed.legend });
    return { layer_id: parsed.layer_id, legend: parsed.legend };
  },

  'rpc.ui.map.set_layer_visibility': async (params) => {
    const parsed = parseParams(
      SetLayerVisibilitySchema,
      params,
      'rpc.ui.map.set_layer_visibility',
    );
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    if (!store.getLayerById(parsed.layer_id)) {
      throw RpcError.invalidParams(
        `set_layer_visibility: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_visibility' },
      );
    }
    store.setLayerVisibility(parsed.layer_id, parsed.visible);
    return { layer_id: parsed.layer_id, visible: parsed.visible };
  }
};

const NAMED_COLORS: Record<string, string> = {
  red: '#ef4444',
  crimson: '#dc2626',
  orange: '#f97316',
  amber: '#f59e0b',
  yellow: '#eab308',
  green: '#22c55e',
  emerald: '#10b981',
  teal: '#14b8a6',
  cyan: '#06b6d4',
  blue: '#3b82f6',
  indigo: '#6366f1',
  purple: '#8b5cf6',
  violet: '#7c3aed',
  pink: '#ec4899',
  rose: '#f43f5e',
  gray: '#6b7280',
  grey: '#6b7280',
  black: '#111827',
  white: '#ffffff',
  红色: '#ef4444',
  红: '#ef4444',
  橙色: '#f97316',
  橙: '#f97316',
  黄色: '#eab308',
  黄: '#eab308',
  绿色: '#22c55e',
  绿: '#22c55e',
  青色: '#06b6d4',
  青: '#06b6d4',
  蓝色: '#3b82f6',
  蓝: '#3b82f6',
  紫色: '#8b5cf6',
  紫: '#8b5cf6',
  粉色: '#ec4899',
  粉: '#ec4899',
  灰色: '#6b7280',
  灰: '#6b7280',
  黑色: '#111827',
  黑: '#111827',
  白色: '#ffffff',
  白: '#ffffff',
};

function normalizeGraduatedConfig(
  layer: MapLayerDefinition,
  config: GraduatedClassification,
): GraduatedClassification {
  ensureVectorLayer(layer, 'set_layer_renderer: graduated renderer requires a vector layer');
  ensureNumericField(layer, config.field, 'graduated.field');
  const classes = config.method === 'manual' && config.breaks?.length
    ? config.breaks.length + 1
    : Math.max(2, Math.min(12, Math.round(config.classes ?? 5)));
  return {
    ...config,
    classes,
    palette: normalizePalette(config.palette, classes, 'graduated.palette'),
  };
}

function normalizeCategorizedConfig(
  layer: MapLayerDefinition,
  config: CategorizedClassification,
): CategorizedClassification {
  ensureVectorLayer(layer, 'set_layer_renderer: categorized renderer requires a vector layer');
  ensureField(layer, config.field, 'categorized.field');
  const colors: Record<string, string> | undefined = config.colors
    ? Object.fromEntries(
      Object.entries(config.colors).map(([value, color]) => [
        String(value),
        normalizeColor(color, `categorized.colors.${value}`),
      ]),
    )
    : undefined;
  return {
    ...config,
    colors,
    otherColor: normalizeColor(config.otherColor ?? '#9ca3af', 'categorized.otherColor'),
  };
}

function normalizeVisualVariable(
  layer: MapLayerDefinition,
  variable: NumericVisualVariable,
  label: string,
): NumericVisualVariable {
  ensureVectorLayer(layer, `set_layer_renderer: ${label} requires a vector layer`);
  ensureNumericField(layer, variable.field, `${label}.field`);
  const classes = variable.method === 'manual' && variable.breaks?.length
    ? variable.breaks.length + 1
    : Math.max(2, Math.min(12, Math.round(variable.classes ?? variable.values?.length ?? 5)));
  return {
    ...variable,
    classes,
  };
}

function ensureVectorLayer(layer: MapLayerDefinition, message: string): asserts layer is MapLayerDefinition & { data: ParsedVectorData } {
  if (layer.data.kind !== 'vector') {
    throw RpcError.invalidParams(message, { method: 'rpc.ui.map.set_layer_renderer' });
  }
}

function ensureField(layer: MapLayerDefinition, field: string, label: string): void {
  if (layer.data.kind !== 'vector') return;
  const declared = layer.data.fields.some((item) => item.name === field);
  if (declared) return;
  const existsInData = resolveVectorGeoJSON(layer.data).features.some((feature) =>
    Object.prototype.hasOwnProperty.call(feature.properties ?? {}, field),
  );
  if (!existsInData) {
    throw RpcError.invalidParams(
      `set_layer_renderer: ${label} '${field}' does not exist on layer '${layer.id}'`,
      { method: 'rpc.ui.map.set_layer_renderer', layer_id: layer.id, field },
    );
  }
}

function ensureNumericField(layer: MapLayerDefinition, field: string, label: string): void {
  ensureField(layer, field, label);
  if (layer.data.kind !== 'vector') return;
  const declared = layer.data.fields.find((item) => item.name === field);
  const values = resolveVectorGeoJSON(layer.data).features
    .map((feature) => {
      const raw = (feature.properties ?? {})[field];
      return typeof raw === 'number' ? raw : Number(raw);
    })
    .filter(Number.isFinite);
  if (declared?.type !== 'number' && values.length === 0) {
    throw RpcError.invalidParams(
      `set_layer_renderer: ${label} '${field}' is not numeric on layer '${layer.id}'`,
      { method: 'rpc.ui.map.set_layer_renderer', layer_id: layer.id, field },
    );
  }
}

function normalizePalette(
  palette: string[] | undefined,
  classes: number,
  label: string,
): string[] | undefined {
  if (!palette?.length) return undefined;
  const normalized = palette.map((color, index) => normalizeColor(color, `${label}[${index}]`));
  if (normalized.length === classes) return normalized;
  if (normalized.length === 1) return Array.from({ length: classes }, () => normalized[0]);
  return Array.from({ length: classes }, (_, index) => {
    const sourceIndex = Math.round((index / Math.max(classes - 1, 1)) * (normalized.length - 1));
    return normalized[sourceIndex];
  });
}

function normalizeColor(color: string, label: string): string {
  const raw = String(color ?? '').trim();
  const named = NAMED_COLORS[raw] ?? NAMED_COLORS[raw.toLowerCase()];
  if (named) return named;
  if (/^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(raw)) return expandShortHex(raw);
  if (/^(rgb|rgba|hsl|hsla)\(/i.test(raw)) return raw;
  throw RpcError.invalidParams(
    `set_layer_renderer: ${label} '${raw}' is not a supported color. Use hex colors like #8b5cf6 or common color names.`,
    { method: 'rpc.ui.map.set_layer_renderer', color: raw },
  );
}

function expandShortHex(color: string): string {
  if (/^#[0-9a-f]{3}$/i.test(color)) {
    return `#${color[1]}${color[1]}${color[2]}${color[2]}${color[3]}${color[3]}`.toLowerCase();
  }
  return color.toLowerCase();
}
