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
  status: 'running' | 'success' | 'completed' | 'error' | 'cancelled' | 'unknown'
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

export interface RunToolCall {
  call_id: string
  name: string
  arguments?: Record<string, unknown>
  output?: string
  error?: string | null
  duration_ms?: number | null
  status?: 'running' | 'completed' | 'error' | string
  metadata?: Record<string, unknown>
  ts?: string
}

export interface RunArtifact {
  id?: string
  kind: string
  path?: string | null
  layer_id?: string | null
  title?: string
  metadata?: Record<string, unknown>
  created_at?: number
  ts?: string
}

export interface RunSession {
  id: string
  kind: string
  profile_name: string
  parent_id?: string | null
  run_id?: string | null
  title?: string
  status?: string
  children?: string[]
  summary?: string
  metadata?: Record<string, unknown>
}

export interface RunLLMUsageRecord {
  run_id?: string
  usage?: Record<string, any>
  prompt_cache?: Record<string, any>
  telemetry?: Record<string, any>
  request?: Record<string, any>
  ts?: string
}

export type AgentWorkStatus = 'queued' | 'running' | 'completed' | 'success' | 'error' | 'cancelled' | 'unknown'

export interface AgentInboxItem {
  id: string
  prompt: string
  conversation_id?: string | null
  profile_name?: string | null
  session_id?: string | null
  run_id?: string | null
  status: AgentWorkStatus
  error?: string
  created_at?: number
  updated_at?: number
  metadata?: Record<string, unknown>
}

export interface AgentQueueItem {
  id: string
  inbox_id: string
  status: AgentWorkStatus
  run_id?: string | null
  workspace_path?: string | null
  profile_name?: string | null
  conversation_id?: string | null
  error?: string
  created_at?: number
  updated_at?: number
  metadata?: Record<string, unknown>
}

export interface PermissionRule {
  id: string
  tool: string
  action: 'allow' | 'ask' | 'deny' | string
  scope?: string
  reason?: string
  profile_name?: string | null
  created_at?: number
}

export interface RunDetail extends RunSummary {
  steps: RunStep[]
  tool_calls?: RunToolCall[]
  tool_call_events?: RunToolCall[]
  artifacts?: RunArtifact[]
  llm_usage?: RunLLMUsageRecord[]
  session?: RunSession | null
  stdout?: string
  final_answer?: string | null
  error?: string | null
  risky_ops?: Array<{ op: string; path?: string; ts?: string }>
}

interface RunDetailResponse {
  status?: string
  meta?: Partial<RunDetail> & Record<string, unknown>
  steps?: RunStep[]
  tool_calls?: RunToolCall[]
  tool_call_events?: RunToolCall[]
  artifacts?: RunArtifact[]
  llm_usage?: RunLLMUsageRecord[]
}

function normalizeRunDetail(raw: RunDetailResponse | RunDetail | null | undefined): RunDetail | null {
  if (!raw) return null
  const meta = ('meta' in raw && raw.meta && typeof raw.meta === 'object') ? raw.meta : raw
  const steps = Array.isArray((raw as RunDetailResponse).steps)
    ? (raw as RunDetailResponse).steps!
    : Array.isArray((meta as any).steps)
      ? (meta as any).steps
      : []
  return {
    ...(meta as RunDetail),
    run_id: String((meta as any).run_id || ''),
    created_at: String((meta as any).created_at || ''),
    status: ((meta as any).status || 'unknown') as RunDetail['status'],
    prompt: String((meta as any).prompt || ''),
    steps,
    tool_calls: Array.isArray((raw as RunDetailResponse).tool_calls)
      ? (raw as RunDetailResponse).tool_calls
      : [],
    tool_call_events: Array.isArray((raw as RunDetailResponse).tool_call_events)
      ? (raw as RunDetailResponse).tool_call_events
      : [],
    artifacts: Array.isArray((raw as RunDetailResponse).artifacts)
      ? (raw as RunDetailResponse).artifacts
      : [],
    llm_usage: Array.isArray((raw as RunDetailResponse).llm_usage)
      ? (raw as RunDetailResponse).llm_usage
      : Array.isArray((meta as any).llm_usage)
        ? (meta as any).llm_usage
        : [],
    session: ((meta as any).session || null) as RunSession | null,
  }
}

