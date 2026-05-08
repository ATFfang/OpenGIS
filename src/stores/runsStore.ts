import { create } from 'zustand'
import { pythonClient } from '@/services/pythonClient'
import { useAssetStore } from './assetStore'

/**
 * 一次 agent run 的摘要信息（`rpc.runs.list` 返回的元素）。
 * 字段对齐 Python 侧 `RunArchive._meta`。
 */
export interface RunSummary {
  run_id: string
  created_at: string           // ISO 8601
  status: 'running' | 'completed' | 'error' | 'cancelled' | 'unknown'
  prompt: string
  model?: string
  pre_sha?: string | null
  post_sha?: string | null
  workspace_path?: string | null
}

/**
 * `rpc.runs.get` 返回的完整对象；比 RunSummary 多一份 steps 列表。
 * step 的字段也对齐 Python 侧 StepRecorder 写进 steps.jsonl 的字段。
 */
export interface RunStep {
  step: number
  kind?: string
  script_path?: string
  code?: string
  output?: string
  error?: string | null
  [key: string]: unknown
}

export interface RunDetail extends RunSummary {
  steps: RunStep[]
  stdout?: string
  final_answer?: string | null
  error?: string | null
  risky_ops?: Array<{ op: string; path?: string; ts?: string }>
}

interface RunsStore {
  runs: RunSummary[]
  isLoading: boolean
  loaded: boolean
  error: string | null

  /** 缓存的 run detail，按需懒加载 */
  details: Record<string, RunDetail | undefined>

  /** 从后端刷新 run 列表（可指定条数上限）。 */
  refresh: (limit?: number) => Promise<void>

  /** 读某条 run 的完整 detail；命中缓存直接返回。 */
  getDetail: (runId: string) => Promise<RunDetail | null>

  /** 撤回某个 run：reset 到该 run 的 pre_sha。 */
  revertRun: (runId: string) => Promise<void>

  /** 重跑：调 `rpc.runs.replay`，由后端读 meta 发起新的 chat.user_message。 */
  replayRun: (runId: string) => Promise<void>
}

export const useRunsStore = create<RunsStore>((set, get) => ({
  runs: [],
  isLoading: false,
  loaded: false,
  error: null,
  details: {},

  refresh: async (limit = 50) => {
    set({ isLoading: true, error: null })
    try {
      // 目前后端 rpc.runs.list 的"每工作区独立"语义由 workspace_path 参数体现。
      // 不开 workspace 时后端 fallback 到 appData 下的 agent-runs 目录（同 ScriptArchive）。
      const workspacePath = useAssetStore.getState().workspacePath || undefined
      const res = await pythonClient.send<{ runs: RunSummary[] }>(
        'rpc.runs.list',
        { limit, workspace_path: workspacePath },
      )
      set({
        runs: Array.isArray(res?.runs) ? res.runs : [],
        isLoading: false,
        loaded: true,
      })
    } catch (e: any) {
      console.error('[runsStore] rpc.runs.list 失败:', e)
      set({
        isLoading: false,
        loaded: true,
        error: e?.message || String(e),
      })
    }
  },

  getDetail: async (runId) => {
    const cached = get().details[runId]
    if (cached) return cached
    try {
      const workspacePath = useAssetStore.getState().workspacePath || undefined
      const detail = await pythonClient.send<RunDetail>(
        'rpc.runs.get',
        { run_id: runId, workspace_path: workspacePath },
      )
      if (!detail) return null
      set((state) => ({ details: { ...state.details, [runId]: detail } }))
      return detail
    } catch (e: any) {
      console.error('[runsStore] rpc.runs.get 失败:', e)
      return null
    }
  },

  revertRun: async (runId) => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    // revert 需要 shell out to git，给 2 min 上限
    await pythonClient.send(
      'rpc.workspace.revert_run',
      { run_id: runId, workspace_path: workspacePath },
      120_000,
    )
    // revert 本身不改动 runs 列表里任何字段，不用 refresh
  },

  replayRun: async (runId) => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    await pythonClient.send(
      'rpc.runs.replay',
      { run_id: runId, workspace_path: workspacePath },
      10 * 60 * 1000, // 同 chat.user_message 的 10 分钟
    )
  },
}))
