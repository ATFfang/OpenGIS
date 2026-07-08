/**
 * Stage 3.4 contract tests: PythonClient ↔ Dispatcher wiring.
 *
 * We bypass WebSocket by constructing a PythonClient instance and driving
 * ``_handleMessage`` directly through a test shim. The goal is to nail
 * down the four inbound-message branches:
 *
 *   1. Response with matching id → resolves the pending request.
 *   2. Request (id + routable method prefix) with a dispatcher wired →
 *      dispatcher.handleRequest is invoked and the response is written
 *      back to the ws.
 *   3. Notification (no id) on a canonical channel → routed to
 *      dispatcher.handleNotification (Stage 3.8 fix).
 *   4. Notification on any channel → also fanned out to
 *      `notificationHandlers` for legacy store subscribers.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type {
  JsonRpcNotification,
  JsonRpcRequest,
  JsonRpcResponse,
} from '@/types/protocol';
import { PythonClient, type DispatcherLike } from '../pythonClient';

/** Access the private ``_handleMessage`` without going through the real socket. */
function feed(client: PythonClient, msg: unknown): void {
  // Deliberately reach into the private method — this mirrors the real
  // onmessage path (parse + handleMessage) without opening a WebSocket.
  (client as unknown as { _handleMessage(d: unknown): void })._handleMessage(msg);
}

/** Stub a minimal ws-like object into the private ``ws`` field. */
function stubOpenWs(client: PythonClient): { sent: string[] } {
  const sent: string[] = [];
  const ws = {
    readyState: 1, // WebSocket.OPEN
    send: (data: string) => sent.push(data),
  };
  (client as unknown as { ws: unknown }).ws = ws;
  return { sent };
}

/**
 * Build a DispatcherLike stub with both methods present. Tests override
 * whichever one they're asserting on.
 */
function stubDispatcher(
  overrides: Partial<DispatcherLike> = {},
): DispatcherLike & {
  handleRequest: ReturnType<typeof vi.fn>;
  handleNotification: ReturnType<typeof vi.fn>;
} {
  return {
    handleRequest: vi.fn(
      async (req: JsonRpcRequest): Promise<JsonRpcResponse> => ({
        jsonrpc: '2.0',
        id: req.id,
        result: { ok: true },
      }),
    ),
    handleNotification: vi.fn(async () => {}),
    ...overrides,
  } as DispatcherLike & {
    handleRequest: ReturnType<typeof vi.fn>;
    handleNotification: ReturnType<typeof vi.fn>;
  };
}

