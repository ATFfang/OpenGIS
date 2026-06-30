import { useAssetStore } from '../assetStore';
import { useScriptStore } from '../scriptStore';
import { useProjectStore } from '../projectStore';

beforeEach(() => {
  useAssetStore.getState().clear();
  useScriptStore.getState().clear();
  useProjectStore.getState().close();
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
