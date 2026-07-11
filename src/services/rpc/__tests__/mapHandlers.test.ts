/**
 * Map handler contract tests.
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
import { useAssetStore } from '@/stores/assetStore';
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
  ;(globalThis as any).window ??= {}
  useAssetStore.setState({ workspacePath: null });
  useMapStore.setState({
    layers: [],
    activeLayerId: null,
    basemap: BUILTIN_BASEMAPS.find((b) => b.id === 'osm-streets')!,
    basemapVisible: true,
    viewState: {
      center: [116.4, 39.9],
      zoom: 4,
      bearing: 0,
      pitch: 0,
    },
  });
}

// 一个最小的三点 FeatureCollection，用来喂 add_layer_from_geojson
const SAMPLE_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
      properties: { name: '天安门', score: 1, comment_count: 10, rating: 4.6 },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.5, 40.0] },
      properties: { name: '鸟巢', score: 2, comment_count: 30, rating: 4.8 },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.3, 39.95] },
      properties: { name: '颐和园', score: 3, comment_count: 20, rating: 4.7 },
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
    expect(layers[0].data.kind).toBe('vector');
    if (layers[0].data.kind !== 'vector') throw new Error('expected vector layer');
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

  it('does not let structural OSM recursion nodes turn a line layer into points', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        name: 'roads',
        geojson: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: {
                type: 'LineString',
                coordinates: [[121.0, 31.0], [121.1, 31.1]],
              },
              properties: { highway: 'residential', _osm_id: 1, _osm_type: 'way' },
            },
            ...Array.from({ length: 4 }, (_, index) => ({
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [121 + index * 0.01, 31] },
              properties: { _osm_id: 100 + index, _osm_type: 'node' },
            })),
          ],
        },
      }),
    );

    expect(resp).toMatchObject({ result: { feature_count: 5, geometry_type: 'LineString' } });
    const layer = useMapStore.getState().layers[0];
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector layer');
    expect(layer.data.geometryType).toBe('LineString');
    expect(layer.style.renderType).toBe('line');
  });

  it('applies agent style paint while adding a layer', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.add_layer_from_geojson', {
        geojson: SAMPLE_FC,
        name: 'styled',
        layer_id: 'styled_points',
        style: {
          type: 'circle',
          paint: {
            'circle-color': '#3366ff',
            'circle-radius': 9,
            'circle-opacity': 0.7,
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
            'circle-stroke-opacity': 0.8,
          },
        },
      }),
    );

    const layer = useMapStore.getState().getLayerById('styled_points')!;
    expect(layer.style).toMatchObject({
      color: '#3366ff',
      radius: 9,
      opacity: 0.7,
      strokeColor: '#ffffff',
      strokeWidth: 2,
      strokeOpacity: 0.8,
    });
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

describe('rpc.ui.map.dynamic_layer_update', () => {
  beforeEach(resetStore);

  it('upserts a worker-driven layer and keeps dynamic metadata runtime-only', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        worker_id: 'worker_1',
        worker_name: 'ticker',
        sequence: 1,
        style: {
          type: 'circle',
          paint: { 'circle-color': '#22c55e', 'circle-radius': 7 },
        },
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        feature_count: 3,
        geometry_type: 'Point',
        sequence: 1,
      },
    });
    const layer = useMapStore.getState().getLayerById('live_points');
    expect(layer?.meta.dynamic).toMatchObject({
      workerId: 'worker_1',
      workerName: 'ticker',
      sequence: 1,
    });
    expect(layer?.style.color).toBe('#22c55e');
    expect(layer?.style.radius).toBe(7);
  });

  it('normalizes worker shorthand paint keys on dynamic updates', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        sequence: 1,
        style: {
          type: 'circle',
          'circle-color': '#ef4444',
          'circle-radius': 11,
          'circle-opacity': 0.65,
          'circle-stroke-color': '#111827',
          'circle-stroke-width': 2,
        },
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        feature_count: 3,
        geometry_type: 'Point',
      },
    });
    const layer = useMapStore.getState().getLayerById('live_points');
    expect(layer?.style.color).toBe('#ef4444');
    expect(layer?.style.radius).toBe(11);
    expect(layer?.style.opacity).toBe(0.65);
    expect(layer?.style.strokeColor).toBe('#111827');
    expect(layer?.style.strokeWidth).toBe(2);
  });

  it('keeps data-driven paint expressions from worker dynamic styles', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        sequence: 1,
        style: {
          type: 'circle',
          'circle-color': ['get', 'color'],
          'circle-radius': 9,
        },
      }),
    );

    const layer = useMapStore.getState().getLayerById('live_points');
    expect(layer?.style.color).toEqual(['get', 'color']);
    expect(layer?.style.radius).toBe(9);
  });

  it('immediately syncs dynamic updates to the live map source when the map is ready', async () => {
    const d = makeDispatcher();
    const triggerRepaint = vi.fn();
    const getMap = vi.spyOn(mapEngine, 'getMap').mockReturnValue({
      isStyleLoaded: () => true,
      triggerRepaint,
    } as any);
    const syncLayer = vi.spyOn(mapEngine, 'syncLayer').mockImplementation(() => {});
    const setLayerVisibility = vi.spyOn(mapEngine, 'setLayerVisibility').mockImplementation(() => {});
    const updateLayerPaint = vi.spyOn(mapEngine, 'updateLayerPaint').mockImplementation(() => {});

    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        sequence: 1,
      }),
    );

    expect(syncLayer).toHaveBeenCalledWith(expect.objectContaining({ id: 'live_points' }));
    expect(setLayerVisibility).toHaveBeenCalledWith('live_points', true);
    expect(updateLayerPaint).toHaveBeenCalledWith(expect.objectContaining({ id: 'live_points' }));
    expect(triggerRepaint).toHaveBeenCalled();

    getMap.mockRestore();
    syncLayer.mockRestore();
    setLayerVisibility.mockRestore();
    updateLayerPaint.mockRestore();
  });

  it('skips stale sequence frames', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        sequence: 2,
      }),
    );
    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: {
          type: 'FeatureCollection',
          features: [],
        },
        sequence: 1,
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        skipped: true,
        reason: 'stale_sequence',
      },
    });
    expect(useMapStore.getState().getLayerById('live_points')?.data.kind).toBe('vector');
    const data = useMapStore.getState().getLayerById('live_points')?.data as any;
    expect(data.featureCount).toBe(3);
  });

  it('accepts lower sequence after the worker process generation changes', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        worker_id: 'worker_1',
        worker_started_at: 100,
        sequence: 203,
      }),
    );

    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              id: 'fresh',
              geometry: { type: 'Point', coordinates: [121.5, 31.2] },
              properties: { status: 'restarted' },
            },
          ],
        },
        worker_id: 'worker_1',
        worker_started_at: 200,
        sequence: 1,
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        feature_count: 1,
        sequence: 1,
      },
    });
    const layer = useMapStore.getState().getLayerById('live_points')!;
    expect(layer.meta.dynamic?.workerStartedAt).toBe(200);
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector');
    expect(layer.data.geojson.features[0].id).toBe('fresh');
  });

  it('skips updates from another workspace', async () => {
    useAssetStore.setState({ workspacePath: '/workspace/current' });
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: SAMPLE_FC,
        workspace_path: '/workspace/other',
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        skipped: true,
        reason: 'workspace_mismatch',
      },
    });
    expect(useMapStore.getState().layers).toHaveLength(0);
  });

  it('applies diff updates against stable feature ids', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'full',
        layer_id: 'live_points',
        name: 'Live Points',
        geojson: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              id: 'a',
              geometry: { type: 'Point', coordinates: [116.4, 39.9] },
              properties: { value: 1, keep: true },
            },
            {
              type: 'Feature',
              id: 'b',
              geometry: { type: 'Point', coordinates: [116.5, 40.0] },
              properties: { value: 2 },
            },
          ],
        },
        sequence: 1,
      }),
    );

    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'diff',
        layer_id: 'live_points',
        diff: {
          remove: ['b'],
          add: [
            {
              type: 'Feature',
              id: 'c',
              geometry: { type: 'Point', coordinates: [116.6, 40.1] },
              properties: { value: 3 },
            },
          ],
          update: [
            {
              id: 'a',
              newGeometry: { type: 'Point', coordinates: [117, 41] },
              removeProperties: ['keep'],
              addOrUpdateProperties: [{ key: 'value', value: 10 }],
            },
          ],
        },
        sequence: 2,
        schema_changed: false,
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_points',
        feature_count: 2,
        mode: 'diff',
        updateable: true,
      },
    });
    const layer = useMapStore.getState().getLayerById('live_points')!;
    expect(layer.meta.dynamic?.mode).toBe('diff');
    expect(layer.meta.dynamic?.updateable).toBe(true);
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector');
    expect(layer.data.runtimeDiff).toMatchObject({
      remove: ['b'],
    });
    expect(layer.data.geojson.features.map((f) => f.id).sort()).toEqual(['a', 'c']);
    const updated = layer.data.geojson.features.find((f) => f.id === 'a')!;
    expect(updated.geometry.coordinates).toEqual([117, 41]);
    expect(updated.properties).toEqual({ value: 10 });
  });

  it('accepts full GeoJSON Features in diff.update using properties.id', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'full',
        layer_id: 'live_vehicles',
        name: 'Live Vehicles',
        geojson: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [121.47, 31.23] },
              properties: { id: 'ind_0099', speed: 12, status: 'old' },
            },
          ],
        },
        sequence: 1,
      }),
    );

    const resp = await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'diff',
        layer_id: 'live_vehicles',
        diff: {
          update: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [121.5, 31.25] },
              properties: { id: 'ind_0099', speed: 18, status: 'moving' },
            },
          ],
        },
        sequence: 2,
        schema_changed: false,
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'live_vehicles',
        feature_count: 1,
        mode: 'diff',
        updateable: true,
      },
    });
    const layer = useMapStore.getState().getLayerById('live_vehicles')!;
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector');
    expect(layer.data.geojson.features).toHaveLength(1);
    expect(layer.data.geojson.features[0].id).toBe('ind_0099');
    expect(layer.data.geojson.features[0].geometry.coordinates).toEqual([121.5, 31.25]);
    expect(layer.data.geojson.features[0].properties).toEqual({
      id: 'ind_0099',
      speed: 18,
      status: 'moving',
    });
    expect(layer.data.runtimeDiff?.update?.[0]).toMatchObject({
      id: 'ind_0099',
      newGeometry: { type: 'Point', coordinates: [121.5, 31.25] },
      removeAllProperties: true,
    });
  });

  it('upserts missing full GeoJSON Features from diff.update as adds', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'full',
        layer_id: 'live_vehicles',
        name: 'Live Vehicles',
        geojson: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [121.47, 31.23] },
              properties: { id: 'ind_0001', speed: 8 },
            },
          ],
        },
        sequence: 1,
      }),
    );

    await d.handleRequest(
      req('rpc.ui.map.dynamic_layer_update', {
        mode: 'diff',
        layer_id: 'live_vehicles',
        diff: {
          update: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [121.51, 31.26] },
              properties: { id: 'ind_0002', speed: 21 },
            },
          ],
        },
        sequence: 2,
        schema_changed: false,
      }),
    );

    const layer = useMapStore.getState().getLayerById('live_vehicles')!;
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector');
    expect(layer.data.geojson.features.map((feature) => feature.id).sort()).toEqual(['ind_0001', 'ind_0002']);
    expect(layer.data.runtimeDiff?.add?.[0]).toMatchObject({
      id: 'ind_0002',
      geometry: { type: 'Point', coordinates: [121.51, 31.26] },
    });
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

  it('delegates to mapEngine.flyTo with center, zoom, pitch and bearing', async () => {
    const spy = vi.spyOn(mapEngine, 'flyTo').mockImplementation(() => {});
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.fly_to', {
        center: [116.4, 39.9],
        zoom: 12,
        pitch: 55,
        bearing: -20,
        duration: 700,
      }),
    );

    expect(spy).toHaveBeenCalledWith([116.4, 39.9], 12, {
      pitch: 55,
      bearing: -20,
      duration: 700,
    });
    expect(useMapStore.getState().viewState).toMatchObject({
      center: [116.4, 39.9],
      zoom: 12,
      pitch: 55,
      bearing: -20,
    });
    spy.mockRestore();
  });
});

describe('rpc.ui.map.set_camera', () => {
  beforeEach(resetStore);

  it('updates partial camera fields without requiring a center', async () => {
    const spy = vi.spyOn(mapEngine, 'setCamera').mockImplementation(() => {});
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_camera', { pitch: 60, bearing: -25, duration: 800 }),
    );

    expect(spy).toHaveBeenCalledWith({
      center: [116.4, 39.9],
      zoom: 4,
      pitch: 60,
      bearing: -25,
      duration: 800,
    });
    expect(resp).toMatchObject({
      result: {
        center: [116.4, 39.9],
        zoom: 4,
        pitch: 60,
        bearing: -25,
      },
    });
    expect(useMapStore.getState().viewState.pitch).toBe(60);
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

  it('custom style_url is rejected explicitly', async () => {
    const d = makeDispatcher();
    const resp = await d.handleRequest(
      req('rpc.ui.map.set_basemap', { basemap: { style_url: 'https://example.com/s.json' } }),
    );
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});

describe('rpc.ui.map.set_basemap_visibility', () => {
  beforeEach(resetStore);

  it('toggles basemap visibility in the store', async () => {
    const spy = vi.spyOn(mapEngine, 'setBasemapVisible').mockImplementation(() => {});
    const d = makeDispatcher();

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_basemap_visibility', { visible: false }),
    );

    expect(resp).toMatchObject({ result: { visible: false } });
    expect(useMapStore.getState().basemapVisible).toBe(false);
    expect(spy).toHaveBeenCalledWith(false);
    spy.mockRestore();
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

  it('maps fill-color / fill-opacity into polygon fill style without changing outline opacity', async () => {
    const d = makeDispatcher();
    await addL(d, 'L1');
    useMapStore.getState().updateLayerStyle('L1', { opacity: 0.9, strokeOpacity: 0.8 });

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
      result: { layer_id: 'L1', applied: { color: '#00ff00', fillOpacity: 0.5 } },
    });
    const layer = useMapStore.getState().getLayerById('L1')!;
    expect(layer.style.color).toBe('#00ff00');
    expect(layer.style.fillOpacity).toBe(0.5);
    expect(layer.style.opacity).toBe(0.9);
    expect(layer.style.strokeOpacity).toBe(0.8);
  });

  it('maps line and stroke paint without overwriting polygon fill color', async () => {
    const d = makeDispatcher();
    await addL(d, 'L1');
    const before = useMapStore.getState().getLayerById('L1')!.style.color;

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_style', {
        layer_id: 'L1',
        style: {
          type: 'fill',
          paint: {
            'line-color': '#112233',
            'line-width': 4,
            'line-opacity': 0.55,
          },
        },
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'L1',
        applied: { strokeColor: '#112233', strokeWidth: 4, strokeOpacity: 0.55 },
      },
    });
    const layer = useMapStore.getState().getLayerById('L1')!;
    expect(layer.style.color).toBe(before);
    expect(layer.style.strokeColor).toBe('#112233');
    expect(layer.style.strokeWidth).toBe(4);
    expect(layer.style.strokeOpacity).toBe(0.55);
  });

  it('maps line-dasharray into layer style', async () => {
    const d = makeDispatcher();
    await addL(d, 'L1');

    await d.handleRequest(
      req('rpc.ui.map.set_layer_style', {
        layer_id: 'L1',
        style: {
          type: 'line',
          paint: { 'line-dasharray': [2, 3] },
        },
      }),
    );

    expect(useMapStore.getState().getLayerById('L1')!.style.lineDasharray).toEqual([2, 3]);
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

describe('rpc.ui.map semantic layer styling', () => {
  beforeEach(resetStore);

  it('stores categorized style with explicit categories and colors', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_renderer', {
        layer_id: 'pois',
        renderer: 'categorized',
        categorized: {
          field: 'name',
          maxCategories: 3,
          categories: ['鸟巢', '天安门'],
          colors: { 鸟巢: '#22c55e', 天安门: '#ef4444' },
          otherColor: '#64748b',
        },
      }),
    );

    expect(resp).toMatchObject({ result: { layer_id: 'pois', renderer: 'categorized' } });
    expect(useMapStore.getState().getLayerById('pois')!.style).toMatchObject({
      renderType: 'categorized',
      categorized: {
        field: 'name',
        maxCategories: 3,
        categories: ['鸟巢', '天安门'],
        colors: { 鸟巢: '#22c55e', 天安门: '#ef4444' },
        otherColor: '#64748b',
      },
    });
  });

  it('stores graduated manual breaks and palette', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    await d.handleRequest(
      req('rpc.ui.map.set_layer_renderer', {
        layer_id: 'pois',
        renderer: 'graduated',
        graduated: {
          field: 'score',
          method: 'manual',
          classes: 4,
          breaks: [1, 2, 3],
          palette: ['#fee5d9', '#fcae91', '#fb6a4a', '#cb181d'],
        },
      }),
    );

    expect(useMapStore.getState().getLayerById('pois')!.style.graduated).toEqual({
      field: 'score',
      method: 'manual',
      classes: 4,
      breaks: [1, 2, 3],
      palette: ['#fee5d9', '#fcae91', '#fb6a4a', '#cb181d'],
    });
  });

  it('stores extrusion renderer and returns extrusion config', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'buildings');

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_layer_renderer', {
        layer_id: 'buildings',
        renderer: 'extrusion',
        extrusion: {
          heightField: 'score',
          heightMultiplier: 2,
        },
      }),
    );

    expect(resp).toMatchObject({
      result: {
        layer_id: 'buildings',
        renderer: 'extrusion',
        extrusion: {
          heightField: 'score',
          heightMultiplier: 2,
        },
      },
    });
    expect(useMapStore.getState().getLayerById('buildings')!.style).toMatchObject({
      renderType: 'extrusion',
      extrusion: {
        heightField: 'score',
        heightMultiplier: 2,
      },
    });
  });

  it('normalizes semantic color names and rejects invalid classification fields', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    const categorized = await d.handleRequest(
      req('rpc.ui.map.set_layer_renderer', {
        layer_id: 'pois',
        renderer: 'categorized',
        categorized: {
          field: 'name',
          colors: { 天安门: '紫色', 鸟巢: 'purple' },
          categories: ['天安门', '鸟巢'],
          otherColor: '灰色',
        },
      }),
    );

    expect(categorized).toMatchObject({
      result: {
        categorized: {
          colors: { 天安门: '#8b5cf6', 鸟巢: '#8b5cf6' },
          otherColor: '#6b7280',
        },
      },
    });
    expect(useMapStore.getState().getLayerById('pois')!.style.categorized?.colors).toEqual({
      天安门: '#8b5cf6',
      鸟巢: '#8b5cf6',
    });

    const badField = await d.handleRequest(
      req('rpc.ui.map.set_layer_renderer', {
        layer_id: 'pois',
        renderer: 'graduated',
        graduated: {
          field: 'missing_score',
          method: 'quantile',
          classes: 5,
          palette: ['紫色'],
        },
      }),
    );
    expect(badField).toMatchObject({ error: { code: -32602 } });
  });

  it('stores display filters and labels without changing renderer', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    await d.handleRequest(
      req('rpc.ui.map.set_layer_filter', {
        layer_id: 'pois',
        filter: { attribute: [{ field: 'name', op: 'in', value: ['鸟巢', '天安门'] }] },
      }),
    );
    await d.handleRequest(
      req('rpc.ui.map.set_layer_label', {
        layer_id: 'pois',
        field: 'name',
        font_size: 13,
        color: '#111827',
        halo_color: '#ffffff',
        halo_width: 2,
        offset: [0, 1],
      }),
    );

    const style = useMapStore.getState().getLayerById('pois')!.style;
    expect(style.renderType).toBe('circle');
    expect(style.filter).toEqual({
      attribute: [{ field: 'name', op: 'in', value: ['鸟巢', '天安门'] }],
    });
    expect(style.label).toEqual({
      field: 'name',
      fontSize: 13,
      color: '#111827',
      haloColor: '#ffffff',
      haloWidth: 2,
      offset: [0, 1],
    });
  });

  it('stores field-driven visual variables and exposes them in layer summaries', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    const updated = await d.handleRequest(
      req('rpc.ui.map.update_visual_variables', {
        layer_id: 'pois',
        size_variable: {
          field: 'comment_count',
          method: 'equal_interval',
          classes: 4,
          range: [4, 18],
        },
        opacity_variable: {
          field: 'rating',
          method: 'quantile',
          classes: 5,
          values: [0.25, 0.4, 0.55, 0.7, 0.9],
        },
        sort_variable: {
          field: 'comment_count',
          order: 'descending',
        },
      }),
    );

    expect(updated).toMatchObject({
      result: {
        layer_id: 'pois',
        size_variable: {
          field: 'comment_count',
          method: 'equal-interval',
          classes: 4,
          range: [4, 18],
        },
        opacity_variable: {
          field: 'rating',
          method: 'quantile',
          classes: 5,
          values: [0.25, 0.4, 0.55, 0.7, 0.9],
        },
        sort_variable: {
          field: 'comment_count',
          order: 'descending',
        },
      },
    });

    const listed = await d.handleRequest(req('rpc.ui.map.list_layers', {}));
    const layer = (listed as { result: { layers: Array<any> } }).result.layers[0];
    expect(layer.style.size_variable).toMatchObject({ field: 'comment_count', range: [4, 18] });
    expect(layer.style.opacity_variable).toMatchObject({ field: 'rating', classes: 5 });
    expect(layer.style.sort_variable).toMatchObject({ field: 'comment_count', order: 'descending' });

    await d.handleRequest(
      req('rpc.ui.map.update_visual_variables', {
        layer_id: 'pois',
        size_variable: null,
        sort_variable: null,
      }),
    );
    expect(useMapStore.getState().getLayerById('pois')!.style.sizeVariable).toBeUndefined();
    expect(useMapStore.getState().getLayerById('pois')!.style.sortVariable).toBeUndefined();
  });

  it('creates and clears highlight overlay layers from filters', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'pois');

    const highlighted = await d.handleRequest(
      req('rpc.ui.map.highlight_features', {
        layer_id: 'pois',
        filter: { attribute: [{ field: 'name', op: 'contains', value: '天' }] },
        name: 'matched pois',
      }),
    );

    expect(highlighted).toMatchObject({
      result: {
        layer_id: 'pois',
        highlight_layer_id: 'highlight_pois',
        feature_count: 1,
      },
    });
    expect(useMapStore.getState().getLayerById('highlight_pois')?.meta.dynamic).toMatchObject({
      sourceLayerId: 'pois',
      highlight: true,
    });

    await d.handleRequest(
      req('rpc.ui.map.highlight_features', {
        layer_id: 'pois',
        filter: { attribute: [{ field: 'name', op: '=', value: '不存在' }] },
      }),
    );
    expect(useMapStore.getState().getLayerById('highlight_pois')).toBeUndefined();
  });

  it('moves layers and stores legend metadata', async () => {
    const d = makeDispatcher();
    await seedLayer(d, 'bottom');
    await seedLayer(d, 'middle');
    await seedLayer(d, 'top');

    const moved = await d.handleRequest(
      req('rpc.ui.map.set_layer_order', {
        layer_id: 'bottom',
        position: 'above',
        target_layer_id: 'top',
      }),
    );
    expect(moved).toMatchObject({ result: { layer_id: 'bottom', from_index: 0, to_index: 2 } });
    expect(useMapStore.getState().layers.map((layer) => layer.id)).toEqual(['middle', 'top', 'bottom']);

    await d.handleRequest(
      req('rpc.ui.map.update_legend_spec', {
        layer_id: 'bottom',
        legend: {
          title: 'POI 类型',
          labels: { cafe: '咖啡', tea: '茶饮' },
          order: ['tea', 'cafe'],
          visible: true,
        },
      }),
    );
    const legend = await d.handleRequest(req('rpc.ui.map.get_legend_spec', { layer_id: 'bottom' }));
    expect(legend).toMatchObject({
      result: {
        layer_id: 'bottom',
        legend: {
          title: 'POI 类型',
          labels: { cafe: '咖啡', tea: '茶饮' },
          order: ['tea', 'cafe'],
          visible: true,
        },
      },
    });
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

// ─────────────────────────────────────────────────────────────────────
// Query/read handler coverage
// ─────────────────────────────────────────────────────────────────────

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

  it('stores a sample for >20MB file-backed vectors while query_features resolves full data', async () => {
    const features = Array.from({ length: 6000 }, (_, index) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [index % 180, index % 80] },
      properties: { index },
    }));
    const fileContent = JSON.stringify({ type: 'FeatureCollection', features });
    const readFile = vi.fn();
    const readFileAsBuffer = vi.fn().mockResolvedValue({
      success: true,
      buffer: new TextEncoder().encode(fileContent).buffer,
    });
    const getFileInfo = vi.fn().mockResolvedValue({
      success: true,
      info: { size: 21 * 1024 * 1024 },
    });
    (globalThis as unknown as {
      window: { electronAPI: { readFile: typeof readFile; readFileAsBuffer: typeof readFileAsBuffer; getFileInfo: typeof getFileInfo } }
    }).window = {
      electronAPI: { readFile, readFileAsBuffer, getFileInfo },
    };

    const d = makeDispatcher();
    const addResp = await d.handleRequest(
      req('rpc.ui.map.add_layer', { path: 'E:/data/big.geojson' }),
    );
    const layerId = (addResp as { result: { layer_id: string } }).result.layer_id;

    expect(readFile).not.toHaveBeenCalled();
    expect(readFileAsBuffer).toHaveBeenCalledWith('E:/data/big.geojson');
    const layer = useMapStore.getState().getLayerById(layerId)!;
    expect(layer.data.kind).toBe('vector');
    if (layer.data.kind !== 'vector') throw new Error('expected vector layer');
    expect(layer.data.sampled).toBe(true);
    expect(layer.data.geojson.features).toHaveLength(5000);
    expect(layer.data.featureCount).toBe(6000);

    const queryResp = await d.handleRequest(
      req('rpc.ui.map.query_features', { layer_id: layerId, limit: 6000 }),
    );
    expect((queryResp as { result: { total_matched: number } }).result.total_matched).toBe(6000);

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

  it('keeps source-value custom stops when updating backend tile raster style', async () => {
    const d = makeDispatcher();
    await d.handleRequest(
      req('rpc.ui.map.add_raster_from_url', {
        url: 'http://127.0.0.1:8765/api/rasters/rst_test/tiles/{z}/{x}/{y}.png?rev=0',
        name: 'kde',
        tile_type: 'xyz',
        raster_id: 'rst_test',
        raster_style: { ramp: 'magma', opacity: 0.7 },
        raster_info: {
          width: 256,
          height: 256,
          band_count: 1,
          band_stats: [{ min: 0, max: 5.79e-10, p2: 0, p98: 5.79e-10 }],
        },
      }),
    );
    const layerId = useMapStore.getState().layers[0].id;
    const stops = [
      { value: 0, color: '#000000', opacity: 0 },
      { value: 1e-10, color: '#ff9900', opacity: 0.5 },
      { value: 5.79e-10, color: '#ff0000', opacity: 0.9 },
    ];
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        style_revision: 2,
        style: { ramp: 'custom', stops, stopsUnit: 'source', opacity: 0.8 },
        info: { band_stats: [{ min: 0, max: 5.79e-10, p2: 0, p98: 5.79e-10 }] },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const resp = await d.handleRequest(
      req('rpc.ui.map.set_raster_style', {
        layer_id: layerId,
        raster: {
          stops,
          stopsUnit: 'source',
          opacity: 0.8,
        },
      }),
    );

    expect(resp).toMatchObject({ result: { layer_id: layerId } });
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8765/api/rasters/rst_test/style',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"stopsUnit":"source"'),
      }),
    );
    expect(useMapStore.getState().getLayerById(layerId)?.style.raster).toMatchObject({
      stopsUnit: 'source',
      opacity: 0.8,
    });
    vi.unstubAllGlobals();
  });
});
