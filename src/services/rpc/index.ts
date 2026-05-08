/**
 * RPC 模块统一出口
 */

export { RpcError, RPC_ERROR_CODES, type RpcErrorCode } from './errors';
export {
  newLayerId,
  newAssetId,
  newScriptId,
  newRunId,
  newMsgId,
  newId,
  inferIdKind,
  isValidId,
  ID_PREFIXES,
  type IdKind,
} from './idGen';
export { HandlerRegistry, globalRegistry, type RpcHandler } from './registry';
export { Dispatcher, globalDispatcher, type DispatcherOptions } from './dispatcher';
export { registerAllHandlers, listAllMethods, ALL_HANDLER_GROUPS } from './handlers/register';
