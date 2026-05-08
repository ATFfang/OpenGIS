/**
 * ScriptStore (v3) — LLM 每一步落盘脚本的登记
 *
 * 每个 `chat.code_block` 事件对应一个 Script：
 *   - 代码内容写到 `.opengis/scripts/run_xxx/step_N.py`
 *   - 执行结果（stdout 摘要 / status）回填到 Script
 *
 * Stage 1 只存元数据；实际落盘由 Stage 2 的 SubprocessPythonExecutor 处理。
 */

import { create } from 'zustand';
import { newScriptId } from '@/services/rpc/idGen';

export type ScriptStatus = 'pending' | 'ok' | 'error' | 'timeout' | 'cancelled' | 'denied';

export interface Script {
  script_id: string;
  run_id: string;
  step: number;
  /** 绝对路径，指向 `.opengis/scripts/run_xxx/step_N.py`。 */
  script_path: string;
  /** 代码文本（内联冗余，方便 UI 直接展示而不读磁盘）。 */
  code: string;
  status: ScriptStatus;
  /** 执行用时（ms），pending 时为 undefined。 */
  duration_ms?: number;
  /** stdout 截断摘要（前 4KB 左右）。 */
  stdout_summary?: string;
  /** 错误信息。 */
  error?: string;
  created_at: number;
  finished_at?: number;
}

export type ScriptInit = Omit<Script, 'script_id' | 'status' | 'created_at'> & {
  script_id?: string;
  status?: ScriptStatus;
  created_at?: number;
};

interface ScriptState {
  scripts: Record<string, Script>;
  byRun: Record<string, string[]>; // run_id -> script_ids（按 step 顺序）

  // actions
  create: (init: ScriptInit) => Script;
  finish: (
    scriptId: string,
    update: Pick<Script, 'status'> & Partial<Pick<Script, 'duration_ms' | 'stdout_summary' | 'error'>>,
  ) => Script | null;
  get: (scriptId: string) => Script | undefined;
  listByRun: (runId: string) => Script[];
  clear: () => void;
}

export const useScriptStore = create<ScriptState>((set, get) => ({
  scripts: {},
  byRun: {},

  create: (init) => {
    const script: Script = {
      status: 'pending',
      created_at: Date.now(),
      ...init,
      script_id: init.script_id ?? newScriptId(),
    };
    set((s) => ({
      scripts: { ...s.scripts, [script.script_id]: script },
      byRun: {
        ...s.byRun,
        [script.run_id]: [...(s.byRun[script.run_id] ?? []), script.script_id],
      },
    }));
    return script;
  },

  finish: (scriptId, update) => {
    const current = get().scripts[scriptId];
    if (!current) return null;
    const next: Script = {
      ...current,
      ...update,
      finished_at: Date.now(),
    };
    set((s) => ({ scripts: { ...s.scripts, [scriptId]: next } }));
    return next;
  },

  get: (scriptId) => get().scripts[scriptId],

  listByRun: (runId) => {
    const ids = get().byRun[runId] ?? [];
    return ids
      .map((id) => get().scripts[id])
      .filter((x): x is Script => !!x)
      .sort((a, b) => a.step - b.step);
  },

  clear: () => set({ scripts: {}, byRun: {} }),
}));
