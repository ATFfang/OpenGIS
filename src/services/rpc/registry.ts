/**
 * RPC Handler Registry
 *
 * 维护 method 名 → handler 函数的映射表。
 * Handler 签名：(params: unknown) => Promise<unknown> | unknown
 *   - 抛出 RpcError 会被 dispatcher 捕获并转为 JSON-RPC error response。
 *   - 抛出其它异常会被当 INTERNAL_ERROR 处理。
 */

import { RpcError } from './errors';

export type RpcHandler<TParams = unknown, TResult = unknown> = (
  params: TParams,
) => Promise<TResult> | TResult;

export class HandlerRegistry {
  private readonly handlers = new Map<string, RpcHandler>();

  /**
   * 注册 handler。重复注册会抛错（防止生产环境意外覆盖）。
   */
  register<TParams, TResult>(method: string, handler: RpcHandler<TParams, TResult>): void {
    if (this.handlers.has(method)) {
      throw new Error(`Handler already registered for method: ${method}`);
    }
    this.handlers.set(method, handler as RpcHandler);
  }

  /**
   * 覆盖注册（测试 / mock 用）。
   */
  override<TParams, TResult>(method: string, handler: RpcHandler<TParams, TResult>): void {
    this.handlers.set(method, handler as RpcHandler);
  }

  unregister(method: string): boolean {
    return this.handlers.delete(method);
  }

  has(method: string): boolean {
    return this.handlers.has(method);
  }

  get(method: string): RpcHandler | undefined {
    return this.handlers.get(method);
  }

  /** 拿到 handler，找不到抛 METHOD_NOT_FOUND。 */
  getOrThrow(method: string): RpcHandler {
    const h = this.handlers.get(method);
    if (!h) throw RpcError.methodNotFound(method);
    return h;
  }

  methods(): string[] {
    return [...this.handlers.keys()].sort();
  }

  clear(): void {
    this.handlers.clear();
  }

  size(): number {
    return this.handlers.size;
  }
}

/** 单例，供两端默认使用；测试里直接 new HandlerRegistry() 即可。 */
export const globalRegistry = new HandlerRegistry();
