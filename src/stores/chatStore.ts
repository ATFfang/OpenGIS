/** Chat store. 对话状态管理：消息列表、附件、流式输出。 */
import { create } from 'zustand'
import { v4 as uuid } from 'uuid'
import type { UIMessage } from '@/types/chat'
import { pythonClient } from '@/services/pythonClient'
import {
  loadConversations,
  persistConversation,
  deleteConversationFile,
  flushAllPendingWrites,
} from '@/services/chatPersistence'
import { withProjectedMessageParts } from '@/services/chatMessageParts'

// ─── 附件类型定义 ──────────────────────────────────────────────
export interface ChatAttachment {
  /** 显示名称（文件名） */
  name: string
  /** 磁盘绝对路径 */
  path: string
  /** 文件类型提示 */
  type: 'file' | 'workflow' | 'tool_group'
  /** 文件大小（字节，用于显示） */
  size?: number
  /** 工具组列表（仅 type='tool_group' 时使用） */
  tool_groups?: string[]
}

// ─── 对话类型定义 ──────────────────────────────────────────────
export interface Conversation {
  id: string
  title: string
  messages: UIMessage[]
  createdAt: number
  updatedAt: number
}

// ─── Store 接口定义 ──────────────────────────────────────────────
interface ChatStore {
  conversations: Conversation[]
  activeConversationId: string | null
  isStreaming: boolean
  isCancelling: boolean
  _persistenceReady: boolean
  /** True when a workflow plan is active — suppresses detailed events. */
  workflowPlanActive: boolean

  activeConversation: () => Conversation | null

  createConversation: () => string
  deleteConversation: (id: string) => void
  setActiveConversation: (id: string) => void
  setStreaming: (isStreaming: boolean) => void
  clearConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  resetConversation: () => void

  /** 从工作区磁盘加载对话。在打开工作区时调用。 */
  loadFromDisk: () => Promise<void>
  /** 刷新所有待写入的数据。在切换工作区或关闭应用前调用。
   *  如果提供了 targetWorkspacePath，则写入该路径而不是当前工作区。 */
  flushToDisk: (targetWorkspacePath?: string | null) => Promise<void>

  sendMessage: (text: string, images?: string[], attachments?: ChatAttachment[]) => Promise<void>
  abortTask: (workspacePathOverride?: string | null) => Promise<void>

  _addMessage: (message: UIMessage) => void
  _updateMessage: (ts: number, updates: Partial<UIMessage>) => void
  _persistActive: () => void
}

const STREAM_TEXT_FLUSH_MS = 48
const MAX_LIVE_TOOL_OUTPUT_CHARS = 64 * 1024
let pendingTextAppends = new Map<number, string>()
let pendingTextCaps = new Map<number, number>()
let pendingTextFlushTimer: number | null = null

function appendBoundedText(current: string, append: string, maxChars?: number): string {
  const next = current + append
  if (!maxChars || next.length <= maxChars) return next
  const omitted = next.length - maxChars
  return `[live output truncated: ${omitted.toLocaleString()} chars omitted]\n` + next.slice(-maxChars)
}

function queueTextAppend(
  set: (partial: Partial<ChatStore> | ((state: ChatStore) => Partial<ChatStore>)) => void,
  get: () => ChatStore,
  ts: number,
  piece: string,
  maxChars?: number,
): void {
  if (!piece) return
  pendingTextAppends.set(ts, (pendingTextAppends.get(ts) || '') + piece)
  if (maxChars) pendingTextCaps.set(ts, maxChars)
  if (pendingTextFlushTimer != null) return
  pendingTextFlushTimer = window.setTimeout(() => {
    flushPendingTextAppends(set, get)
  }, STREAM_TEXT_FLUSH_MS)
}

function flushPendingTextAppends(
  set: (partial: Partial<ChatStore> | ((state: ChatStore) => Partial<ChatStore>)) => void,
  get: () => ChatStore,
): void {
  if (pendingTextFlushTimer != null) {
    window.clearTimeout(pendingTextFlushTimer)
    pendingTextFlushTimer = null
  }
  if (pendingTextAppends.size === 0) return
  const appends = pendingTextAppends
  pendingTextAppends = new Map()
  const caps = pendingTextCaps
  pendingTextCaps = new Map()
  set((state) => ({
    conversations: state.conversations.map((c) => {
      if (c.id !== state.activeConversationId) return c
      let changed = false
      const messages = c.messages.map((m) => {
        const append = appends.get(m.ts)
        if (!append) return m
        changed = true
        return withProjectedMessageParts({
          ...m,
          text: appendBoundedText(m.text || '', append, caps.get(m.ts)),
        })
      })
      return changed ? { ...c, messages, updatedAt: Date.now() } : c
    }),
  }))
  void get
}

