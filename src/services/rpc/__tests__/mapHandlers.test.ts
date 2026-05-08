/**
 * Stage 3.5：真实现的 7 个 map handler 的契约测试。
 *
 * 目标：证明 handler 跑完 LayerStore 的状态确实发生了变化（add / remove /
 * visibility / style / basemap），以及 camera 类 handler 正确调用了
 * MapEngine（用 vi.spyOn）。
 *
 * 不测：MapLibre 实际渲染 —— vitest 里 MapEngine 没挂 map 实例，方法会
 * no-op，这是期望行为。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Dispatcher } from '../dispatcher';
import { HandlerRegistry } from '../registry';
import { registerAllHandlers } from '../handlers/register';
import type { JsonRpcRequest } from '@/types/protocol';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { BUILTIN_BASEMAPS } from '@/services/geo';

function req(method: string, params: unknown = {}, id = 'r'): JsonRpcRequest {
  return { jsonrpc: '2.0', id, method, params };
}

function makeDispatcher(): Dispatcher {
  const reg = new HandlerRegistry();
  registerAllHandlers(reg);
  return new Dispatcher({ registry: reg });
}

function resetStore(): void {
  useMapStore.setState({
    layers: [],
    activeLayerId: null,
    basemap: BUILTIN_BASEMAPS.find((b) => b.id === 'osm-streets')!,
  });
}

// 一个最小的三点 FeatureCollection，用来喂 add_layer_from_geojson
const SAMPLE_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
      properties: { name: '天安门' },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.5, 40.0] },
      properties: { name: '鸟巢' },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.3, 39.95] },
      properties: { name: '颐和园' },
    },
  ],
} as const;

describe('rpc.ui.map.add_layer_from_geojson', () => {
  beforeEach(resetStore);

  it('adds a FeatureCollection layer and returns bbox + feature_count + geometry_type', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: SAMPLE_FC,
        name: '北京三地标',
        layer_id: 'beijing_landmarks',
        color: '#ff6600',
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'beijing_landmarks',
        feature_count: 3,
        geometry_type: 'Point',
        crs: 'EPSG:4326',
      },
    });
    // bbox 应该是 [minX, minY, maxX, maxY]
    expect((resp as { result: { bbox: [number, number, number, number] } }).result.bbox)
      .toEqual([116.3, 39.9, 116.5, 40.0]);

    // LayerStore 确实写入了
    const layers = useMapStore.getState().layers;
    expect(layers).toHaveLength(1);
    expect(layers[0].id).toBe('beijing_landmarks');
    expect(layers[0].name).toBe('北京三地标');
    expect(layers[0].style.color).toBe('#ff6600');
    expect(layers[0].visible).toBe(true);
    expect(layers[0].data.featureCount).toBe(3);
  });

  it('accepts a bare Feature by wrapping it in a FeatureCollection', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [1, 2] },
          properties: {},
        },
        name: 'single',
      }),
    );

    expect(resp).toMatchObject({ result: { feature_count: 1, geometry_type: 'Point' } });
    expect(useMapStore.getState().layers).toHaveLength(1);
  });

  it('empty FeatureCollection → -32602 invalidParams', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: { type: 'FeatureCollection', features: [] },
        name: 'empty',
      }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
    expect(useMapStore.getState().layers).toHaveLength(0);
  });
});

describe('rpc.ui.map.remove_layer', () => {
  beforeEach(resetStore);

  it('removes an existing layer and reports removed=true', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: SAMPLE_FC,
        name: 'L',
        layer_id: 'L1',
      }),
    );
    expect(useMapStore.getState().layers).toHaveLength(1);

    const resp = await d.handleRequest(req('rpc.ui.map.remove_layer', { layer_id: 'L1' }));
    expect(resp).toMatchObject({ result: { layer_id: 'L1', removed: true } });
    expect(useMapStore.getState().layers).toHaveLength(0);
  });

  it('removing unknown layer_id reports removed=false (no throw)', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.remove_layer', { layer_id: 'ghost' }),
    );
    expect(resp).toMatchObject({ result: { layer_id: 'ghost', removed: false } });
  });
});

describe('rpc.ui.map.zoom_to_bbox', () => {
  beforeEach(resetStore);

  it('delegates to mapEngine.fitBounds with the right tuple', async () => {
    const spy = vi.spyOn(mapEngine, 'fitBounds').mockImplementation(() => {});
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.zoom_to_bbox', { bbox: [0, 0, 10, 10], padding: 80 }),
    );

    expect(spy).toHaveBeenCalledWith([0, 0, 10, 10], { padding: 80 });
    expect(resp).toMatchObject({ result: { bbox: [0, 0, 10, 10] } });
    spy.mockRestore();
  });
});

describe('rpc.ui.map.fly_to', () => {
  beforeEach(resetStore);

  it('delegates to mapEngine.flyTo with center + zoom', async () => {
    const spy = vi.spyOn(mapEngine, 'flyTo').mockImplementation(() => {});
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.fly_to', { center: [116.4, 39.9], zoom: 12 }),
    );

    expect(spy).toHaveBeenCalledWith([116.4, 39.9], 12);
    spy.mockRestore();
  });
});

describe('rpc.ui.map.set_basemap', () => {
  beforeEach(resetStore);

  it.each<[string, string]>([
    ['osm', 'osm-streets'],
    ['satellite', 'osm-raster'],
    ['dark', 'carto-dark'],
    ['light', 'carto-light'],
  ])('resolves alias "%s" → %s', async (alias, resolved) => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_basemap', { basemap: alias }),
    );
    expect(resp).toMatchObject({ result: { basemap_id: resolved } });
    expect(useMapStore.getState().basemap.id).toBe(resolved);
  });

  it('unknown short name → -32602 (rejected by zod enum)', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_basemap', { basemap: 'neptune' }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });

  it('custom style_url is rejected at Stage 3.5', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_basemap', { basemap: { style_url: 'https://example.com/s.json' } }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.set_layer_style', () => {
  beforeEach(resetStore);

  async function addL(d: Dispatcher, id: string): Promise<void> {
    await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: SAMPLE_FC,
        name: id,
        layer_id: id,
      }),
    );
  }

  it('maps fill-color / fill-opacity into the LayerStore color / opacity', async () => {
    const d = makeDispatcher();
    await addL(d, 'L1');

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_style', {
        layer_id: 'L1',
        style: {
          type: 'fill',
          paint: { 'fill-color': '#00ff00', 'fill-opacity': 0.5 },
        },
      }),
    );
    expect(resp).toMatchObject({
      result: { layer_id: 'L1', applied: { color: '#00ff00', opacity: 0.5 } },
    });
    const layer = useMapStore.getState().getLayerById('L1')!;
    expect(layer.style.color).toBe('#00ff00');
    expect(layer.style.opacity).toBe(0.5);
  });

  it('unknown layer_id → -32602', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_style', {
        layer_id: 'ghost',
        style: { type: 'fill', paint: { 'fill-color': '#f00' } },
      }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.set_layer_visibility', () => {
  beforeEach(resetStore);

  it('flips visible on the layer', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: SAMPLE_FC,
        name: 'L',
        layer_id: 'L1',
      }),
    );
    expect(useMapStore.getState().getLayerById('L1')!.visible).toBe(true);

    await d.handleRequest(
      req('rpc.ui.map.set_layer_visibility', { layer_id: 'L1', visible: false }),
    );
    expect(useMapStore.getState().getLayerById('L1')!.visible).toBe(false);
  });

  it('unknown layer_id → -32602', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_visibility', { layer_id: 'ghost', visible: true }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

// ===========================================================================
// Stage 3.7：新补的 5 个 handler
// ===========================================================================

/** 给 list/get/query 系列测试用的小工具，喂一个 3-point layer */
async function seedLayer(d: Dispatcher, id: string, name = id): Promise<void> {
  await d.handleRequest(
    req('rpc.ui.map.add_layer_from_geojson', {
      geojson: SAMPLE_FC,
      name,
      layer_id: id,
    }),
  );
}

