import { Dispatcher } from '../dispatcher';
import { RpcError } from '../errors';
import { HandlerRegistry } from '../registry';
import type { JsonRpcRequest } from '@/types/protocol';

function makeReq(method: string, params: unknown = {}, id = 'req-1'): JsonRpcRequest {
  return { jsonrpc: '2.0', id, method, params };
}

describe('Dispatcher', () => {
  it('routes request to registered handler and wraps result', async () => {
    const reg = new HandlerRegistry();
    reg.register('rpc.echo', (params) => ({ got: params }));
    const d = new Dispatcher({ registry: reg });

    const resp = await d.handleRequest(makeReq('rpc.echo', { n: 1 }));
    expect(resp).toMatchObject({
      jsonrpc: '2.0',
      id: 'req-1',
      result: { got: { n: 1 } },
    });
  });

  it('returns METHOD_NOT_FOUND for unknown method', async () => {
    const d = new Dispatcher({ registry: new HandlerRegistry() });
    const resp = await d.handleRequest(makeReq('rpc.unknown'));
    expect(resp).toMatchObject({
      id: 'req-1',
      error: { code: -32601 },
    });
  });

  it('wraps RpcError thrown in handler into error response', async () => {
    const reg = new HandlerRegistry();
    reg.register('rpc.bad', () => {
      throw RpcError.invalidParams('nope');
    });
    const d = new Dispatcher({ registry: reg });
    const resp = await d.handleRequest(makeReq('rpc.bad'));
    expect(resp).toMatchObject({ error: { code: -32602, message: 'nope' } });
  });

  it('converts unknown exceptions into INTERNAL_ERROR', async () => {
    const reg = new HandlerRegistry();
    reg.register('rpc.boom', () => {
      throw new Error('kaboom');
    });
    const d = new Dispatcher({ registry: reg });
    const resp = await d.handleRequest(makeReq('rpc.boom'));
    expect(resp).toMatchObject({ error: { code: -32603 } });
    if ('error' in resp && resp.error) {
      expect(resp.error.message).toContain('kaboom');
    }
  });

  it('rejects malformed request with INVALID_REQUEST', async () => {
    const d = new Dispatcher({ registry: new HandlerRegistry() });
    // 故意给 jsonrpc 错的值
    const resp = await d.handleRequest({
      // @ts-expect-error -- testing malformed
      jsonrpc: '1.0',
      id: 'x',
      method: 'm',
      params: {},
    });
    expect(resp).toMatchObject({ error: { code: -32600 } });
  });

  it('enforces per-method timeout (TIMEOUT = -32000)', async () => {
    const reg = new HandlerRegistry();
    reg.register('rpc.slow', () => new Promise(() => { /* never resolves */ }));
    const d = new Dispatcher({
      registry: reg,
      methodTimeouts: { 'rpc.slow': 20 },
    });
    const resp = await d.handleRequest(makeReq('rpc.slow'));
    expect(resp).toMatchObject({ error: { code: -32000 } });
  });

  it('handleNotification does NOT return response and swallows errors via onError hook', async () => {
    const reg = new HandlerRegistry();
    const calls: unknown[] = [];
    reg.register('event.x', (p) => calls.push(p));
    reg.register('event.broken', () => {
      throw new Error('x');
    });
    const errors: Array<[string, unknown]> = [];
    const d = new Dispatcher({
      registry: reg,
      onError: (m, e) => errors.push([m, e]),
    });

    await d.handleNotification({ jsonrpc: '2.0', method: 'event.x', params: { a: 1 } });
    expect(calls).toEqual([{ a: 1 }]);

    await d.handleNotification({ jsonrpc: '2.0', method: 'event.broken', params: {} });
    expect(errors.length).toBe(1);
    expect(errors[0][0]).toBe('event.broken');
  });

  it('dispatch auto-routes by presence of id', async () => {
    const reg = new HandlerRegistry();
    reg.register('rpc.a', () => 1);
    reg.register('event.b', () => 1);
    const d = new Dispatcher({ registry: reg });

    const r = await d.dispatch({ jsonrpc: '2.0', id: '1', method: 'rpc.a', params: {} });
    expect(r).not.toBeNull();
    expect(r).toMatchObject({ result: 1 });

    const n = await d.dispatch({ jsonrpc: '2.0', method: 'event.b', params: {} });
    expect(n).toBeNull();
  });

  it('channelOf returns correct prefix channel', () => {
    expect(Dispatcher.channelOf('rpc.ui.map.add_layer')).toBe('rpc');
    expect(Dispatcher.channelOf('chat.token')).toBe('chat');
    expect(Dispatcher.channelOf('event.layer.added')).toBe('event');
    expect(Dispatcher.channelOf('weird.thing')).toBe(null);
  });
});