// ─── 辅助函数：推送当前 LLM 设置到 Python 后端 ──────────────────────
// 仅当配置自上次调用后实际更改时才发送。
let _lastConfigHash: string | null = null

function settleRunningStatusCards(
  conv: Conversation | null,
  updateMessage: (ts: number, updates: Partial<UIMessage>) => void,
  mode: 'completed' | 'failed' | 'cancelled',
): void {
  if (!conv) return
  for (const msg of conv.messages) {
    if (msg.say === 'plan' && msg.planData?.steps?.some((s: any) => s.status === 'in_progress')) {
      updateMessage(msg.ts, {
        planData: {
          ...msg.planData,
          steps: msg.planData.steps.map((s: any) =>
            s.status === 'in_progress'
              ? { ...s, status: mode === 'completed' ? 'done' : 'failed' }
              : s
          ),
          updatedAt: Date.now(),
        },
      })
    }

    if (msg.say === 'subagent' && msg.subagentData?.status === 'running') {
      updateMessage(msg.ts, {
        subagentData: {
          ...msg.subagentData,
          status: mode === 'completed' ? 'done' : mode,
          tasks: msg.subagentData.tasks.map((task: any) =>
            task.status === 'running'
              ? { ...task, status: mode === 'completed' ? 'done' : mode }
              : task
          ),
          updatedAt: Date.now(),
        },
      })
    }
  }
}

async function configureBackendAgent(): Promise<void> {
  const { useSettingsStore } = await import('./settingsStore')
  const settings = useSettingsStore.getState()

  // 计算简单哈希值以检测更改
  const configStr = `${settings.model.protocol}|${settings.model.modelName}|${settings.model.apiKey}|${settings.model.baseURL || ''}|${settings.agent.maxIterations}`
  if (configStr === _lastConfigHash) {
    // 配置未更改 — 跳过 RPC 调用
    return
  }

  try {
    await pythonClient.send('rpc.agent.set_llm_config', {
      protocol: settings.model.protocol,
      model: settings.model.modelName,
      api_key: settings.model.apiKey,
      base_url: settings.model.baseURL || undefined,
      max_iterations: settings.agent.maxIterations,
    })
    _lastConfigHash = configStr
  } catch (e) {
    console.warn('[chatStore] rpc.agent.set_llm_config 失败:', e)
  }
}

// ─── 订阅后端 Agent 通知（一次性，首次访问 store 时） ──────────────────────
let _unsubscribeBridge: (() => void) | null = null

