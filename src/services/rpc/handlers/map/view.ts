import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { parseParams } from '../_util';
import { bboxToTuple } from '../_map_util';
import { hydrateMapLayersForRpc, useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { BUILTIN_BASEMAPS } from '@/services/geo';
import { FlyToSchema, SetBasemapSchema, SetBasemapVisibilitySchema, ZoomToBBoxSchema, ZoomToLayerSchema } from '../schemas';

// ─────────────────────────────────────────────────────────────────────
// Basemap 别名表
// ─────────────────────────────────────────────────────────────────────
//
// Python 端（display.py::set_basemap）发来的是 LLM 可读的短名
// ("osm" / "satellite" / "dark" / "light")，但 BUILTIN_BASEMAPS 里的
// 实际 id 是 "osm-streets" / "osm-raster" / "carto-dark" / "carto-light"。
// 做一层别名映射，让两边各自保持各自的习惯。
const BASEMAP_ALIAS: Record<string, string> = {
  osm: 'osm-streets',
  satellite: 'osm-raster',
  dark: 'carto-dark',
  light: 'carto-light',
};

function resolveBasemapId(raw: string): string {
  // 先查别名，没命中当作直接的 BUILTIN id 处理。
  return BASEMAP_ALIAS[raw] ?? raw;
}

export const viewHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.zoom_to_layer': async (params) => {
    const parsed = parseParams(ZoomToLayerSchema, params, 'rpc.ui.map.zoom_to_layer');
    await hydrateMapLayersForRpc();
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `zoom_to_layer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.zoom_to_layer' },
      );
    }
    const bbox = layer.data.bbox;
    if (!bbox) {
      throw RpcError.invalidParams(
        `zoom_to_layer: layer '${parsed.layer_id}' has no bbox (empty or invalid geometry)`,
        { method: 'rpc.ui.map.zoom_to_layer' },
      );
    }
    const tuple = bboxToTuple(bbox);
    mapEngine.fitBounds(tuple, { padding: parsed.padding ?? 40 });
    return { layer_id: parsed.layer_id, bbox: tuple };
  },

  'rpc.ui.map.zoom_to_bbox': (params) => {
    const parsed = parseParams(ZoomToBBoxSchema, params, 'rpc.ui.map.zoom_to_bbox');
    const bbox: [number, number, number, number] = [
      parsed.bbox[0],
      parsed.bbox[1],
      parsed.bbox[2],
      parsed.bbox[3],
    ];
    mapEngine.fitBounds(bbox, { padding: parsed.padding ?? 40 });
    return { bbox };
  },

  'rpc.ui.map.fly_to': (params) => {
    const parsed = parseParams(FlyToSchema, params, 'rpc.ui.map.fly_to');
    const center: [number, number] = [parsed.center[0], parsed.center[1]];
    mapEngine.flyTo(center, parsed.zoom);
    return { center, zoom: parsed.zoom ?? null };
  },

  'rpc.ui.map.set_basemap': (params) => {
    const parsed = parseParams(SetBasemapSchema, params, 'rpc.ui.map.set_basemap');

    if (typeof parsed.basemap === 'string') {
      const targetId = resolveBasemapId(parsed.basemap);
      const basemap = BUILTIN_BASEMAPS.find((b) => b.id === targetId);
      if (!basemap) {
        throw RpcError.invalidParams(
          `Unknown basemap id: ${parsed.basemap} (resolved to ${targetId})`,
          {
            method: 'rpc.ui.map.set_basemap',
            known: BUILTIN_BASEMAPS.map((b) => b.id),
            aliases: Object.keys(BASEMAP_ALIAS),
          },
        );
      }
      useMapStore.getState().setBasemap(basemap);
      return { basemap_id: basemap.id };
    }

    // Custom style_url basemaps need a separate style-loading path.
    // Reject explicitly instead of silently falling back to a built-in basemap.
    throw RpcError.invalidParams(
      'set_basemap: custom { style_url } basemaps are not supported yet',
      { method: 'rpc.ui.map.set_basemap' },
    );
  },

  'rpc.ui.map.set_basemap_visibility': (params) => {
    const parsed = parseParams(
      SetBasemapVisibilitySchema,
      params,
      'rpc.ui.map.set_basemap_visibility',
    );
    const store = useMapStore.getState();
    store.setBasemapVisible(parsed.visible);
    mapEngine.setBasemapVisible(parsed.visible);
    return { visible: parsed.visible };
  }
};
