/**
 * rpc.ui.ask.* handlers — 4 个
 *
 * Agent-originated approvals and clarifying questions all route through
 * ApprovalGate. Keep this file protocol-only: parse params, create one
 * request, await the result.
 */

import type { RpcHandler } from '../registry';
import { parseParams } from './_util';
import { useApprovalStore } from '@/stores/approvalStore';
import {
  ApproveCodeSchema,
  AskChooseSchema,
  AskConfirmSchema,
  AskTextSchema,
} from './schemas';

export const askHandlers: Record<string, RpcHandler> = {
  'rpc.ui.ask.approve_code': async (params) => {
    const parsed = parseParams(ApproveCodeSchema, params, 'rpc.ui.ask.approve_code');
    const result = await useApprovalStore.getState().request({
      kind: 'code',
      title: '批准 Agent 执行代码？',
      message: parsed.explanation || 'Agent 请求执行 Python 代码。',
      requestId: parsed.request_id,
      toolName: parsed.tool_name || 'execute_code',
      danger: parsed.risky_operations.length > 0,
      timeoutSeconds: parsed.timeout_seconds,
      runId: parsed.run_id,
      step: parsed.step,
      code: parsed.code,
      risks: parsed.risky_operations,
    });
    return { approved: Boolean(result.approved) };
  },

  'rpc.ui.ask.choose': async (params) => {
    const parsed = parseParams(AskChooseSchema, params, 'rpc.ui.ask.choose');
    const result = await useApprovalStore.getState().request({
      kind: 'choose',
      title: parsed.question,
      options: parsed.options,
      defaultValue: parsed.options[0] ?? '',
      timeoutSeconds: parsed.timeout_seconds,
    });
    const answer = result.answer ?? null;
    return { answer: answer && parsed.options.includes(answer) ? answer : null };
  },

  'rpc.ui.ask.text': async (params) => {
    const parsed = parseParams(AskTextSchema, params, 'rpc.ui.ask.text');
    const result = await useApprovalStore.getState().request({
      kind: 'text',
      title: parsed.question,
      placeholder: parsed.placeholder,
      defaultValue: parsed.default,
      timeoutSeconds: parsed.timeout_seconds,
    });
    return { answer: result.answer ?? null };
  },

  'rpc.ui.ask.confirm': async (params) => {
    const parsed = parseParams(AskConfirmSchema, params, 'rpc.ui.ask.confirm');
    const result = await useApprovalStore.getState().request({
      kind: 'confirm',
      title: parsed.question,
      message: parsed.reason,
      requestId: parsed.request_id,
      toolName: parsed.tool_name,
      danger: parsed.danger,
      timeoutSeconds: parsed.timeout_seconds,
    });
    return { approved: Boolean(result.approved) };
  },
};
