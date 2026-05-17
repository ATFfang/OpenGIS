/**
 * OpenGIS v3 Protocol Types
 *
 * 通用协议类型定义，与 Python 侧 `python-backend/opengis_backend/protocol.py` 对齐。
 *
 * 来源：`docs/api/INTERFACE.md` §0.5
 * 规范：`docs/v3/protocol.md`
 *
 * ⚠️ 修改此文件时，必须同步更新 Python 侧的 protocol.py 和 docs/api/INTERFACE.md §0.5/0.6。
 */

// ─────────────────────────────────────────────────────────────────────
// 几何与空间参考
// ─────────────────────────────────────────────────────────────────────

/** [minX, minY, maxX, maxY]，单位随 CRS。WGS84 下即经纬度。 */
export type BBox = [number, number, number, number];

/** EPSG 代码字符串。如 'EPSG:4326'、'EPSG:3857'。 */
export type CRS = string;

export type GeometryType =
  | 'Point'
  | 'MultiPoint'
  | 'LineString'
  | 'MultiLineString'
  | 'Polygon'
  | 'MultiPolygon'
  | 'GeometryCollection'
  | 'Raster';

export type LayerSource = 'file' | 'memory' | 'url' | 'postgis';

// ─────────────────────────────────────────────────────────────────────
// 渲染样式
// ─────────────────────────────────────────────────────────────────────

export type LayerStyleType = 'circle' | 'line' | 'fill' | 'raster' | 'symbol';

export interface LayerStyle {
  type: LayerStyleType;
  /** MapLibre paint 属性，结构依 type 而定。 */
  paint?: Record<string, unknown>;
  /** MapLibre layout 属性。 */
  layout?: Record<string, unknown>;
}

// ─────────────────────────────────────────────────────────────────────
// JSON-RPC 2.0 消息
// ─────────────────────────────────────────────────────────────────────

export interface JsonRpcRequest<TParams = unknown> {
  jsonrpc: '2.0';
  id: string;
  method: string;
  params: TParams;
}

export interface JsonRpcErrorObject<TData = unknown> {
  /** 错误码，见 `docs/api/INTERFACE.md` §0.4。 */
  code: number;
  message: string;
  data?: TData;
}

export interface JsonRpcSuccessResponse<TResult = unknown> {
  jsonrpc: '2.0';
  id: string;
  result: TResult;
  error?: never;
}

export interface JsonRpcErrorResponse<TData = unknown> {
  jsonrpc: '2.0';
  id: string;
  result?: never;
  error: JsonRpcErrorObject<TData>;
}

export type JsonRpcResponse<TResult = unknown, TErrorData = unknown> =
  | JsonRpcSuccessResponse<TResult>
  | JsonRpcErrorResponse<TErrorData>;

export interface JsonRpcNotification<TParams = unknown> {
  jsonrpc: '2.0';
  method: string;
  params: TParams;
}

export type JsonRpcMessage<TParams = unknown, TResult = unknown, TErrorData = unknown> =
  | JsonRpcRequest<TParams>
  | JsonRpcResponse<TResult, TErrorData>
  | JsonRpcNotification<TParams>;

// ─────────────────────────────────────────────────────────────────────
// 方法前缀（三通道分流）
// ─────────────────────────────────────────────────────────────────────

/** 双向 RPC（需要 result）。 */
export const METHOD_PREFIX_RPC = 'rpc.' as const;
/** 聊天消息、流式 token、代码步事件。 */
export const METHOD_PREFIX_CHAT = 'chat.' as const;
/** 单向状态变更事件（TS → Py）。 */
export const METHOD_PREFIX_EVENT = 'event.' as const;

export type MethodChannel = 'rpc' | 'chat' | 'event';

export function getMethodChannel(method: string): MethodChannel | null {
  if (method.startsWith(METHOD_PREFIX_RPC)) return 'rpc';
  if (method.startsWith(METHOD_PREFIX_CHAT)) return 'chat';
  if (method.startsWith(METHOD_PREFIX_EVENT)) return 'event';
  return null;
}

// ─────────────────────────────────────────────────────────────────────
// 协议版本
// ─────────────────────────────────────────────────────────────────────

export const PROTOCOL_VERSION = '3.0' as const;
