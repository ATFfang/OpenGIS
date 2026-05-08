/**
 * rpc.agent.* / rpc.workspace.* — TS → Python 发起方
 *
 * 这些方向是 TS 主动调 Python，所以 TS 侧不注册 handler，而是导出 caller 签名。
 * 例外：`rpc.agent.hello` 是 Python 建连后反向发来的握手，TS 需要 handler（这里实现）。
 *
 * Stage 3 会给 caller 函数接通真实 transport。
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import { AgentHelloSchema } from './schemas';
import { PROTOCOL_VERSION } from '@/types/protocol';

// ---------------------------------------------------------------------------
// Py → TS：rpc.agent.hello （TS 端必须注册 handler）
// ---------------------------------------------------------------------------

export const agentHandlers: Record<string, RpcHandler> = {
  'rpc.agent.hello': (params) => {
    parseParams(AgentHelloSchema, params, 'rpc.agent.hello');
    // Stage 3 会在 transport 建连后让真实 handler 返回 workspace_path；
    // Stage 1 保持 not implemented，但参数校验通过。
    notImplemented('rpc.agent.hello');
  },
};

// ---------------------------------------------------------------------------
// TS → Py：caller 签名（Stage 3 填实现）
// ---------------------------------------------------------------------------

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

/**
 * Stage 1 占位实现：抛 not implemented。Stage 3 用真实 transport 实例替换。
 */
export const agentCallerStub: AgentCaller = {
  interrupt: () => {
    throw new Error('agentCaller.interrupt — wired in Stage 3');
  },
  getStatus: () => {
    throw new Error('agentCaller.getStatus — wired in Stage 3');
  },
  setLlmConfig: () => {
    throw new Error('agentCaller.setLlmConfig — wired in Stage 3');
  },
};

export const workspaceCallerStub: WorkspaceCaller = {
  rollback: () => {
    throw new Error('workspaceCaller.rollback — wired in Stage 3');
  },
};

/** 对外暴露一下当前协议版本，hello caller 会用到。 */
export const TS_PROTOCOL_VERSION = PROTOCOL_VERSION;
