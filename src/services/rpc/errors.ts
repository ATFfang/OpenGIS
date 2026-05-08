/**
 * JSON-RPC 2.0 错误码枚举 + RpcError 异常类
 *
 * 来源：`docs/api/INTERFACE.md` §0.4
 */

import type { JsonRpcErrorObject, JsonRpcErrorResponse } from '@/types/protocol';

export const RPC_ERROR_CODES = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  TIMEOUT: -32000,
  USER_CANCELLED: -32001,
  PERMISSION_DENIED: -32002,
  SANDBOX_ERROR: -32003,
  LLM_ERROR: -32004,
} as const;

export type RpcErrorCode = (typeof RPC_ERROR_CODES)[keyof typeof RPC_ERROR_CODES];

export class RpcError extends Error {
  public readonly code: number;
  public readonly data?: unknown;

  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.name = 'RpcError';
    this.code = code;
    this.data = data;
  }

  toJsonRpcError(): JsonRpcErrorObject {
    return { code: this.code, message: this.message, data: this.data };
  }

  /** 建一个 JSON-RPC 错误响应对象（供 dispatcher 返回）。 */
  toResponse(id: string): JsonRpcErrorResponse {
    return {
      jsonrpc: '2.0',
      id,
      error: this.toJsonRpcError(),
    };
  }

  // --- factory helpers ---

  static parseError(message = 'Parse error', data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.PARSE_ERROR, message, data);
  }
  static invalidRequest(message = 'Invalid Request', data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.INVALID_REQUEST, message, data);
  }
  static methodNotFound(method: string): RpcError {
    return new RpcError(RPC_ERROR_CODES.METHOD_NOT_FOUND, `Method not found: ${method}`, {
      method,
    });
  }
  static invalidParams(message: string, data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.INVALID_PARAMS, message, data);
  }
  static internal(message: string, data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.INTERNAL_ERROR, message, data);
  }
  static notImplemented(method: string): RpcError {
    return new RpcError(RPC_ERROR_CODES.INTERNAL_ERROR, `Not implemented: ${method}`, {
      method,
      stage: 'stage-1-stub',
    });
  }
  static timeout(method: string, ms: number): RpcError {
    return new RpcError(RPC_ERROR_CODES.TIMEOUT, `Timeout: ${method} exceeded ${ms}ms`, {
      method,
      timeout_ms: ms,
    });
  }
  static userCancelled(message = 'User cancelled'): RpcError {
    return new RpcError(RPC_ERROR_CODES.USER_CANCELLED, message);
  }
  static permissionDenied(message: string, data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.PERMISSION_DENIED, message, data);
  }
  static sandbox(message: string, data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.SANDBOX_ERROR, message, data);
  }
  static llm(message: string, data?: unknown): RpcError {
    return new RpcError(RPC_ERROR_CODES.LLM_ERROR, message, data);
  }
}