function installNotificationBridge(
  set: (partial: Partial<ChatStore> | ((state: ChatStore) => Partial<ChatStore>)) => void,
  get: () => ChatStore,
) {
  // 如果已经安装，先取消旧的订阅，防止内存泄漏
  if (_unsubscribeBridge) {
    _unsubscribeBridge()
    _unsubscribeBridge = null
  }

  _unsubscribeBridge = pythonClient.onNotification((method, params) => {
    const state = get()
    if (!state.activeConversationId) return

    switch (method) {
      case 'chat.stream_delta': {
        // 追加到（或创建）当前助手消息
        const conv = state.activeConversation()
        const last = conv?.messages[conv.messages.length - 1]
        const content = (params?.content as string) ?? ''
        if (last && last.type === 'say' && last.say === 'text' && last.partial) {
          queueTextAppend(set, get, last.ts, content)
        } else {
          // 添加文本前清除所有残留的 thinking 消息
          if (last && last.say === 'thinking' && last.partial) {
            state._updateMessage(last.ts, { partial: false })
          }
          state._addMessage({
            ts: Date.now(),
            type: 'say',
            say: 'text',
            text: content,
            partial: true,
          })
        }
        break
      }
      case 'chat.stream_end': {
        flushPendingTextAppends(set, get)
        const conv = state.activeConversation()
        if (conv) {
          // 移除所有残留的 progress / thinking 消息
          for (let i = conv.messages.length - 1; i >= 0; i--) {
            const msg = conv.messages[i]
            if ((msg.say === 'progress' || msg.say === 'thinking') && msg.partial) {
              state._updateMessage(msg.ts, { partial: false })
              break
            }
          }
          // 完成部分文本消息
          const last = conv.messages[conv.messages.length - 1]
          if (last && last.partial && last.say !== 'progress' && last.say !== 'thinking') {
            state._updateMessage(last.ts, { partial: false })
          }
          settleRunningStatusCards(conv, state._updateMessage, 'completed')
        }
        get().setStreaming(false)
        set({ workflowPlanActive: false })
        break
      }
      case 'chat.code_block': {
        flushPendingTextAppends(set, get)
        // 参数: { step, code, script_path, script_abs_path, run_id }
        // 如果已经在此步骤的代码流中，只需完成
        // 现有部分代码消息即可（保留时间戳以便
        // 行的展开状态保持稳定）。否则添加新消息。
        const convCb = state.activeConversation()
        const stepNum = params?.step as number | undefined
        let mergedIntoExisting = false

        if (convCb && stepNum != null) {
          for (let i = convCb.messages.length - 1; i >= 0; i--) {
            const m = convCb.messages[i]
            if (m.say === 'code' && m.stepNumber === stepNum) {
              // Merge into existing message (whether partial or already finished)
              state._updateMessage(m.ts, {
                text: params?.code as string,
                scriptPath: params?.script_path as string | undefined,
                scriptAbsPath: params?.script_abs_path as string | undefined,
                runId: params?.run_id as string | undefined,
                partial: false,
              })
              mergedIntoExisting = true
              break
            }
            // 停止扫描，一旦遇到比此步骤更旧的内容
            if (m.say === 'code' && (m.stepNumber ?? 0) < stepNum) break
          }
        }
        if (mergedIntoExisting) {
          // Still need to remove any lingering progress / thinking message
          if (convCb) {
            const lastCb = convCb.messages[convCb.messages.length - 1]
            if (lastCb && (lastCb.say === 'progress' || lastCb.say === 'thinking')) {
              state._updateMessage(lastCb.ts, { partial: false })
            }
          }
          break
        }

        // Remove any lingering progress / thinking message
        if (convCb) {
          const lastCb = convCb.messages[convCb.messages.length - 1]
          if (lastCb && (lastCb.say === 'progress' || lastCb.say === 'thinking')) {
            state._updateMessage(lastCb.ts, { partial: false })
          }
        }
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'code',
          text: params?.code as string,
          stepNumber: stepNum,
          scriptPath: params?.script_path as string | undefined,
          scriptAbsPath: params?.script_abs_path as string | undefined,
          runId: params?.run_id as string | undefined,
        })
        break
      }
      case 'chat.code_block_start': {
        // 参数: { step, run_id }
        // LLM 刚刚打开了 ```python 围栏。创建一个部分代码
        // 消息，以便 UI 立即开始流入。
        const convCs = state.activeConversation()
        const stepNumS = params?.step as number | undefined

        // 隐藏任何 thinking/progress  spinner — 代码正在到达
        if (convCs) {
          const lastCs = convCs.messages[convCs.messages.length - 1]
          if (lastCs && (lastCs.say === 'progress' || lastCs.say === 'thinking') && lastCs.partial) {
            state._updateMessage(lastCs.ts, { partial: false })
          }
        }

        // 如果此步骤的部分消息已存在，不要重复创建
        if (convCs && stepNumS != null) {
          const dup = convCs.messages.some(
            (m) => m.say === 'code' && m.stepNumber === stepNumS && m.partial,
          )
          if (dup) break
        }
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'code',
          text: '',
          stepNumber: stepNumS,
          runId: params?.run_id as string | undefined,
          partial: true,
        })
        break
      }
      case 'chat.code_delta': {
        // 参数: { step, delta, run_id }
        // 追加到此步骤的部分代码消息
        const convCd = state.activeConversation()
        const stepNumD = params?.step as number | undefined
        const piece = (params?.delta as string) ?? ''
        if (!convCd || stepNumD == null || !piece) break
        for (let i = convCd.messages.length - 1; i >= 0; i--) {
          const m = convCd.messages[i]
          if (m.say === 'code' && m.stepNumber === stepNumD && m.partial) {
            queueTextAppend(set, get, m.ts, piece)
            break
          }
          if (m.say === 'code' && (m.stepNumber ?? 0) < stepNumD) break
        }
        break
      }
      case 'chat.code_block_end': {
        flushPendingTextAppends(set, get)
        // 参数: { step, run_id }
        // LLM 完成了围栏写入。标记部分消息完成
        // — 前端将在短暂延迟后动画折叠它。
        // 我们保持 partial=true 一个额外的节拍，以便 UI 可以读取它
        // 为"刚刚完成"；在这里翻转为 false 会触发折叠。
        const convCe = state.activeConversation()
        const stepNumE = params?.step as number | undefined
        if (!convCe || stepNumE == null) break
        for (let i = convCe.messages.length - 1; i >= 0; i--) {
          const m = convCe.messages[i]
          if (m.say === 'code' && m.stepNumber === stepNumE && m.partial) {
            state._updateMessage(m.ts, { partial: false })
            break
          }
          if (m.say === 'code' && (m.stepNumber ?? 0) < stepNumE) break
        }
        break
      }
      case 'chat.reasoning_delta': {
        // 参数: { delta, round?, open?, run_id }
        // 将代码前思考的块流入可折叠的
        // "Thinking" 气泡。追加到最近的部分
        // reasoning 消息；如果不存在（或 `open` 为 true），开始
        // 一个新的。
        const convRd = state.activeConversation()
        if (!convRd) break
        const piece = (params?.delta as string) ?? ''
        const wantsOpen = params?.open === true
        // 隐藏任何 thinking/progress spinner — reasoning 已到达
        const lastRd = convRd.messages[convRd.messages.length - 1]
        if (lastRd && (lastRd.say === 'progress' || lastRd.say === 'thinking') && lastRd.partial) {
          state._updateMessage(lastRd.ts, { partial: false })
        }
        const refreshed = state.activeConversation()
        const lastReason = refreshed?.messages[(refreshed?.messages.length ?? 0) - 1]
        if (!wantsOpen && lastReason && lastReason.say === 'reasoning' && lastReason.partial) {
          if (piece) queueTextAppend(set, get, lastReason.ts, piece)
        } else {
          state._addMessage({
            ts: Date.now(),
            type: 'say',
            say: 'reasoning',
            text: piece,
            partial: true,
            runId: params?.run_id as string | undefined,
          })
        }
        break
      }
      case 'chat.reasoning_end': {
        flushPendingTextAppends(set, get)
        // 参数: { round, run_id }
        // 关闭当前部分 reasoning 气泡 — UI 将
        // 动画折叠它。
        const convRe = state.activeConversation()
        if (!convRe) break
        for (let i = convRe.messages.length - 1; i >= 0; i--) {
          const m = convRe.messages[i]
          if (m.say === 'reasoning' && m.partial) {
            state._updateMessage(m.ts, { partial: false })
            break
          }
          if (m.say === 'code' || m.say === 'text') break
        }
        break
      }
      case 'chat.reasoning_promote': {
        flushPendingTextAppends(set, get)
        // 参数: { round, run_id }
        // 轮次结束但没有代码 — 我们作为 "reasoning" 流式传输的内容
        // 实际上是最终答案。就地转换气泡。
        const convRp = state.activeConversation()
        if (!convRp) break
        for (let i = convRp.messages.length - 1; i >= 0; i--) {
          const m = convRp.messages[i]
          if (m.say === 'reasoning' && m.partial) {
            state._updateMessage(m.ts, { say: 'text', partial: false })
            break
          }
          if (m.say === 'code' || m.say === 'text') break
        }
        break
      }
      case 'chat.code_result': {
        // 参数: { step, output, error, run_id, duration_ms }
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'code_result',
          text: (params?.output as string) || '',
          stepNumber: params?.step as number | undefined,
          codeError: (params?.error as string | null) ?? null,
          runId: params?.run_id as string | undefined,
          durationMs: params?.duration_ms as number | undefined,
        })
        break
      }
      case 'chat.tool_start': {
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'tool',
          toolName: params?.name as string,
          toolCallId: params?.call_id as string | undefined,
          toolArgs: params?.args as Record<string, unknown>,
          toolStatus: 'running',
        })
        break
      }
      case 'chat.tool_output_delta': {
        const conv = state.activeConversation()
        const callId = params?.call_id as string | undefined
        const piece = (params?.delta as string) ?? ''
        if (!conv || !callId || !piece) break
        for (let i = conv.messages.length - 1; i >= 0; i--) {
          const m = conv.messages[i]
          if (m.say === 'tool' && m.toolCallId === callId) {
            queueTextAppend(set, get, m.ts, piece, MAX_LIVE_TOOL_OUTPUT_CHARS)
            break
          }
        }
        break
      }
      case 'chat.tool_result': {
        flushPendingTextAppends(set, get)
        const conv = state.activeConversation()
        const metadata = (params?.metadata || {}) as Record<string, unknown>
        const codeStep = metadata.code_step as number | undefined
        const scriptPath = metadata.script_path as string | undefined
        const scriptAbsPath = metadata.script_abs_path as string | undefined
        const callId = params?.call_id as string | undefined
        // 找到最近的运行中工具消息并标记为完成
        if (conv) {
          let updated = false
          if (callId) {
            for (let i = conv.messages.length - 1; i >= 0; i--) {
              const m = conv.messages[i]
              if (m.say === 'tool' && m.toolCallId === callId) {
                state._updateMessage(m.ts, {
                  toolStatus: params?.error ? 'failed' : 'completed',
                  text: params?.output as string,
                  durationMs: params?.duration_ms as number | undefined,
                })
                updated = true
                break
              }
            }
          }
          for (let i = conv.messages.length - 1; i >= 0; i--) {
            const m = conv.messages[i]
            if (!updated && m.say === 'tool' && m.toolStatus === 'running') {
              state._updateMessage(m.ts, {
                toolStatus: params?.error ? 'failed' : 'completed',
                text: params?.output as string,
                durationMs: params?.duration_ms as number | undefined,
              })
              break
            }
          }
          if (codeStep != null && (scriptPath || scriptAbsPath)) {
            for (let i = conv.messages.length - 1; i >= 0; i--) {
              const m = conv.messages[i]
              if (m.say === 'code' && m.stepNumber === codeStep) {
                state._updateMessage(m.ts, {
                  scriptPath,
                  scriptAbsPath,
                  runId: params?.run_id as string | undefined,
                })
                break
              }
              if (m.say === 'code' && (m.stepNumber ?? 0) < codeStep) break
            }
          }
        }
        break
      }
      case 'chat.error': {
        flushPendingTextAppends(set, get)
        settleRunningStatusCards(state.activeConversation(), state._updateMessage, 'failed')
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'error',
          text: (params?.error as string) || 'Unknown error',
        })
        get().setStreaming(false)
        set({ workflowPlanActive: false })
        break
      }
      case 'chat.max_steps_reached': {
        // 软停止：agent 用尽了每次运行的步骤预算。
        // 摘要已通过 chat.stream_delta 推送，因此
        // 此消息仅添加"继续？"功能。
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'max_steps_reached',
          maxStepsInfo: {
            maxSteps: Number(params?.max_steps ?? 0),
            stepCount: Number(params?.step_count ?? 0),
            summary: (params?.summary as string) || '',
          },
          runId: params?.run_id as string | undefined,
        })
        // 注意：stream_end 会立即由后端发出，它将
        // 关闭 isStreaming — 无需在此处执行
        break
      }
      case 'chat.cancelled': {
        flushPendingTextAppends(set, get)
        // Clean up all partial UI state — same as stream_end.
        // Without this, thinking bubbles, progress bars, and subagent
        // cards stay stuck in their "running" state after cancel.
        const cancelConv = state.activeConversation()
        if (cancelConv) {
          for (const msg of cancelConv.messages) {
            // Mark partial thinking / progress as finished
            if ((msg.say === 'progress' || msg.say === 'thinking') && msg.partial) {
              state._updateMessage(msg.ts, { partial: false })
            }
          }
          settleRunningStatusCards(cancelConv, state._updateMessage, 'cancelled')
          // Close any partial assistant text
          const last = cancelConv.messages[cancelConv.messages.length - 1]
          if (last && last.partial && last.say !== 'progress' && last.say !== 'thinking') {
            state._updateMessage(last.ts, { partial: false })
          }
        }
        get().setStreaming(false)
        set({ workflowPlanActive: false })
        break
      }
      case 'chat.progress': {
        // 进度指示器：更新或添加进度消息
        // 首先，清除所有残留的 thinking 消息
        const conv = state.activeConversation()
        const last = conv?.messages[conv.messages.length - 1]
        const stage = (params?.stage as string) || 'processing'
        const progressDetail = (params?.message as string) || ''
        if (last && last.say === 'thinking' && last.partial) {
          // 用进度替换 thinking
          state._updateMessage(last.ts, { partial: false })
        }
        const convAfter = state.activeConversation()
        const lastAfter = convAfter?.messages[convAfter.messages.length - 1]
        if (lastAfter && lastAfter.say === 'progress') {
          // 更新现有进度消息
          state._updateMessage(lastAfter.ts, { progressStage: stage, progressDetail, partial: true })
        } else {
          state._addMessage({
            ts: Date.now(),
            type: 'say',
            say: 'progress',
            progressStage: stage,
            progressDetail,
            partial: true,
          })
        }
        break
      }
      case 'chat.title_generated': {
        // 从 LLM 自动生成的标题
        const convId = params?.conversation_id as string
        const title = params?.title as string
        if (convId && title) {
          set((s) => ({
            conversations: s.conversations.map((c) =>
              c.id === convId ? { ...c, title } : c
            ),
          }))
          get()._persistActive()
        }
        break
      }
      // rpc.ui.map.* 通知由 src/services/rpc/ 处理函数消费，
      // 不是这里。
    }
  })
}

