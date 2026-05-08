/**
 * EventBus (v3) — TS → Python 单向状态变更通知
 *
 * Stage 1：发送动作通过 `transport` 抽象；默认用 `consoleTransport`（只打日志）。
 * Stage 3：替换为真实 WebSocket notification transport。
 *
 * 节流规则：
 *   - `event.viewport.changed` 必须节流（trailing），默认 200ms
 *   - 其他事件立即 emit
 */

import type {
  BBox,
  CRS,
  GeometryType,
  JsonRpcNotification,
  LayerStyle,
} from '@/types/protocol';

// ---------------------------------------------------------------------------
// 事件 payload 类型（与 INTERFACE.md §3 一一对应）
// ---------------------------------------------------------------------------

export interface LayerAddedPayload {
  layer_id: string;
  name: string;
  geometry_type: GeometryType;
  bbox: BBox;
  feature_count: number;
  crs: CRS;
  added_by: 'user' | 'agent';
  run_id?: string;
}

export interface LayerRemovedPayload {
  layer_id: string;
}

export interface LayerUpdatedPayload {
  layer_id: string;
  changes: Partial<{ name: string; visible: boolean; style: LayerStyle }>;
}

export interface SelectionChangedPayload {
  layer_id: string;
  feature_ids: string[];
}

export interface ViewportChangedPayload {
  center: [number, number];
  zoom: number;
  bbox: BBox;
  pitch: number;
  bearing: number;
}

export interface WorkspaceOpenedPayload {
  workspace_path: string;
  project_name: string;
  layers: unknown[]; // Layer[] snapshot，Stage 3 填准确类型
  assets: unknown[]; // Asset[] snapshot
}

export interface WorkspaceClosedPayload {
  workspace_path: string;
}

// ---------------------------------------------------------------------------
// Transport：Stage 1 只有 console，Stage 3 换成 WebSocket
// ---------------------------------------------------------------------------

export type EventTransport = (notif: JsonRpcNotification) => void;

export const consoleTransport: EventTransport = (notif) => {
  // eslint-disable-next-line no-console
  console.log('[EventBus]', notif.method, notif.params);
};

// ---------------------------------------------------------------------------
// 节流工具（trailing-edge，保留最后一次 payload）
// ---------------------------------------------------------------------------

function trailingThrottle<T>(
  fn: (arg: T) => void,
  waitMs: number,
): (arg: T) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingArg: T | null = null;
  return (arg: T) => {
    pendingArg = arg;
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      if (pendingArg !== null) {
        const toSend = pendingArg;
        pendingArg = null;
        fn(toSend);
      }
    }, waitMs);
  };
}

// ---------------------------------------------------------------------------
// EventBus
// ---------------------------------------------------------------------------

export interface EventBusOptions {
  transport?: EventTransport;
  viewportThrottleMs?: number;
}

export class EventBus {
  private transport: EventTransport;
  private viewportEmit: (payload: ViewportChangedPayload) => void;

  constructor(opts: EventBusOptions = {}) {
    this.transport = opts.transport ?? consoleTransport;
    const throttleMs = opts.viewportThrottleMs ?? 200;
    this.viewportEmit = trailingThrottle((p: ViewportChangedPayload) => {
      this.send('event.viewport.changed', p);
    }, throttleMs);
  }

  /** Stage 3 接通真实 transport 后调用这个替换。 */
  setTransport(transport: EventTransport): void {
    this.transport = transport;
  }

  private send(method: string, params: unknown): void {
    this.transport({ jsonrpc: '2.0', method, params });
  }

  // --- 具名发射器 ---

  emitLayerAdded(payload: LayerAddedPayload): void {
    this.send('event.layer.added', payload);
  }

  emitLayerRemoved(payload: LayerRemovedPayload): void {
    this.send('event.layer.removed', payload);
  }

  emitLayerUpdated(payload: LayerUpdatedPayload): void {
    this.send('event.layer.updated', payload);
  }

  emitSelectionChanged(payload: SelectionChangedPayload): void {
    this.send('event.selection.changed', payload);
  }

  /** 节流 200ms（trailing）。 */
  emitViewportChanged(payload: ViewportChangedPayload): void {
    this.viewportEmit(payload);
  }

  emitWorkspaceOpened(payload: WorkspaceOpenedPayload): void {
    this.send('event.workspace.opened', payload);
  }

  emitWorkspaceClosed(payload: WorkspaceClosedPayload): void {
    this.send('event.workspace.closed', payload);
  }
}

/** 默认单例。Stage 3 用 `setTransport` 接入真实 WebSocket。 */
export const eventBus = new EventBus();
