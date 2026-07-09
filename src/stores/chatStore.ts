/** Chat store. 对话状态管理：消息列表、附件、流式输出。 */
import { create } from 'zustand'
import { v4 as uuid } from 'uuid'
import type { MessagePart, UIMessage } from '@/types/chat'
import { pythonClient } from '@/services/pythonClient'
import {
  loadConversations,
  persistConversation,
  deleteConversationFile,
  flushAllPendingWrites,
} from '@/services/chatPersistence'
import { upsertMessagePart } from '@/services/chatMessageParts'

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

function sayTypeForPart(part: MessagePart): UIMessage['say'] {
  if (part.type === 'reasoning') return 'reasoning'
  if (part.type === 'tool') return 'tool'
  if (part.type === 'tool_output') return 'code_result'
  if (part.type === 'code') return 'code'
  if (part.type === 'artifact') return 'image'
  if (part.type === 'plan') return 'plan'
  if (part.type === 'progress') return 'progress'
  if (part.type === 'error') return 'error'
  return 'text'
}

function messageFromNativePart(part: MessagePart): UIMessage {
  const data = part.data ?? {}
  const step = typeof data.step === 'number' ? data.step : data.stepNumber
  return {
    ts: Date.now(),
    type: 'say',
    say: sayTypeForPart(part),
    text: part.text ?? '',
    partial: part.status === 'running' || part.status === 'streaming',
    runId: part.runId || part.run_id,
    toolName: part.tool,
    toolCallId: part.callId || part.call_id,
    stepNumber: typeof step === 'number' ? step : undefined,
    codeError: typeof data.error === 'string' ? data.error : null,
    durationMs: typeof data.durationMs === 'number'
      ? data.durationMs
      : typeof data.duration_ms === 'number'
        ? data.duration_ms
        : undefined,
    progressStage: typeof data.stage === 'string' ? data.stage : undefined,
    progressDetail: part.text || (typeof data.detail === 'string' ? data.detail : undefined),
    parts: [part],
  }
}

function upsertNativePartIntoConversation(conv: Conversation, part: MessagePart): Conversation {
  if (part.type === 'turn') return conv
  const messages = [...conv.messages]
  const existingIndex = messages.findIndex((message) =>
    message.parts?.some((candidate) => candidate.id === part.id)
  )
  if (existingIndex >= 0) {
    messages[existingIndex] = applyNativePartToMessage(
      upsertMessagePart(messages[existingIndex], part),
      part,
    )
    return { ...conv, messages, updatedAt: Date.now() }
  }
  messages.push(messageFromNativePart(part))
  return { ...conv, messages, updatedAt: Date.now() }
}

function isOpenPartStatus(status: MessagePart['status'] | undefined): boolean {
  return status === 'pending' || status === 'running' || status === 'streaming'
}

function isTurnScopedPart(part: MessagePart): boolean {
  return (
    part.type === 'text' ||
    part.type === 'reasoning' ||
    part.type === 'tool' ||
    part.type === 'tool_output' ||
    part.type === 'code' ||
    part.type === 'plan' ||
    part.type === 'progress'
  )
}

function settleMessagePartStatus(part: MessagePart, mode: 'completed' | 'failed' | 'cancelled'): MessagePart {
  if (!isTurnScopedPart(part)) return part
  if (!isOpenPartStatus(part.status)) return part
  return { ...part, status: mode }
}

function settledToolStatusForMode(
  current: UIMessage['toolStatus'] | undefined,
  mode: 'completed' | 'failed' | 'cancelled',
): UIMessage['toolStatus'] | undefined {
  if (current !== 'pending' && current !== 'running') return current
  return mode === 'failed' || mode === 'cancelled' ? 'failed' : 'completed'
}

function settleConversationForTurnEnd(
  conv: Conversation,
  mode: 'completed' | 'failed' | 'cancelled',
): Conversation {
  let changed = false
  const messages = conv.messages.map((message) => {
    let next = message
    if (message.parts?.length) {
      const parts = message.parts.map((part) => {
        const settled = settleMessagePartStatus(part, mode)
        if (settled !== part) changed = true
        return settled
      })
      next = { ...next, parts }
    }
    if (next.partial) {
      changed = true
      next = { ...next, partial: false }
    }
    if (next.toolStatus === 'pending' || next.toolStatus === 'running') {
      changed = true
      next = { ...next, toolStatus: settledToolStatusForMode(next.toolStatus, mode) }
    }
    return next
  })
  return changed ? { ...conv, messages, updatedAt: Date.now() } : conv
}