// ─── Store 实现 ──────────────────────────────────────────────
export const useChatStore = create<ChatStore>((set, get) => {
  // 延迟桥接安装，直到首次使用 store，以便
  // pythonClient 有机会连接。
  setTimeout(() => installNotificationBridge(set, get), 0)

  return {
    conversations: [],
    activeConversationId: null,
    isStreaming: false,
    isCancelling: false,
    workflowPlanActive: false,
    _persistenceReady: false,

    activeConversation: () => {
      const state = get()
      return state.conversations.find((c) => c.id === state.activeConversationId) ?? null
    },

    createConversation: () => {
      const id = uuid()
      const conversation: Conversation = {
        id,
        title: 'New Conversation',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      set((state) => ({
        conversations: [conversation, ...state.conversations],
        activeConversationId: id,
        workflowPlanActive: false,
      }))
      return id
    },

    deleteConversation: (id) => {
      // 从磁盘删除
      import('@/stores/assetStore').then(({ useAssetStore }) => {
        const wp = useAssetStore.getState().workspacePath
        deleteConversationFile(wp, id)
      })
      set((state) => {
        const conversations = state.conversations.filter((c) => c.id !== id)
        const newActiveId = state.activeConversationId === id
          ? conversations[0]?.id ?? null
          : state.activeConversationId
        // Check if the new active conversation has an active workflow plan
        const newActiveConv = conversations.find((c) => c.id === newActiveId)
        const hasWorkflowPlan = newActiveConv?.messages.some(
          (m) => m.say === 'plan' && m.planData?.steps?.some((s: any) => s.status === 'in_progress')
        ) ?? false
        return {
          conversations,
          activeConversationId: newActiveId,
          workflowPlanActive: hasWorkflowPlan,
        }
      })
    },

    setActiveConversation: (id) => {
      // Check if the target conversation has an active workflow plan
      const conv = get().conversations.find((c) => c.id === id)
      const hasWorkflowPlan = conv?.messages.some(
        (m) => m.say === 'plan' && m.planData?.steps?.some((s: any) => s.status === 'in_progress')
      ) ?? false
      set({ activeConversationId: id, workflowPlanActive: hasWorkflowPlan })
    },
    setStreaming: (isStreaming) => set({ isStreaming, isCancelling: false }),

    renameConversation: (id, title) => {
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, title, updatedAt: Date.now() } : c
        ),
      }))
      get()._persistActive()
    },

    clearConversation: (id) => {
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, messages: [], updatedAt: Date.now() } : c
        ),
      }))
      get()._persistActive()
    },

    resetConversation: () => {
      const state = get()
      if (state.activeConversationId) {
        import('./assetStore').then(({ useAssetStore }) => {
          const workspacePath = useAssetStore.getState().workspacePath
          pythonClient.send('rpc.agent.interrupt', {
            workspace_path: workspacePath || undefined,
          }).catch(() => {})
        })
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === s.activeConversationId
              ? { ...c, messages: [], updatedAt: Date.now() }
              : c
          ),
          isStreaming: false,
          isCancelling: false,
          workflowPlanActive: false,
        }))
        get()._persistActive()
      }
    },

    loadFromDisk: async () => {
      const { useAssetStore } = await import('@/stores/assetStore')
      const workspacePath = useAssetStore.getState().workspacePath
      if (!workspacePath) {
        set({ _persistenceReady: false })
        return
      }
      try {
        const loaded = await loadConversations(workspacePath)
        // Mark pending screenshot cards as expired — the backend session
        // that created them is gone, so they can never be captured.
        for (const conv of loaded) {
          conv.messages = conv.messages.map((msg) => {
            if (msg.say === 'screenshot' && msg.screenshotData) {
              return withProjectedMessageParts({
                ...msg,
                say: 'text',
                text: '📸 [截图已过期]',
                screenshotData: undefined,
              })
            }
            return withProjectedMessageParts(msg)
          })
        }
        set({
          conversations: loaded,
          activeConversationId: loaded[0]?.id ?? null,
          _persistenceReady: true,
        })
      } catch (e) {
        console.error('[chatStore] loadFromDisk 失败:', e)
        set({ _persistenceReady: true })
      }
    },

    flushToDisk: async (targetWorkspacePath?: string | null) => {
      const wp = targetWorkspacePath !== undefined
        ? targetWorkspacePath
        : (await import('@/stores/assetStore')).useAssetStore.getState().workspacePath
      await flushAllPendingWrites(wp, get().conversations)
    },

    sendMessage: async (text, images, attachments) => {
      const state = get()
      if (state.isStreaming || state.isCancelling) {
        console.warn('[chatStore] sendMessage ignored while agent is busy/cancelling')
        return
      }
      let conversationId = state.activeConversationId
      if (!conversationId) {
        conversationId = get().createConversation()
      }

      // 根据第一条消息自动生成标题
      const conv = get().conversations.find((c) => c.id === conversationId)
      const isFirstUserMessage = !conv || !conv.messages.some((m) => m.say === 'user_feedback')
      if (conv && isFirstUserMessage) {
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, title: text.slice(0, 50) + (text.length > 50 ? '...' : '') }
              : c
          ),
        }))
      }

      // 立即回显用户消息
      get()._addMessage({
        ts: Date.now(),
        type: 'say',
        say: 'user_feedback',
        text,
        images,
        files: attachments?.map((a) => a.name),
      })

      set({ isStreaming: true, isCancelling: false })

      try {
        // 确保后端在聊天前拥有最新的 LLM 配置
        await configureBackendAgent()

        // 传递当前打开的工作区（如果有），以便后端可以
        // 在 <workspace>/script/ 下持久化 agent 步骤脚本。如果没有工作区，则回退到
        // <appdata>/opengis/agent-runs/<run_id>/。
        const { useAssetStore } = await import('./assetStore')
        const workspacePath = useAssetStore.getState().workspacePath

        if (!workspacePath) {
          get()._addMessage({
            ts: Date.now(),
            type: 'say',
            say: 'error',
            text: '⚠️ 请先打开一个工作目录。Agent 需要工作目录来存储脚本和分析结果。',
          })
          set({ isStreaming: false, isCancelling: false })
          return
        }

        const { useSettingsStore: settingsStore } = await import('./settingsStore')
        const userInstructions = settingsStore.getState().agent.customInstructions || undefined

        await pythonClient.send('chat.user_message', {
          message: text,
          conversation_id: conversationId,
          workspace_path: workspacePath || undefined,
          generate_title: isFirstUserMessage,
          attachments: attachments?.map((a) => {
            const groups = a.tool_groups
            return {
              name: a.name,
              path: a.path,
              type: a.type,
              ...(groups ? { tool_groups: groups } : {}),
            }
          }) || undefined,
          user_instructions: userInstructions,
        })
      } catch (error: any) {
        console.error('[chatStore] chat.user_message 失败:', error)
        const rawMsg = error?.message || String(error)
        // 人性化常见的前端错误
        let friendlyMsg = rawMsg
        if (rawMsg.includes('WebSocket') || rawMsg.includes('not connected')) {
          friendlyMsg = '⚠️ 后端服务未连接，请检查 Python 后端是否正常运行。'
        } else if (rawMsg.includes('timeout')) {
          friendlyMsg = '⚠️ 请求超时，请检查网络连接后重试。'
        } else {
          friendlyMsg = `⚠️ 发送失败：${rawMsg}`
        }
        get()._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'error',
          text: friendlyMsg,
        })
        set({ isStreaming: false, isCancelling: false, workflowPlanActive: false })
      }
    },

    abortTask: async (workspacePathOverride?: string | null) => {
      const t0 = performance.now()
      console.log(`[ABORT-DEBUG][${new Date().toISOString()}] abortTask called, isStreaming=${get().isStreaming}`)
      // 立即设置 streaming 为 false 以提高 UI 响应速度
      set({ isStreaming: false, isCancelling: true })
      console.log(`[ABORT-DEBUG] +${(performance.now()-t0).toFixed(1)}ms set isStreaming=false,isCancelling=true`)
      // 添加用户可见的取消消息
      get()._addMessage({
        ts: Date.now(),
        type: 'say',
        say: 'text',
        text: '⏹️ Task cancelled by user.',
      })
      console.log(`[ABORT-DEBUG] +${(performance.now()-t0).toFixed(1)}ms sending rpc.agent.interrupt...`)
      // 告诉后端终止进程并释放锁
      // 我们等待此操作，以便后端有时间清理，
      // 然后再让用户发送下一条消息
      try {
        const { useAssetStore } = await import('./assetStore')
        const workspacePath = workspacePathOverride !== undefined
          ? workspacePathOverride
          : useAssetStore.getState().workspacePath
        const result = await pythonClient.send('rpc.agent.interrupt', {
          workspace_path: workspacePath || undefined,
        })
        console.log(`[ABORT-DEBUG] +${(performance.now()-t0).toFixed(1)}ms rpc.agent.interrupt returned:`, result)
      } catch (e) {
        console.warn(`[ABORT-DEBUG] +${(performance.now()-t0).toFixed(1)}ms rpc.agent.interrupt FAILED:`, e)
      } finally {
        set({ isCancelling: false, workflowPlanActive: false })
      }
      console.log(`[ABORT-DEBUG] +${(performance.now()-t0).toFixed(1)}ms abortTask finished`)
    },

    _addMessage: (message) => {
      set((state) => ({
        conversations: state.conversations.map((c) => {
          if (c.id === state.activeConversationId) {
            return {
              ...c,
              messages: [...c.messages, withProjectedMessageParts(message)],
              updatedAt: Date.now(),
            }
          }
          return c
        }),
      }))
      get()._persistActive()
    },

    _updateMessage: (ts, updates) => {
      set((state) => ({
        conversations: state.conversations.map((c) => {
          if (c.id === state.activeConversationId) {
            return {
              ...c,
              messages: c.messages.map((m) =>
                m.ts === ts ? withProjectedMessageParts({ ...m, ...updates }) : m
              ),
            }
          }
          return c
        }),
      }))
      // 仅在消息最终确定时持久化（不是 partial/streaming）
      if (!updates.partial) {
        get()._persistActive()
      }
    },

    _persistActive: () => {
      const state = get()
      if (!state._persistenceReady) return
      const conv = state.activeConversation()
      if (!conv) return
      import('@/stores/assetStore').then(({ useAssetStore }) => {
        const wp = useAssetStore.getState().workspacePath
        persistConversation(wp, conv)
      })
    },
  }
})

