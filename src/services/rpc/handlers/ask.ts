/**
 * rpc.ui.ask.* handlers — 4 个
 *
 * 其中 `approve_code` 是权限门核心，Stage 4 会接入 PermissionGate UI 弹框。
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import {
  ApproveCodeSchema,
  AskChooseSchema,
  AskConfirmSchema,
  AskTextSchema,
} from './schemas';

export const askHandlers: Record<string, RpcHandler> = {
  'rpc.ui.ask.approve_code': (params) => {
    parseParams(ApproveCodeSchema, params, 'rpc.ui.ask.approve_code');
    notImplemented('rpc.ui.ask.approve_code');
  },

  'rpc.ui.ask.choose': (params) => {
    parseParams(AskChooseSchema, params, 'rpc.ui.ask.choose');
    notImplemented('rpc.ui.ask.choose');
  },

  'rpc.ui.ask.text': (params) => {
    parseParams(AskTextSchema, params, 'rpc.ui.ask.text');
    notImplemented('rpc.ui.ask.text');
  },

  'rpc.ui.ask.confirm': (params) => {
    parseParams(AskConfirmSchema, params, 'rpc.ui.ask.confirm');
    notImplemented('rpc.ui.ask.confirm');
  },
};
