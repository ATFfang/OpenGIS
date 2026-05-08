import { afterEach, beforeEach, vi } from 'vitest';
import { EventBus, type EventTransport } from '../events';
import type { JsonRpcNotification } from '@/types/protocol';

describe('EventBus', () => {
  let captured: JsonRpcNotification[];
  let transport: EventTransport;

  beforeEach(() => {
    captured = [];
    transport = (n) => captured.push(n);
  });

  it('emits layer.added as a JSON-RPC notification', () => {
    const bus = new EventBus({ transport });
    bus.emitLayerAdded({
      layer_id: 'layer_a',
      name: 'x',
      geometry_type: 'Point',
      bbox: [0, 0, 1, 1],
      feature_count: 1,
      crs: 'EPSG:4326',
      added_by: 'user',
    });
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({
      jsonrpc: '2.0',
      method: 'event.layer.added',
      params: { layer_id: 'layer_a' },
    });
    expect('id' in captured[0]).toBe(false);
  });

  it('emits layer.removed / updated / selection.changed / workspace.opened/closed', () => {
    const bus = new EventBus({ transport });
    bus.emitLayerRemoved({ layer_id: 'layer_a' });
    bus.emitLayerUpdated({ layer_id: 'layer_a', changes: { name: 'new' } });
    bus.emitSelectionChanged({ layer_id: 'layer_a', feature_ids: ['1', '2'] });
    bus.emitWorkspaceOpened({
      workspace_path: '/p',
      project_name: 'p',
      layers: [],
      assets: [],
    });
    bus.emitWorkspaceClosed({ workspace_path: '/p' });
    expect(captured.map((x) => x.method)).toEqual([
      'event.layer.removed',
      'event.layer.updated',
      'event.selection.changed',
      'event.workspace.opened',
      'event.workspace.closed',
    ]);
  });

  describe('viewport throttling', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('trailing-edge throttles to once per 200ms with last payload', () => {
      const bus = new EventBus({ transport, viewportThrottleMs: 200 });

      const mkPayload = (zoom: number) => ({
        center: [0, 0] as [number, number],
        zoom,
        bbox: [0, 0, 1, 1] as [number, number, number, number],
        pitch: 0,
        bearing: 0,
      });

      bus.emitViewportChanged(mkPayload(1));
      bus.emitViewportChanged(mkPayload(2));
      bus.emitViewportChanged(mkPayload(3));
      expect(captured).toHaveLength(0); // 还没到 200ms

      vi.advanceTimersByTime(200);
      expect(captured).toHaveLength(1);
      expect((captured[0].params as { zoom: number }).zoom).toBe(3); // 最后一次

      // 下一轮
      bus.emitViewportChanged(mkPayload(9));
      vi.advanceTimersByTime(200);
      expect(captured).toHaveLength(2);
      expect((captured[1].params as { zoom: number }).zoom).toBe(9);
    });
  });

  it('setTransport swaps the transport at runtime', () => {
    const bus = new EventBus({ transport });
    const second: JsonRpcNotification[] = [];
    bus.setTransport((n) => second.push(n));
    bus.emitLayerRemoved({ layer_id: 'l' });
    expect(captured).toHaveLength(0);
    expect(second).toHaveLength(1);
  });
});
