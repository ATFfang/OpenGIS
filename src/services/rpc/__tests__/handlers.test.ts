/**
 * Handler 骨架的契约测试：
 *   - 注册齐当前 UI-facing method
 *   - 合法参数 → -32603 notImplemented（证明参数校验通过，落到 stub 抛错）
 *   - 非法参数 → -32602 invalidParams
 */

import { Dispatcher } from '../dispatcher';
import { HandlerRegistry } from '../registry';
import { listAllMethods, registerAllHandlers } from '../handlers/register';
import type { JsonRpcRequest } from '@/types/protocol';
import { useChatStore } from '@/stores/chatStore';

function req(method: string, params: unknown = {}, id = 'r'): JsonRpcRequest {
  return { jsonrpc: '2.0', id, method, params };
}

describe('Handler registration', () => {
  it('registers expected method count', () => {
    const reg = new HandlerRegistry();
    const names = registerAllHandlers(reg);
    expect(names.length).toBe(45);
    // 抽样检查关键 method 在册
    expect(names).toContain('rpc.ui.map.add_layer');
    expect(names).toContain('rpc.ui.map.dynamic_layer_update');
    expect(names).toContain('rpc.ui.map.add_image_overlay');
    expect(names).toContain('rpc.ui.map.query_features');
    expect(names).toContain('rpc.ui.map.set_basemap_visibility');
    expect(names).toContain('rpc.ui.chat.show_text');
    expect(names).toContain('rpc.ui.chat.show_image');
    expect(names).toContain('rpc.ui.ask.approve_code');
    expect(names).toContain('rpc.ui.fs.list_assets');
    expect(names).toContain('rpc.ui.fs.refresh_assets');
    expect(names).toContain('rpc.ui.layout.get_state');
    expect(names).toContain('rpc.ui.layout.add_element');
    expect(names).toContain('rpc.ui.layout.update_frame');
    expect(names).toContain('rpc.ui.layout.update_style');
    expect(names).toContain('rpc.ui.layout.update_map_view');
    expect(names).toContain('rpc.ui.layout.export');
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
  // Implemented map/chat/ask/fs handlers have dedicated behavior tests.
  // This block only covers methods that are intentionally declared but still
  // unavailable in the renderer.
  //   - chat.show_image 已真实现（plot 通道），不在此列。
  //   - chat.show_text / show_table 仍是 stub。
  //   - ask.* 已接 Dialog 审批 UI，不在此列。
  //   - fs.get_workspace / fs.list_assets / fs.open_external 仍是 stub。
  //   - fs.refresh_assets 已真实现，不在此列。
  //   - agent.hello 是占位 stub。
  const validCases: Array<[string, unknown]> = [
    ['rpc.ui.chat.show_text', { text: 'hi' }],
    ['rpc.ui.chat.show_table', { columns: ['a'], rows: [[1]] }],
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

describe('rpc.ui.fs.refresh_assets', () => {
  it('dispatches the AssetExplorer refresh event', async () => {
    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });
    const listener = vi.fn();
    const previousWindow = (globalThis as any).window;
    const previousCustomEvent = (globalThis as any).CustomEvent;
    const target = new EventTarget();
    const customEvent =
      previousCustomEvent ??
      class<T = unknown> extends Event {
        detail: T;
        constructor(type: string, init?: CustomEventInit<T>) {
          super(type);
          this.detail = init?.detail as T;
        }
      };

    (globalThis as any).CustomEvent = customEvent;
    (globalThis as any).window = {
      addEventListener: target.addEventListener.bind(target),
      removeEventListener: target.removeEventListener.bind(target),
      dispatchEvent: target.dispatchEvent.bind(target),
    };

    try {
      window.addEventListener('opengis:assets-refresh', listener);
      const resp = await d.handleRequest(req('rpc.ui.fs.refresh_assets', {
        path: '/tmp/result.geojson',
        reason: 'write_file',
      }));
      window.removeEventListener('opengis:assets-refresh', listener);

      expect(resp).toMatchObject({ result: { success: true } });
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener.mock.calls[0]?.[0]).toMatchObject({
        detail: { path: '/tmp/result.geojson', reason: 'write_file' },
      });
    } finally {
      (globalThis as any).window = previousWindow;
      (globalThis as any).CustomEvent = previousCustomEvent;
    }
  });
});

describe('rpc.ui.chat.plan_update', () => {
  it('creates a renderable MessagePart-backed plan card', async () => {
    useChatStore.setState({
      conversations: [],
      activeConversationId: null,
      isStreaming: false,
      isCancelling: false,
      workflowPlanActive: false,
      _persistenceReady: false,
    });
    useChatStore.getState().createConversation();

    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });

    const resp = await d.handleRequest(req('rpc.ui.chat.plan_update', {
      plan_id: 'workflow-test',
      title: 'Workflow Test',
      workflow: true,
      run_id: 'run-1',
      steps: [{ id: 'step1', title: 'Load data', status: 'in_progress' }],
    }));

    expect(resp).toMatchObject({ result: { ok: true } });
    const conv = useChatStore.getState().activeConversation();
    expect(conv?.messages).toHaveLength(1);
    const msg = conv!.messages[0];
    expect(msg.say).toBe('plan');
    expect(msg.planData?.workflow).toBe(true);
    expect(msg.parts?.[0]).toMatchObject({
      id: 'plan:workflow-test',
      type: 'plan',
      status: 'running',
      data: { planData: expect.objectContaining({ planId: 'workflow-test' }) },
    });
    expect(useChatStore.getState().workflowPlanActive).toBe(true);
  });
});

