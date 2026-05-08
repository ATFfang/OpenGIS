/**
 * Handler 公共工具
 */

import type { z } from 'zod';
import { RpcError } from '../errors';

/**
 * 校验参数，失败抛 `-32602 Invalid params`，携带 zod issues。
 * 成功返回 parsed 结果（带完整 TS 类型）。
 */
export function parseParams<T extends z.ZodTypeAny>(
  schema: T,
  params: unknown,
  method: string,
): z.infer<T> {
  const result = schema.safeParse(params);
  if (!result.success) {
    throw RpcError.invalidParams(`Invalid params for ${method}`, {
      method,
      issues: result.error.issues,
    });
  }
  return result.data;
}

/**
 * 标记一个 method 在 Stage 1 阶段还没实现。
 * Stage 2-3 会逐个替换成真实实现。
 */
export function notImplemented(method: string): never {
  throw RpcError.notImplemented(method);
}