// ─── 当工作区更改时自动加载对话 ──────────────────────
import { useAssetStore } from '@/stores/assetStore'

useAssetStore.subscribe((state, prev) => {
  if (state.workspacePath !== prev.workspacePath) {
    const store = useChatStore.getState()
    // 将旧工作区对话刷新到旧工作区路径，
    // 然后在从新工作区加载之前清除内存状态。
    const doLoad = () => {
      // 清除内存中的对话，以便过期数据永远不会被待处理的防抖写入
      // 写入新工作区
      useChatStore.setState({
        conversations: [],
        activeConversationId: null,
        isStreaming: false,
        isCancelling: false,
        workflowPlanActive: false,
        _persistenceReady: false,
      })
      useChatStore.getState().loadFromDisk()
    }
    const settleCurrentRun = store.isStreaming || store.isCancelling
      ? store.abortTask(prev.workspacePath).catch((e) => {
          console.warn('[chatStore] failed to abort active run before workspace switch:', e)
        })
      : Promise.resolve()
    const flushOldWorkspace = prev.workspacePath
      ? settleCurrentRun.then(() => store.flushToDisk(prev.workspacePath))
      : settleCurrentRun
    flushOldWorkspace.then(doLoad).catch((e) => {
      console.error('[chatStore] failed to switch workspace conversations:', e)
      doLoad()
    })
  }
})

// 如果加载此模块时工作区已设置，则触发初始加载
setTimeout(() => {
  const wp = useAssetStore.getState().workspacePath
  if (wp) {
    useChatStore.getState().loadFromDisk()
  }
}, 100)