describe('rpc.ui.chat.subagent_update', () => {
  it('creates a renderable MessagePart-backed subagent card', async () => {
    useChatStore.setState({
      conversations: [],
      activeConversationId: null,
      isStreaming: false,
      isCancelling: false,
      workflowPlanActive: false,
      _persistenceReady: false,
    });
    useChatStore.getState().createConversation();

    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });

    const resp = await d.handleRequest(req('rpc.ui.chat.subagent_update', {
      subagent_id: 'sub-1',
      status: 'running',
      parallel: false,
      run_id: 'run-1',
      tasks: [{ title: 'Inspect dataset', status: 'running' }],
    }));

    expect(resp).toMatchObject({ result: { ok: true } });
    const msg = useChatStore.getState().activeConversation()!.messages[0];
    expect(msg.say).toBe('subagent');
    expect(msg.parts?.[0]).toMatchObject({
      id: 'subagent:sub-1',
      type: 'progress',
      status: 'running',
      data: {
        kind: 'subagent',
        subagentData: expect.objectContaining({ subagentId: 'sub-1' }),
      },
    });
  });
});

describe('rpc.ui.chat.interactive_snapshot', () => {
  it('creates a renderable MessagePart-backed screenshot approval card', async () => {
    useChatStore.setState({
      conversations: [],
      activeConversationId: null,
      isStreaming: false,
      isCancelling: false,
      workflowPlanActive: false,
      _persistenceReady: false,
    });
    useChatStore.getState().createConversation();

    const reg = new HandlerRegistry();
    registerAllHandlers(reg);
    const d = new Dispatcher({ registry: reg });

    const resp = await d.handleRequest(req('rpc.ui.chat.interactive_snapshot', {
      request_id: 'shot-1',
      save_path: '/tmp/shot.png',
      prompt: 'Capture map',
    }));

    expect(resp).toMatchObject({ result: { ok: true } });
    const msg = useChatStore.getState().activeConversation()!.messages[0];
    expect(msg.say).toBe('screenshot');
    expect(msg.parts?.[0]).toMatchObject({
      id: 'screenshot:shot-1',
      type: 'approval',
      status: 'pending',
      data: {
        kind: 'screenshot',
        screenshotData: expect.objectContaining({ requestId: 'shot-1' }),
      },
    });
  });
});
