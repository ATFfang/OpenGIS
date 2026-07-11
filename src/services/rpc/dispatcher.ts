/**
 * JSON-RPC 2.0 Dispatcher
 *
 * 职责：
 *   - 把收到的 JSON-RPC request / notification 路由到 registry 里对应的 handler
 *   - 把 handler 的返回值包装成 JsonRpcSuccessResponse
 *   - 把 RpcError / 普通异常包装成 JsonRpcErrorResponse
 *   - Notification（无 id）不会返回任何响应
 */

import {
  getMethodChannel,
  type JsonRpcNotification,
  type JsonRpcRequest,
  type JsonRpcResponse,
} from '@/types/protocol';
import { RpcError } from './errors';
import { globalRegistry, type HandlerRegistry } from './registry';

export interface DispatcherOptions {
  registry?: HandlerRegistry;
  /** 每个 method 的超时（ms），不配则用 defaultTimeoutMs。 */
  methodTimeouts?: Record<string, number>;
  /** 默认超时，默认 60 秒。 */
  defaultTimeoutMs?: number;
  /** 可选日志钩子。 */
  onError?: (method: string, error: unknown) => void;
}

/** 包了一层超时的 Promise。 */
function withTimeout<T>(promise: Promise<T>, ms: number, method: string): Promise<T> {
  if (!Number.isFinite(ms) || ms <= 0) return promise;
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(RpcError.timeout(method, ms));
    }, ms);
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}

export class Dispatcher {
  private readonly registry: HandlerRegistry;
  private readonly methodTimeouts: Record<string, number>;
  private readonly defaultTimeoutMs: number;
  private readonly onError?: DispatcherOptions['onError'];

  constructor(opts: DispatcherOptions = {}) {
    this.registry = opts.registry ?? globalRegistry;
    this.methodTimeouts = opts.methodTimeouts ?? {};
    this.defaultTimeoutMs = opts.defaultTimeoutMs ?? 60_000;
    this.onError = opts.onError;
  }

  /**
   * 处理一条 JSON-RPC request。返回响应（成功或错误）。
   */
  async handleRequest(req: JsonRpcRequest): Promise<JsonRpcResponse> {
    // 格式校验
    if (req.jsonrpc !== '2.0' || typeof req.id !== 'string' || typeof req.method !== 'string') {
      return RpcError.invalidRequest('Malformed JSON-RPC request', { received: req }).toResponse(
        typeof req.id === 'string' ? req.id : '',
      );
    }

    try {
      const handler = this.registry.getOrThrow(req.method);
      const timeout = this.methodTimeouts[req.method] ?? this.defaultTimeoutMs;
      const result = await withTimeout(
        Promise.resolve().then(() => handler(req.params)),
        timeout,
        req.method,
      );
      return { jsonrpc: '2.0', id: req.id, result };
    } catch (err) {
      this.onError?.(req.method, err);
      if (err instanceof RpcError) {
        return err.toResponse(req.id);
      }
      // 未知异常 → INTERNAL_ERROR，透传 message 便于排障
      const message = err instanceof Error ? err.message : String(err);
      return RpcError.internal(message, { method: req.method }).toResponse(req.id);
    }
  }

  /**
   * 处理一条 notification（无 id 的 request）。
   * 返回 void；handler 抛错只触发 onError 钩子。
   */
  async handleNotification(notif: JsonRpcNotification): Promise<void> {
    if (notif.jsonrpc !== '2.0' || typeof notif.method !== 'string') {
      this.onError?.(notif.method ?? '<unknown>', RpcError.invalidRequest('Malformed notification'));
      return;
    }
    const handler = this.registry.get(notif.method);
    if (!handler) {
      this.onError?.(notif.method, RpcError.methodNotFound(notif.method));
      return;
    }
    try {
      await Promise.resolve().then(() => handler(notif.params));
    } catch (err) {
      this.onError?.(notif.method, err);
    }
  }

  /**
   * 统一入口：根据消息是否带 id 自动分派。
   * 返回 JsonRpcResponse 或 null（notification）。
   */
  async dispatch(
    msg: JsonRpcRequest | JsonRpcNotification,
  ): Promise<JsonRpcResponse | null> {
    if ('id' in msg && msg.id !== undefined) {
      return this.handleRequest(msg as JsonRpcRequest);
    }
    await this.handleNotification(msg as JsonRpcNotification);
    return null;
  }

  /** 根据 method 前缀返回通道类型，暴露在 dispatcher 上便于就近引用。 */
  static channelOf(method: string) {
    return getMethodChannel(method);
  }
}

/** 懒加载单例：若使用 globalRegistry 就用默认 dispatcher。 */
export const globalDispatcher = new Dispatcher();
