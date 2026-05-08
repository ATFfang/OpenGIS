import { useLayerStore } from '../layerStore';
import { useAssetStore } from '../assetStore';
import { useScriptStore } from '../scriptStore';
import { useProjectStore } from '../projectStore';

beforeEach(() => {
  useLayerStore.getState().clear();
  useAssetStore.getState().clear();
  useScriptStore.getState().clear();
  useProjectStore.getState().close();
});

describe('LayerStore', () => {
  it('add generates layer_id with correct prefix and stores the layer', () => {
    const layer = useLayerStore.getState().add({
      name: 'points',
      geometry_type: 'Point',
      bbox: [0, 0, 1, 1],
      feature_count: 42,
      crs: 'EPSG:4326',
      source: 'memory',
      added_by: 'user',
    });
    expect(layer.layer_id).toMatch(/^layer_/);
    expect(layer.visible).toBe(true);
    expect(useLayerStore.getState().get(layer.layer_id)).toEqual(layer);
  });

  it('list returns layers in insertion order', () => {
    const store = useLayerStore.getState();
    const a = store.add({
      name: 'a',
      geometry_type: 'Point',
      bbox: [0, 0, 1, 1],
      feature_count: 1,
      crs: 'EPSG:4326',
      source: 'memory',
      added_by: 'user',
    });
    const b = store.add({
      name: 'b',
      geometry_type: 'Polygon',
      bbox: [0, 0, 2, 2],
      feature_count: 2,
      crs: 'EPSG:4326',
      source: 'memory',
      added_by: 'agent',
    });
    expect(useLayerStore.getState().list().map((l) => l.layer_id)).toEqual([
      a.layer_id,
      b.layer_id,
    ]);
  });

  it('remove returns true/false correctly and purges from order', () => {
    const store = useLayerStore.getState();
    const l = store.add({
      name: 'x',
      geometry_type: 'Point',
      bbox: [0, 0, 1, 1],
      feature_count: 0,
      crs: 'EPSG:4326',
      source: 'memory',
      added_by: 'user',
    });
    expect(useLayerStore.getState().remove(l.layer_id)).toBe(true);
    expect(useLayerStore.getState().remove(l.layer_id)).toBe(false);
    expect(useLayerStore.getState().list()).toEqual([]);
  });

  it('setVisibility / setStyle mutate through update()', () => {
    const store = useLayerStore.getState();
    const l = store.add({
      name: 'x',
      geometry_type: 'Point',
      bbox: [0, 0, 1, 1],
      feature_count: 0,
      crs: 'EPSG:4326',
      source: 'memory',
      added_by: 'user',
    });
    expect(useLayerStore.getState().setVisibility(l.layer_id, false)).toBe(true);
    expect(useLayerStore.getState().get(l.layer_id)?.visible).toBe(false);

    useLayerStore.getState().setStyle(l.layer_id, { type: 'circle', paint: { 'circle-radius': 4 } });
    expect(useLayerStore.getState().get(l.layer_id)?.style?.type).toBe('circle');
  });
});

describe('AssetStore', () => {
  it('register generates asset_id and is idempotent by absolute_path', () => {
    const s = useAssetStore.getState();
    const a = s.register({
      path: 'data/a.shp',
      absolute_path: 'E:/proj/data/a.shp',
      format: 'shp',
      size: 1024,
    });
    expect(a.asset_id).toMatch(/^asset_/);

    const a2 = useAssetStore.getState().register({
      path: 'data/a.shp',
      absolute_path: 'E:/proj/data/a.shp',
      format: 'shp',
      size: 2048, // 不同 size 不会建新 asset
    });
    expect(a2.asset_id).toBe(a.asset_id);
  });

  it('findByPath hits the reverse index', () => {
    const s = useAssetStore.getState();
    const a = s.register({
      path: 'x.geojson',
      absolute_path: '/abs/x.geojson',
      format: 'geojson',
      size: 10,
    });
    expect(useAssetStore.getState().findByPath('/abs/x.geojson')?.asset_id).toBe(a.asset_id);
  });

  it('list filters by format case-insensitively', () => {
    const s = useAssetStore.getState();
    s.register({ path: 'a.shp', absolute_path: '/a.shp', format: 'shp', size: 1 });
    s.register({ path: 'b.geojson', absolute_path: '/b.geojson', format: 'GeoJSON', size: 1 });
    expect(useAssetStore.getState().list('SHP').length).toBe(1);
    expect(useAssetStore.getState().list('geojson').length).toBe(1);
    expect(useAssetStore.getState().list().length).toBe(2);
  });
});

describe('ScriptStore', () => {
  it('create → finish transitions status and records duration', () => {
    const s = useScriptStore.getState();
    const sc = s.create({
      run_id: 'run_x',
      step: 0,
      script_path: '/runs/run_x/step_0.py',
      code: 'print(1)',
    });
    expect(sc.script_id).toMatch(/^script_/);
    expect(sc.status).toBe('pending');

    const done = useScriptStore.getState().finish(sc.script_id, {
      status: 'ok',
      duration_ms: 12,
      stdout_summary: '1',
    });
    expect(done?.status).toBe('ok');
    expect(done?.duration_ms).toBe(12);
    expect(done?.finished_at).toBeTypeOf('number');
  });

  it('listByRun returns scripts sorted by step', () => {
    const s = useScriptStore.getState();
    s.create({ run_id: 'run_x', step: 2, script_path: '/2', code: '' });
    s.create({ run_id: 'run_x', step: 0, script_path: '/0', code: '' });
    s.create({ run_id: 'run_y', step: 0, script_path: '/y', code: '' });
    expect(useScriptStore.getState().listByRun('run_x').map((x) => x.step)).toEqual([0, 2]);
    expect(useScriptStore.getState().listByRun('run_y').length).toBe(1);
  });
});

describe('ProjectStore', () => {
  it('open sets current and close returns path', () => {
    const s = useProjectStore.getState();
    const p = s.open({
      workspace_path: 'E:/proj',
      opengis_dir: 'E:/proj/.opengis',
      project_name: 'proj',
    });
    expect(p.opened_at).toBeTypeOf('number');
    expect(useProjectStore.getState().isOpen()).toBe(true);

    const closed = useProjectStore.getState().close();
    expect(closed).toBe('E:/proj');
    expect(useProjectStore.getState().isOpen()).toBe(false);
  });

  it('updateHead writes head_commit when a project is open', () => {
    useProjectStore.getState().open({
      workspace_path: 'E:/p',
      opengis_dir: 'E:/p/.opengis',
      project_name: 'p',
    });
    useProjectStore.getState().updateHead('abc123');
    expect(useProjectStore.getState().get()?.head_commit).toBe('abc123');
  });

  it('updateHead on closed project is a no-op', () => {
    // 关着就不应 crash
    useProjectStore.getState().updateHead('deadbeef');
    expect(useProjectStore.getState().get()).toBe(null);
  });
});
