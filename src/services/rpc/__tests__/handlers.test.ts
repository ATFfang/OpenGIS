/**
 * Handler 骨架的契约测试：
 *   - 注册齐 28 个 method
 *   - 合法参数 → -32603 notImplemented（证明参数校验通过，落到 stub 抛错）
 *   - 非法参数 → -32602 invalidParams
 */

import { Dispatcher } from '../dispatcher';
import { HandlerRegistry } from '../registry';
import { listAllMethods, registerAllHandlers } from '../handlers/register';
import type { JsonRpcRequest } from '@/types/protocol';

function req(method: string, params: unknown = {}, id = 'r'): JsonRpcRequest {
  return { jsonrpc: '2.0', id, method, params };
}

describe('Handler registration', () => {
  it('registers expected method count (17 map + 3 chat + 4 ask + 3 fs + 1 agent.hello = 28 RPC UI-facing)', () => {
    const reg = new HandlerRegistry();
    const names = registerAllHandlers(reg);
    // 17 map (含 add_image_overlay) + 3 chat + 4 ask + 3 fs + 1 agent = 28
    expect(names.length).toBe(28);
    // 抽样检查关键 method 在册
    expect(names).toContain('rpc.ui.map.add_layer');
    expect(names).toContain('rpc.ui.map.add_image_overlay');
    expect(names).toContain('rpc.ui.map.query_features');
    expect(names).toContain('rpc.ui.chat.show_text');
    expect(names).toContain('rpc.ui.chat.show_image');
    expect(names).toContain('rpc.ui.ask.approve_code');
    expect(names).toContain('rpc.ui.fs.list_assets');
    expect(names).toContain('rpc.agent.hello');
  });

  it('listAllMethods matches registered methods', () => {
    const reg = new HandlerRegistry();
    const registered = registerAllHandlers(reg);
    expect(listAllMethods()).toEqual(registered);
  });

  it('double-registration throws unless override', () => {
    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    expect(() => registerAllHandlers(reg)).toThrow();
    expect(() => registerAllHandlers(reg, { override: true })).not.toThrow();
  });
});

describe('Handler contract: valid params → -32603 not implemented', () => {
  // Stage 3.5 + 3.7 已把 12 个 map handler 改为真实现，故不再在这里测：
  //   Stage 3.5：add_layer_from_geojson / remove_layer / zoom_to_bbox / fly_to /
  //              set_basemap / set_layer_style / set_layer_visibility
  //   Stage 3.7：add_layer / zoom_to_layer / list_layers / get_layer / query_features
  // 这些 method 的契约测试见 `__tests__/mapHandlers.test.ts`。
  //
  // 下面只保留**目前仍是 stub** 的 method。13 个 map handler 里唯一没实现的
  // 是 add_raster_from_url（MapEngine 还没接 raster 源），其余都实装完了。
  // 下面只保留**目前仍是 stub** 的 method。
  //   - chat.show_image 已真实现（plot 通道），不在此列。
  //   - chat.show_text / show_table 仍是 stub。
  //   - ask.* 全是 stub（Stage 4 接 UI）。
  //   - fs.* 全是 stub。
  //   - agent.hello 是占位 stub。
  const validCases: Array<[string, unknown]> = [
    ['rpc.ui.chat.show_text', { text: 'hi' }],
    ['rpc.ui.chat.show_table', { columns: ['a'], rows: [[1]] }],
    [
      'rpc.ui.ask.approve_code',
      { run_id: 'run_x', step: 0, code: 'print(1)', risky_operations: [] },
    ],
    ['rpc.ui.ask.choose', { question: 'pick', options: ['a', 'b'] }],
    ['rpc.ui.ask.text', { question: 'name?' }],
    ['rpc.ui.ask.confirm', { question: 'ok?' }],
    ['rpc.ui.fs.get_workspace', {}],
    ['rpc.ui.fs.list_assets', {}],
    ['rpc.ui.fs.open_external', { path: 'a.txt' }],
    ['rpc.agent.hello', { python_version: '0.3.0', supported_protocol: '3.0' }],
  ];

  it.each(validCases)('%s with valid params returns -32603', async (method, params) => {
    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });
    const resp = await d.handleRequest(req(method, params));
    expect(resp).toMatchObject({ error: { code: -32603 } });
  });
});

describe('Handler contract: invalid params → -32602 invalidParams', () => {
  const invalidCases: Array<[string, unknown]> = [
    ['rpc.ui.map.add_layer', { name: 'missing path' }], // path 缺
    ['rpc.ui.map.add_layer', { path: '' }], // path 空串
    ['rpc.ui.map.remove_layer', {}], // 缺 layer_id
    ['rpc.ui.map.zoom_to_bbox', { bbox: [0, 0, 1] }], // bbox 少一个
    [
      'rpc.ui.map.set_layer_style',
      { layer_id: 'x', style: { type: 'wut' } },
    ], // style.type 非法
    ['rpc.ui.map.fly_to', { center: [0] }], // center 少一个
    ['rpc.ui.chat.show_text', {}], // text 缺
    ['rpc.ui.chat.show_table', { columns: 'not-an-array', rows: [] }],
    ['rpc.ui.ask.choose', { question: 'x', options: [] }], // options 空
    ['rpc.ui.ask.approve_code', { run_id: 'r', step: -1, code: '', risky_operations: [] }], // step < 0
    ['rpc.agent.hello', { python_version: 0, supported_protocol: '3.0' }], // version 类型错
    ['rpc.ui.map.add_raster_from_url', { url: 'not-a-url', name: 'x', tile_type: 'xyz' }],
  ];

  it.each(invalidCases)('%s with invalid params returns -32602', async (method, params) => {
    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });
    const resp = await d.handleRequest(req(method, params));
    expect(resp).toMatchObject({ error: { code: -32602 } });
  });
});