function applyNativePartToMessage(message: UIMessage, part: MessagePart): UIMessage {
  const mergedPart = message.parts?.find((candidate) => candidate.id === part.id) ?? part
  const partial = mergedPart.status === 'running' || mergedPart.status === 'streaming'
  const data = mergedPart.data ?? {}
  const updates: Partial<UIMessage> = {
    partial,
    runId: mergedPart.runId || mergedPart.run_id || message.runId,
  }
  if (mergedPart.type === 'text') {
    updates.say = 'text'
    if (mergedPart.text != null) updates.text = mergedPart.text || message.text
  } else if (mergedPart.type === 'reasoning') {
    updates.say = 'reasoning'
    if (mergedPart.text != null) updates.text = mergedPart.text || message.text
  } else if (mergedPart.type === 'code') {
    updates.say = 'code'
    const step = typeof data.step === 'number' ? data.step : data.stepNumber
    if (typeof step === 'number') updates.stepNumber = step
    if (mergedPart.text != null) updates.text = mergedPart.text || message.text
  } else if (mergedPart.type === 'tool') {
    updates.say = 'tool'
    updates.toolName = mergedPart.tool || message.toolName
    updates.toolCallId = mergedPart.callId || mergedPart.call_id || message.toolCallId
    updates.toolStatus = mergedPart.status === 'failed' ? 'failed' : partial ? 'running' : 'completed'
  } else if (mergedPart.type === 'tool_output') {
    updates.say = 'code_result'
    if (mergedPart.text != null) updates.text = mergedPart.text || message.text
    const step = typeof data.step === 'number' ? data.step : data.stepNumber
    if (typeof step === 'number') updates.stepNumber = step
    updates.codeError = typeof data.error === 'string' ? data.error : message.codeError
    updates.durationMs = typeof data.durationMs === 'number'
      ? data.durationMs
      : typeof data.duration_ms === 'number'
        ? data.duration_ms
        : message.durationMs
  } else if (mergedPart.type === 'progress') {
    updates.say = 'progress'
    updates.progressStage = typeof data.stage === 'string' ? data.stage : message.progressStage
    updates.progressDetail = mergedPart.text || (typeof data.detail === 'string' ? data.detail : message.progressDetail)
  } else if (mergedPart.type === 'error') {
    updates.say = 'error'
    updates.text = mergedPart.text || message.text
  }
  return { ...message, ...updates }
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
      case 'chat.message_part': {
        const part = (params?.part ?? params) as MessagePart | undefined
        if (!part?.id || !part.type) break
        if (part.type === 'turn' && part.data?.kind === 'stream_end') {
          const activeConversationId = state.activeConversationId
          if (activeConversationId) {
            const conv = state.activeConversation()
            if (conv) settleRunningStatusCards(conv, state._updateMessage, 'completed')
            set((s) => ({
              conversations: s.conversations.map((conversation) =>
                conversation.id === activeConversationId
                  ? settleConversationForTurnEnd(conversation, 'completed')
                  : conversation
              ),
            }))
          }
          get().setStreaming(false)
          set({ workflowPlanActive: false })
          get()._persistActive()
          break
        }
        set((s) => ({
          conversations: s.conversations.map((conversation) =>
            conversation.id === s.activeConversationId
              ? upsertNativePartIntoConversation(conversation, part)
              : conversation
          ),
        }))
        if (part.type === 'error') {
          const activeConversationId = get().activeConversationId
          const conv = get().activeConversation()
          if (conv) settleRunningStatusCards(conv, get()._updateMessage, 'failed')
          if (activeConversationId) {
            set((s) => ({
              conversations: s.conversations.map((conversation) =>
                conversation.id === activeConversationId
                  ? settleConversationForTurnEnd(conversation, 'failed')
                  : conversation
              ),
            }))
          }
          get().setStreaming(false)
          set({ workflowPlanActive: false })
          get()._persistActive()
        }
        if (part.type === 'tool' && (part.status === 'completed' || part.status === 'failed')) {
          const metadata = ((part.data?.metadata || {}) as Record<string, unknown>)
          const codeStep = metadata.code_step as number | undefined
          const scriptPath = metadata.script_path as string | undefined
          const scriptAbsPath = metadata.script_abs_path as string | undefined
          if (codeStep != null && (scriptPath || scriptAbsPath)) {
            const conv = get().activeConversation()
            if (conv) {
              for (let i = conv.messages.length - 1; i >= 0; i--) {
                const m = conv.messages[i]
                if (m.say === 'code' && m.stepNumber === codeStep) {
                  get()._updateMessage(m.ts, {
                    scriptPath,
                    scriptAbsPath,
                    runId: part.runId || part.run_id,
                  })
                  break
                }
                if (m.say === 'code' && (m.stepNumber ?? 0) < codeStep) break
              }
            }
          }
        }
        break
      }
      case 'chat.cancelled': {
        const activeConversationId = state.activeConversationId
        if (activeConversationId) {
          const cancelConv = state.activeConversation()
          if (cancelConv) settleRunningStatusCards(cancelConv, state._updateMessage, 'cancelled')
          set((s) => ({
            conversations: s.conversations.map((conversation) =>
              conversation.id === activeConversationId
                ? settleConversationForTurnEnd(conversation, 'cancelled')
                : conversation
            ),
          }))
        }
        get().setStreaming(false)
        set({ workflowPlanActive: false })
        get()._persistActive()
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
        // Chat rendering is MessagePart-first. Persisted rows without parts
        // are discarded at load time.
        for (const conv of loaded) {
          conv.messages = conv.messages.filter((msg) => Array.isArray(msg.parts) && msg.parts.length > 0)
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
      // 立即设置 streaming 为 false 以提高 UI 响应速度
      set({ isStreaming: false, isCancelling: true })
      // 添加用户可见的取消消息
      get()._addMessage({
        ts: Date.now(),
        type: 'say',
        say: 'text',
        text: '⏹️ Task cancelled by user.',
      })
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
        void result
      } catch (e) {
        console.warn('[chatStore] rpc.agent.interrupt failed:', e)
      } finally {
        set({ isCancelling: false, workflowPlanActive: false })
      }
    },

    _addMessage: (message) => {
      set((state) => ({
        conversations: state.conversations.map((c) => {
          if (c.id === state.activeConversationId) {
            return {
              ...c,
              messages: [...c.messages, message],
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
                m.ts === ts ? { ...m, ...updates } : m
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
