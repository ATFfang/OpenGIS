/**
 * rpc.agent.* / rpc.workspace.* — TS → Python 发起方
 *
 * 这些方向是 TS 主动调 Python，所以 TS 侧不注册 handler，而是导出 caller 签名。
 * 例外：`rpc.agent.hello` 是 Python 建连后反向发来的握手，TS 需要 handler（这里实现）。
 *
 * Runtime code supplies the concrete transport-backed caller.
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import { AgentHelloSchema } from './schemas';
import { PROTOCOL_VERSION } from '@/types/protocol';

// ─────────────────────────────────────────────────────────────────────
// Py → TS：rpc.agent.hello （TS 端必须注册 handler）
// ─────────────────────────────────────────────────────────────────────

export const agentHandlers: Record<string, RpcHandler> = {
  'rpc.agent.hello': (params) => {
    parseParams(AgentHelloSchema, params, 'rpc.agent.hello');
    notImplemented('rpc.agent.hello');
  },
};

// ─────────────────────────────────────────────────────────────────────
// TS → Py：caller 签名
// ─────────────────────────────────────────────────────────────────────

export interface AgentCaller {
  interrupt(runId: string): Promise<{ interrupted: boolean }>;
  getStatus(): Promise<{
    status: 'idle' | 'running' | 'waiting_approval';
    current_run_id?: string;
    current_step?: number;
    started_at?: number;
  }>;
  setLlmConfig(config: {
    provider: string;
    model: string;
    api_key: string;
    base_url?: string;
    temperature?: number;
    max_tokens?: number;
  }): Promise<{ ok: true; route: string }>;
}

export interface WorkspaceCaller {
  rollback(commit: string): Promise<{ ok: true }>;
}

/** Placeholder caller used before the transport-backed implementation is installed. */
export const agentCallerStub: AgentCaller = {
  interrupt: () => {
    throw new Error('agentCaller.interrupt is not wired');
  },
  getStatus: () => {
    throw new Error('agentCaller.getStatus is not wired');
  },
  setLlmConfig: () => {
    throw new Error('agentCaller.setLlmConfig is not wired');
  },
};

export const workspaceCallerStub: WorkspaceCaller = {
  rollback: () => {
    throw new Error('workspaceCaller.rollback is not wired');
  },
};

/** 对外暴露一下当前协议版本，hello caller 会用到。 */
export const TS_PROTOCOL_VERSION = PROTOCOL_VERSION;