describe('PythonClient inbound message routing (Stage 3.4)', () => {
  let client: PythonClient;

  beforeEach(() => {
    client = new PythonClient();
  });

  it('dispatches rpc.* requests to the wired dispatcher and sends the response back', async () => {
    const { sent } = stubOpenWs(client);
    const dispatcher = stubDispatcher({
      handleRequest: vi.fn(
        async (req: JsonRpcRequest): Promise<JsonRpcResponse> => ({
          jsonrpc: '2.0',
          id: req.id,
          result: { echoed: req.method },
        }),
      ),
    });
    client.setDispatcher(dispatcher);

    feed(client, {
      jsonrpc: '2.0',
      id: 'req-1',
      method: 'rpc.ui.map.add_layer',
      params: { path: 'x.geojson' },
    });

    // Dispatcher is awaited asynchronously → let micro-tasks flush.
    await Promise.resolve();
    await Promise.resolve();

    expect(dispatcher.handleRequest).toHaveBeenCalledTimes(1);
    expect(dispatcher.handleRequest.mock.calls[0][0]).toMatchObject({
      jsonrpc: '2.0',
      id: 'req-1',
      method: 'rpc.ui.map.add_layer',
      params: { path: 'x.geojson' },
    });
    expect(sent).toHaveLength(1);
    const sentResp = JSON.parse(sent[0]);
    expect(sentResp).toMatchObject({
      jsonrpc: '2.0',
      id: 'req-1',
      result: { echoed: 'rpc.ui.map.add_layer' },
    });
  });

  it('dispatches chat.* requests as well (all three channels route)', async () => {
    const { sent } = stubOpenWs(client);
    const dispatcher = stubDispatcher();
    client.setDispatcher(dispatcher);

    feed(client, {
      jsonrpc: '2.0',
      id: 'req-2',
      method: 'chat.user_message',
      params: { message: 'hi' },
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(dispatcher.handleRequest).toHaveBeenCalledTimes(1);
    expect(sent).toHaveLength(1);
  });

  it('does NOT dispatch when no dispatcher is wired (falls through to notifications)', async () => {
    const { sent } = stubOpenWs(client);
    const notifSpy = vi.fn();
    client.onNotification(notifSpy);

    // Without a dispatcher, even a request-shaped message with a routable
    // prefix falls through. The fallback branch treats it as a notification.
    feed(client, {
      jsonrpc: '2.0',
      id: 'orphan-1',
      method: 'rpc.ui.map.add_layer',
      params: {},
    });

    await Promise.resolve();
    expect(sent).toHaveLength(0); // nothing written back — client is not responsible
    expect(notifSpy).toHaveBeenCalledWith('rpc.ui.map.add_layer', {});
  });

  it('falls through to notification handlers for non-routable methods even with a dispatcher', async () => {
    stubOpenWs(client);
    const dispatcher = stubDispatcher();
    client.setDispatcher(dispatcher);
    const notifSpy = vi.fn();
    client.onNotification(notifSpy);

    // "map.addLayer" has no rpc./chat./event. prefix → should NOT be
    // dispatched. Kept as a smoke case because Python may still emit
    // non-v3 notifications during development (e.g. progress/debug).
    // Legacy notification handlers still see it.
    feed(client, {
      jsonrpc: '2.0',
      method: 'map.addLayer',
      params: { layer_id: 'l1' },
    });

    await Promise.resolve();
    expect(dispatcher.handleRequest).not.toHaveBeenCalled();
    expect(dispatcher.handleNotification).not.toHaveBeenCalled();
    expect(notifSpy).toHaveBeenCalledWith('map.addLayer', { layer_id: 'l1' });
  });

  it('sends an internal error response if dispatcher throws unexpectedly', async () => {
    const { sent } = stubOpenWs(client);
    const dispatcher = stubDispatcher({
      handleRequest: vi.fn(async () => {
        throw new Error('boom');
      }),
    });
    client.setDispatcher(dispatcher);

    feed(client, {
      jsonrpc: '2.0',
      id: 'req-err',
      method: 'rpc.agent.hello',
      params: {},
    });

    // Wait for the promise rejection handler to run.
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));

    expect(sent).toHaveLength(1);
    const resp = JSON.parse(sent[0]);
    expect(resp).toMatchObject({
      jsonrpc: '2.0',
      id: 'req-err',
      error: { code: -32603 },
    });
    expect(resp.error.message).toContain('boom');
  });

  it('setDispatcher(null) disconnects the wiring', async () => {
    const { sent } = stubOpenWs(client);
    const dispatcher = stubDispatcher();
    client.setDispatcher(dispatcher);
    client.setDispatcher(null);

    feed(client, {
      jsonrpc: '2.0',
      id: 'req-n',
      method: 'rpc.ui.map.add_layer',
      params: {},
    });

    await Promise.resolve();
    expect(dispatcher.handleRequest).not.toHaveBeenCalled();
    expect(sent).toHaveLength(0);
  });

  // ── Stage 3.8: notification routing (the bug we just fixed) ──────────

  it('routes rpc.* notifications (no id) to dispatcher.handleNotification', async () => {
    stubOpenWs(client);
    const dispatcher = stubDispatcher();
    client.setDispatcher(dispatcher);

    feed(client, {
      jsonrpc: '2.0',
      method: 'rpc.ui.map.add_layer_from_geojson',
      params: { name: 'demo', geojson: { type: 'FeatureCollection', features: [] } },
    });

    await Promise.resolve();
    await Promise.resolve();

    expect(dispatcher.handleNotification).toHaveBeenCalledTimes(1);
    const call = (dispatcher.handleNotification.mock.calls[0][0] as JsonRpcNotification);
    expect(call).toMatchObject({
      jsonrpc: '2.0',
      method: 'rpc.ui.map.add_layer_from_geojson',
    });
    expect(call.params).toMatchObject({ name: 'demo' });
    // Notification → never send a response back over the ws.
    expect(dispatcher.handleRequest).not.toHaveBeenCalled();
  });

  it('still fans out canonical notifications to notificationHandlers (both sinks fire)', async () => {
    stubOpenWs(client);
    const dispatcher = stubDispatcher();
    client.setDispatcher(dispatcher);
    const notifSpy = vi.fn();
    client.onNotification(notifSpy);

    feed(client, {
      jsonrpc: '2.0',
      method: 'chat.stream_delta',
      params: { delta: 'hi' },
    });

    await Promise.resolve();
    expect(dispatcher.handleNotification).toHaveBeenCalledTimes(1);
    expect(notifSpy).toHaveBeenCalledWith('chat.stream_delta', { delta: 'hi' });
  });

  it('does not call handleNotification when no dispatcher is wired', async () => {
    stubOpenWs(client);
    const notifSpy = vi.fn();
    client.onNotification(notifSpy);

    feed(client, {
      jsonrpc: '2.0',
      method: 'rpc.ui.map.add_layer_from_geojson',
      params: {},
    });

    await Promise.resolve();
    // No throw, and the legacy handler still sees the message.
    expect(notifSpy).toHaveBeenCalledWith('rpc.ui.map.add_layer_from_geojson', {});
  });

  it('swallows dispatcher.handleNotification rejections without breaking the pump', async () => {
    stubOpenWs(client);
    const dispatcher = stubDispatcher({
      handleNotification: vi.fn(async () => {
        throw new Error('handler blew up');
      }),
    });
    client.setDispatcher(dispatcher);
    const notifSpy = vi.fn();
    client.onNotification(notifSpy);

    // Capture the defensive console.error from the rejection-path.
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.remove_layer',
        params: { layer_id: 'l1' },
      });
      await Promise.resolve();
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));

      // Fan-out must still run even if the dispatcher side rejected.
      expect(notifSpy).toHaveBeenCalledWith('rpc.ui.map.remove_layer', { layer_id: 'l1' });
      expect(errSpy).toHaveBeenCalled();
    } finally {
      errSpy.mockRestore();
    }
  });

  it('coalesces repeated dynamic full frames by layer_id before dispatching', async () => {
    vi.useFakeTimers();
    try {
      stubOpenWs(client);
      const dispatcher = stubDispatcher();
      client.setDispatcher(dispatcher);
      const notifSpy = vi.fn();
      client.onNotification(notifSpy);

      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'full', geojson: { type: 'FeatureCollection', features: [] }, sequence: 1 },
      });
      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'full', geojson: { type: 'FeatureCollection', features: [] }, sequence: 2 },
      });
      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'full', geojson: { type: 'FeatureCollection', features: [] }, sequence: 3 },
      });

      expect(dispatcher.handleNotification).not.toHaveBeenCalled();
      vi.advanceTimersByTime(100);
      await Promise.resolve();
      await Promise.resolve();

      expect(dispatcher.handleNotification).toHaveBeenCalledTimes(1);
      expect(dispatcher.handleNotification.mock.calls[0][0]).toMatchObject({
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', sequence: 3 },
      });
      expect(notifSpy).toHaveBeenCalledTimes(1);
      expect(notifSpy).toHaveBeenCalledWith(
        'rpc.ui.map.dynamic_layer_update',
        { layer_id: 'live', mode: 'full', geojson: { type: 'FeatureCollection', features: [] }, sequence: 3 },
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('preserves full plus diff dynamic layer bursts in order', async () => {
    vi.useFakeTimers();
    try {
      stubOpenWs(client);
      const dispatcher = stubDispatcher();
      client.setDispatcher(dispatcher);

      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'full', geojson: { type: 'FeatureCollection', features: [] }, sequence: 1 },
      });
      feed(client, {
        jsonrpc: '2.0',
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'diff', diff: { add: [] }, sequence: 2 },
      });

      vi.advanceTimersByTime(100);
      await Promise.resolve();
      await Promise.resolve();

      expect(dispatcher.handleNotification).toHaveBeenCalledTimes(2);
      expect(dispatcher.handleNotification.mock.calls[0][0]).toMatchObject({
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'full', sequence: 1 },
      });
      expect(dispatcher.handleNotification.mock.calls[1][0]).toMatchObject({
        method: 'rpc.ui.map.dynamic_layer_update',
        params: { layer_id: 'live', mode: 'diff', sequence: 2 },
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