describe('rpc.ui.map.list_layers', () => {
  beforeEach(resetStore);

  it('returns empty list + count=0 when no layers', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(req('rpc.ui.map.list_layers', {}));
    expect(resp).toMatchObject({ result: { layers: [], count: 0 } });
  });

  it('returns summarized meta (no features) after add_layer_from_geojson', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1', '北京三地标');
    await seedLayer(d, 'L2', 'second');

    const resp = await d.handleRequest(req('rpc.ui.map.list_layers', {}));
    const result = (resp as { result: { layers: Array<Record<string, unknown>>; count: number } })
      .result;
    expect(result.count).toBe(2);
    expect(result.layers).toHaveLength(2);

    const l1 = result.layers.find((l) => l.layer_id === 'L1')!;
    expect(l1).toMatchObject({
      layer_id: 'L1',
      name: '北京三地标',
      source_type: 'geojson',
      visible: true,
      feature_count: 3,
      geometry_type: 'Point',
      crs: 'EPSG:4326',
    });
    // bbox 是 tuple
    expect(Array.isArray(l1.bbox)).toBe(true);
    expect((l1.bbox as number[]).length).toBe(4);
    // 精简表示不带 features
    expect('features' in l1).toBe(false);
  });
});

describe('rpc.ui.map.get_layer', () => {
  beforeEach(resetStore);

  it('returns meta for an existing layer', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');
    const resp = await d.handleRequest(req('rpc.ui.map.get_layer', { layer_id: 'L1' }));
    expect(resp).toMatchObject({
      result: {
        layer_id: 'L1',
        feature_count: 3,
        geometry_type: 'Point',
        source_type: 'geojson',
      },
    });
  });

  it('unknown layer_id → -32602', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(req('rpc.ui.map.get_layer', { layer_id: 'ghost' }));
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.zoom_to_layer', () => {
  beforeEach(resetStore);

  it('fetches bbox from LayerStore and calls mapEngine.fitBounds', async () => {
    const spy = vi.spyOn(mapEngine, 'fitBounds').mockImplementation(() => {});
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    const resp = await d.handleRequest(
      req('rpc.ui.map.zoom_to_layer', { layer_id: 'L1', padding: 60 }),
    );

    expect(spy).toHaveBeenCalledTimes(1);
    const [bboxArg, opts] = spy.mock.calls[0];
    expect(bboxArg).toEqual([116.3, 39.9, 116.5, 40.0]);
    expect(opts).toMatchObject({ padding: 60 });
    expect(resp).toMatchObject({
      result: { layer_id: 'L1', bbox: [116.3, 39.9, 116.5, 40.0] },
    });
    spy.mockRestore();
  });

  it('uses default padding=40 when not provided', async () => {
    const spy = vi.spyOn(mapEngine, 'fitBounds').mockImplementation(() => {});
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    await d.handleRequest(req('rpc.ui.map.zoom_to_layer', { layer_id: 'L1' }));
    expect(spy.mock.calls[0][1]).toMatchObject({ padding: 40 });
    spy.mockRestore();
  });

  it('unknown layer_id → -32602', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.zoom_to_layer', { layer_id: 'ghost' }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.query_features', () => {
  beforeEach(resetStore);

  it('returns all features when no filter', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');
    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', { layer_id: 'L1' }),
    );
    const result = (resp as { result: { total_matched: number; truncated: boolean; features: unknown[] } })
      .result;
    expect(result.total_matched).toBe(3);
    expect(result.features).toHaveLength(3);
    expect(result.truncated).toBe(false);
  });

  it('filters by attribute equality', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', {
        layer_id: 'L1',
        filter: { attribute: [{ field: 'name', op: '=', value: '天安门' }] },
      }),
    );
    const result = (resp as { result: { total_matched: number; features: Array<{ properties: Record<string, unknown> }> } })
      .result;
    expect(result.total_matched).toBe(1);
    expect(result.features[0].properties.name).toBe('天安门');
  });

  it('filters by attribute contains', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', {
        layer_id: 'L1',
        // "天" 匹配"天安门"
        filter: { attribute: [{ field: 'name', op: 'contains', value: '天' }] },
      }),
    );
    const result = (resp as { result: { total_matched: number } }).result;
    expect(result.total_matched).toBe(1);
  });

  it('filters by bbox (only features intersecting the query bbox)', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    // 只覆盖 [116.35, 39.8, 116.55, 40.1] —— 天安门 (116.4,39.9) + 鸟巢 (116.5,40.0) 命中
    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', {
        layer_id: 'L1',
        filter: { bbox: [116.35, 39.8, 116.55, 40.1] },
      }),
    );
    const result = (resp as { result: { total_matched: number } }).result;
    expect(result.total_matched).toBe(2);
  });

  it('filters by point (point falls inside feature bbox)', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    // 对 Point feature，"点落在 bbox 内" == "点等于该点"
    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', {
        layer_id: 'L1',
        filter: { point: [116.4, 39.9] },
      }),
    );
    const result = (resp as { result: { total_matched: number; features: Array<{ properties: Record<string, unknown> }> } })
      .result;
    expect(result.total_matched).toBe(1);
    expect(result.features[0].properties.name).toBe('天安门');
  });

  it('respects limit and sets truncated=true when clipped', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'L1');

    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', { layer_id: 'L1', limit: 2 }),
    );
    const result = (resp as { result: { total_matched: number; truncated: boolean } }).result;
    expect(result.total_matched).toBe(2);
    expect(result.truncated).toBe(true);
  });

  it('unknown layer_id → -32602', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.query_features', { layer_id: 'ghost' }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.add_layer', () => {
  beforeEach(() => {
    resetStore();
    // @ts-expect-error mock global electronAPI
    delete globalThis.window;
  });

  it('throws internal error when electronAPI is unavailable', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer', { path: 'C:/tmp/x.geojson' }),
    );
    // 不在 renderer 里 → -32603 internal
    expect(resp).toMatchObject({ error: { code: -32603 } });
  });

  it('loads a GeoJSON file via electronAPI.readFile and adds to LayerStore', async () => {
    const fileContent = JSON.stringify(SAMPLE_FC);
    const readFile = vi.fn().mockResolvedValue({ success: true, content: fileContent });
    // 伪造 Electron renderer 环境
    (globalThis as unknown as { window: { electronAPI: { readFile: typeof readFile } } }).window = {
      electronAPI: { readFile },
    };

    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer', { path: 'E:/data/landmarks.geojson' }),
    );

    expect(readFile).toHaveBeenCalledWith('E:/data/landmarks.geojson');
    const result = (resp as {
      result: { layer_id: string; bbox: number[]; feature_count: number; geometry_type: string };
    }).result;
    expect(result.feature_count).toBe(3);
    expect(result.geometry_type).toBe('Point');
    expect(result.bbox).toEqual([116.3, 39.9, 116.5, 40.0]);

    // LayerStore 确实写入了
    const layers = useMapStore.getState().layers;
    expect(layers).toHaveLength(1);
    expect(layers[0].name).toBe('landmarks');

    // @ts-expect-error clean up
    delete globalThis.window;
  });

  it('rejects unsupported extensions (e.g. .shp)', async () => {
    const readFile = vi.fn().mockResolvedValue({ success: true, content: 'irrelevant' });
    (globalThis as unknown as { window: { electronAPI: { readFile: typeof readFile } } }).window = {
      electronAPI: { readFile },
    };

    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer', { path: 'E:/data/roads.shp' }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });

    // @ts-expect-error clean up
    delete globalThis.window;
  });

  it('surfaces readFile failure as -32602 invalidParams', async () => {
    const readFile = vi.fn().mockResolvedValue({ success: false, error: 'ENOENT' });
    (globalThis as unknown as { window: { electronAPI: { readFile: typeof readFile } } }).window = {
      electronAPI: { readFile },
    };

    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer', { path: 'E:/data/missing.geojson' }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });

    // @ts-expect-error clean up
    delete globalThis.window;
  });
});

describe('rpc.ui.map.add_raster_from_url', () => {
  beforeEach(resetStore);

  it('returns layer_id + bbox + source for xyz tile', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_raster_from_url', {
        url: 'https://example.com/{z}/{x}/{y}.png',
        name: 'tiles',
        tile_type: 'xyz',
      }),
    );
    expect(resp).toMatchObject({
      result: {
        layer_id: expect.stringMatching(/^raster_/),
        source: 'tile-xyz',
        bbox: [-180, -85.05, 180, 85.05],
      },
    });
  });

  it('cog tile_type still notImplemented', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_raster_from_url', {
        url: 'https://example.com/cog.tif',
        name: 'cog',
        tile_type: 'cog',
      }),
    );
    expect(resp).toMatchObject({ error: { code: -32603 } });
  });
});