interface RunsStore {
  runs: RunSummary[]
  isLoading: boolean
  loaded: boolean
  error: string | null

  /** 缓存的 run detail，按需懒加载 */
  details: Record<string, RunDetail | undefined>
  inboxItems: AgentInboxItem[]
  queueItems: AgentQueueItem[]
  permissionRules: PermissionRule[]
  controlLoading: boolean

  /** 从后端刷新 run 列表（可指定条数上限）。 */
  refresh: (limit?: number) => Promise<void>
  refreshControlPlane: () => Promise<void>
  processQueue: () => Promise<void>
  retryQueueItem: (queueId: string) => Promise<void>
  cancelQueueItem: (queueId: string) => Promise<void>
  removePermissionRule: (ruleId: string) => Promise<void>

  /** 读某条 run 的完整 detail；命中缓存直接返回。 */
  getDetail: (runId: string, forceRefresh?: boolean) => Promise<RunDetail | null>

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
  inboxItems: [],
  queueItems: [],
  permissionRules: [],
  controlLoading: false,

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

  refreshControlPlane: async () => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    set({ controlLoading: true })
    try {
      if (workspacePath) {
        await pythonClient.send('rpc.agent.queue.resume', { workspace_path: workspacePath })
      }
      const [inboxRes, queueRes, rulesRes] = await Promise.all([
        pythonClient.send<{ items: AgentInboxItem[] }>(
          'rpc.agent.inbox.list',
          { workspace_path: workspacePath, limit: 20 },
        ),
        pythonClient.send<{ items: AgentQueueItem[] }>(
          'rpc.agent.queue.list',
          { limit: 20 },
        ),
        workspacePath
          ? pythonClient.send<{ rules: PermissionRule[] }>(
            'rpc.agent.permissions.rules.list',
            { workspace_path: workspacePath },
          )
          : Promise.resolve({ rules: [] }),
      ])
      set({
        inboxItems: Array.isArray(inboxRes?.items) ? inboxRes.items : [],
        queueItems: Array.isArray(queueRes?.items) ? queueRes.items : [],
        permissionRules: Array.isArray(rulesRes?.rules) ? rulesRes.rules : [],
        controlLoading: false,
      })
    } catch (e) {
      console.warn('[runsStore] refreshControlPlane failed:', e)
      set({ controlLoading: false })
    }
  },

  processQueue: async () => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    await pythonClient.send(
      'rpc.agent.queue.process',
      { workspace_path: workspacePath, limit: 1 },
      10 * 60 * 1000,
    )
    await get().refreshControlPlane()
    await get().refresh()
  },

  retryQueueItem: async (queueId) => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    await pythonClient.send('rpc.agent.queue.retry', { queue_id: queueId, workspace_path: workspacePath })
    await get().refreshControlPlane()
  },

  cancelQueueItem: async (queueId) => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    await pythonClient.send('rpc.agent.queue.cancel', { queue_id: queueId, workspace_path: workspacePath })
    await get().refreshControlPlane()
  },

  removePermissionRule: async (ruleId) => {
    const workspacePath = useAssetStore.getState().workspacePath || undefined
    if (!workspacePath) return
    await pythonClient.send('rpc.agent.permissions.rules.remove', {
      workspace_path: workspacePath,
      rule_id: ruleId,
    })
    await get().refreshControlPlane()
  },

  getDetail: async (runId, forceRefresh = false) => {
    const cached = get().details[runId]
    if (cached && !forceRefresh) return cached
    try {
      const workspacePath = useAssetStore.getState().workspacePath || undefined
      const raw = await pythonClient.send<RunDetailResponse>(
        'rpc.runs.get',
        { run_id: runId, workspace_path: workspacePath },
      )
      const detail = normalizeRunDetail(raw)
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
