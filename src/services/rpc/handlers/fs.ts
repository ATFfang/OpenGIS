/**
 * rpc.ui.fs.* handlers — 3 个
 *
 * Stage 3 接入 Electron Main 的 fs 权限门（只读 workspace 内路径）。
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import { GetWorkspaceSchema, ListAssetsSchema, OpenExternalSchema } from './schemas';

export const fsHandlers: Record<string, RpcHandler> = {
  'rpc.ui.fs.get_workspace': (params) => {
    parseParams(GetWorkspaceSchema, params, 'rpc.ui.fs.get_workspace');
    notImplemented('rpc.ui.fs.get_workspace');
  },

  'rpc.ui.fs.list_assets': (params) => {
    parseParams(ListAssetsSchema, params, 'rpc.ui.fs.list_assets');
    notImplemented('rpc.ui.fs.list_assets');
  },

  'rpc.ui.fs.open_external': (params) => {
    parseParams(OpenExternalSchema, params, 'rpc.ui.fs.open_external');
    notImplemented('rpc.ui.fs.open_external');
  },
};
