import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { newLayerId } from '../../idGen';
import { parseParams } from '../_util';
import { bboxToTuple, computeBBox, detectGeometryType, normalizeToFeatureCollection } from '../_map_util';
import { useMapStore, hydrateMapLayersForRpc } from '@/stores/mapStore';
import { getDefaultStyle, makeHandledVectorData, resolveVectorGeoJSON, shouldHandleLayer, type DataSourceMeta, type GeoJSONFeature, type MapLayerDefinition, type ParsedVectorData } from '@/services/geo';
import { parseCSV, parseGeoJSON, extractFields } from '@/services/geo/parsers';
import { AddLayerFromGeoJsonSchema, AddLayerSchema, GetLayerSchema, ListLayersSchema, QueryFeaturesSchema, RemoveLayerSchema } from '../schemas';
import { applyPaintToLayerStyle, basenameWithoutExt, byteLength, ensureFullVectorLayer, estimateGeoJSONBytes, featureContainsPoint, featureIntersectsBBox, getExtensionLower, getFileInfo, matchesAttributes, readTextFileForMapLayer, summarizeLayer } from './shared';

export const layerHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.add_layer': async (params) => {
    const parsed = parseParams(AddLayerSchema, params, 'rpc.ui.map.add_layer');
    const filePath = parsed.path;
    const ext = getExtensionLower(filePath);

    const api = (globalThis as any).window?.electronAPI;
    if (!api?.readFile) {
      throw RpcError.internal(
        'add_layer: electronAPI.readFile is unavailable (not running in Electron renderer)',
        { method: 'rpc.ui.map.add_layer' },
      );
    }

    const fileInfo = await getFileInfo(api, filePath);
    let text: string;
    try {
      text = await readTextFileForMapLayer(api, filePath, fileInfo.size);
    } catch (err) {
      throw RpcError.invalidParams(
        `add_layer: failed to read file '${filePath}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_layer', path: filePath },
      );
    }

    let vector: ParsedVectorData;
    const displayName = parsed.name ?? basenameWithoutExt(filePath);
    try {
      if (ext === '.geojson' || ext === '.json') {
        vector = parseGeoJSON(text, displayName);
      } else if (ext === '.csv' || ext === '.tsv') {
        vector = parseCSV(text, displayName);
      } else {
        throw RpcError.invalidParams(
          `add_layer: unsupported extension '${ext}'. Supported: .geojson .json .csv .tsv. ` +
            `For other formats, convert to GeoJSON and use add_layer_from_geojson.`,
          { method: 'rpc.ui.map.add_layer', path: filePath },
        );
      }
    } catch (err) {
      if (err instanceof RpcError) throw err;
      throw RpcError.invalidParams(
        `add_layer: failed to parse '${filePath}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_layer', path: filePath },
      );
    }

    const layerId = newLayerId();
    const fileSize = fileInfo.size ?? byteLength(text);
    if (shouldHandleLayer(fileSize)) {
      vector = makeHandledVectorData(vector, {
        handleId: `vector:${layerId}`,
        sizeBytes: fileSize,
      });
    }
    const meta: DataSourceMeta = {
      fileName: filePath.split(/[\\/]/).pop() || displayName,
      extension: ext,
      sourceType: ext === '.csv' || ext === '.tsv' ? 'csv' : 'geojson',
      fileSize,
      filePath,
    };
    const style = getDefaultStyle(vector.geometryType);
    applyPaintToLayerStyle(style, parsed.style?.paint);

    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: meta.sourceType,
      visible: parsed.visible ?? true,
      style,
      data: vector,
      meta,
      addedAt: Date.now(),
    };

    useMapStore.getState().addLayer(definition);

    return {
      layer_id: layerId,
      name: displayName,
      bbox: bboxToTuple(vector.bbox),
      feature_count: vector.featureCount,
      geometry_type: vector.geometryType,
      crs: vector.crs,
      fields: vector.fields.map((f) => ({ name: f.name, type: f.type })),
    };
  },

  'rpc.ui.map.list_layers': async (params) => {
    parseParams(ListLayersSchema, params, 'rpc.ui.map.list_layers');
    await hydrateMapLayersForRpc();
    const layers = useMapStore.getState().layers;
    return {
      layers: layers.map(summarizeLayer),
      count: layers.length,
    };
  },

  'rpc.ui.map.get_layer': async (params) => {
    const parsed = parseParams(GetLayerSchema, params, 'rpc.ui.map.get_layer');
    await hydrateMapLayersForRpc();
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `get_layer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.get_layer' },
      );
    }
    return summarizeLayer(layer);
  },

  'rpc.ui.map.query_features': async (params) => {
    const parsed = parseParams(QueryFeaturesSchema, params, 'rpc.ui.map.query_features');
    await hydrateMapLayersForRpc();
    let layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `query_features: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.query_features' },
      );
    }
    if (layer.data.kind !== 'vector') {
      throw RpcError.invalidParams(
        `query_features: layer '${parsed.layer_id}' is not a vector layer`,
        { method: 'rpc.ui.map.query_features' },
      );
    }
    layer = await ensureFullVectorLayer(layer);
    if (layer.data.kind !== 'vector') {
      throw RpcError.invalidParams(
        `query_features: layer '${parsed.layer_id}' is not a vector layer`,
        { method: 'rpc.ui.map.query_features' },
      );
    }

    const allFeatures = resolveVectorGeoJSON(layer.data).features;
    const filter = parsed.filter ?? {};
    const attrFilters = filter.attribute ?? [];
    const bboxFilter = filter.bbox;
    const pointFilter = filter.point;
    const limit = parsed.limit ?? 1000;

    const matched: GeoJSONFeature[] = [];
    for (const f of allFeatures) {
      if (!matchesAttributes(f, attrFilters)) continue;
      if (bboxFilter && !featureIntersectsBBox(f, bboxFilter)) continue;
      if (pointFilter && !featureContainsPoint(f, pointFilter)) continue;
      matched.push(f);
      if (matched.length >= limit) break;
    }

    return {
      layer_id: parsed.layer_id,
      total_matched: matched.length,
      truncated: allFeatures.length > matched.length && matched.length === limit,
      features: matched,
    };
  },

  'rpc.ui.map.add_layer_from_geojson': (params) => {
    const parsed = parseParams(
      AddLayerFromGeoJsonSchema,
      params,
      'rpc.ui.map.add_layer_from_geojson',
    );
    const extras = (params ?? {}) as Record<string, unknown>;

    const fc = normalizeToFeatureCollection(parsed.geojson);
    if (!fc) {
      throw RpcError.invalidParams(
        'add_layer_from_geojson: geojson is not a FeatureCollection / Feature / bare geometry',
        { method: 'rpc.ui.map.add_layer_from_geojson' },
      );
    }
    if (fc.features.length === 0) {
      throw RpcError.invalidParams(
        'add_layer_from_geojson: geojson has no features',
        { method: 'rpc.ui.map.add_layer_from_geojson' },
      );
    }

    const geometryType = detectGeometryType(fc);
    const bbox = computeBBox(fc);
    const displayName = parsed.name;
    // id 必须内容无关且唯一：同名图层多轮生成不应互相覆盖，
    // 并行 subagent 同时造层也不会因 hash 碰撞而丢失。
    // Python 端会显式带上 layer_id（uuid），这里只是兜底。
    const layerId =
      typeof extras.layer_id === 'string' && extras.layer_id
        ? (extras.layer_id as string)
        : newLayerId();

    const inlineSize = estimateGeoJSONBytes(fc);
    let data: ParsedVectorData = {
      kind: 'vector',
      geojson: fc,
      geometryType,
      featureCount: fc.features.length,
      bbox,
      crs: 'EPSG:4326',
      fields: extractFields(fc),
    };
    if (shouldHandleLayer(inlineSize)) {
      data = makeHandledVectorData(data, {
        handleId: `vector:${layerId}`,
        sizeBytes: inlineSize,
      });
    }

    // Agent 造的图层不落文件，仍造一个虚 meta 让 LayerPanel/AssetExplorer
    // 的 `l.meta.fileName` 守卫不 trip。
    const meta: DataSourceMeta = {
      fileName: `${displayName}.geojson`,
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: inlineSize,
    };

    const style = getDefaultStyle(geometryType);
    applyPaintToLayerStyle(style, parsed.style?.paint);
    if (!parsed.style?.paint) {
      if (typeof extras.color === 'string' && extras.color) {
        style.color = extras.color as string;
      }
      if (typeof extras.opacity === 'number') {
        style.opacity = extras.opacity as number;
      }
    }

    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: 'geojson',
      visible: parsed.visible ?? true,
      style,
      data,
      meta,
      addedAt: Date.now(),
    };

    useMapStore.getState().addLayer(definition);

    return {
      layer_id: layerId,
      bbox: bboxToTuple(bbox),
      feature_count: fc.features.length,
      geometry_type: geometryType,
      crs: 'EPSG:4326',
      fields: data.fields.map((f) => ({ name: f.name, type: f.type })),
    };
  },

  'rpc.ui.map.remove_layer': async (params) => {
    const parsed = parseParams(RemoveLayerSchema, params, 'rpc.ui.map.remove_layer');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const exists = !!store.getLayerById(parsed.layer_id);
    store.removeLayer(parsed.layer_id);
    return { layer_id: parsed.layer_id, removed: exists };
  }
};
