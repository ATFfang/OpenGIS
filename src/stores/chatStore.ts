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

// ─── 附件类型定义 ──────────────────────────────────────────────
export interface ChatAttachment {
  /** 显示名称（文件名） */
  name: string
  /** 磁盘绝对路径 */
  path: string
  /** 文件类型提示 */
  type: 'file' | 'workflow' | 'skill'
  /** 文件大小（字节，用于显示） */
  size?: number
  /** 技能组列表（仅 type='skill' 时使用） */
  skill_groups?: string[]
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
  isWaitingForUser: boolean
  _persistenceReady: boolean

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
  abortTask: () => Promise<void>

  _addMessage: (message: UIMessage) => void
  _updateMessage: (ts: number, updates: Partial<UIMessage>) => void
  _persistActive: () => void
}

// ─── 辅助函数：推送当前 LLM 设置到 Python 后端 ──────────────────────
// 仅当配置自上次调用后实际更改时才发送。
let _lastConfigHash: string | null = null

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
let _bridgeInstalled = false
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
  _bridgeInstalled = true

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
          state._updateMessage(last.ts, { text: (last.text || '') + content })
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
        }
        get().setStreaming(false)
        break
      }
      case 'chat.code_block': {
        // 参数: { step, code, script_path, script_abs_path, run_id }
        // 如果已经在此步骤的代码流中，只需完成
        // 现有部分代码消息即可（保留时间戳以便
        // 行的展开状态保持稳定）。否则添加新消息。
        const convCb = state.activeConversation()
        const stepNum = params?.step as number | undefined
        let mergedIntoPartial = false

        if (convCb && stepNum != null) {
          for (let i = convCb.messages.length - 1; i >= 0; i--) {
            const m = convCb.messages[i]
            if (m.say === 'code' && m.stepNumber === stepNum && m.partial) {
              state._updateMessage(m.ts, {
                text: params?.code as string,
                scriptPath: params?.script_path as string | undefined,
                scriptAbsPath: params?.script_abs_path as string | undefined,
                runId: params?.run_id as string | undefined,
                partial: false,
              })
              mergedIntoPartial = true
              break
            }
            // 停止扫描，一旦遇到比此步骤更旧的内容
            if (m.say === 'code' && (m.stepNumber ?? 0) < stepNum) break
          }
        }
        if (mergedIntoPartial) break

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
            state._updateMessage(m.ts, { text: (m.text || '') + piece })
            break
          }
          if (m.say === 'code' && (m.stepNumber ?? 0) < stepNumD) break
        }
        break
      }
      case 'chat.code_block_end': {
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
          if (piece) state._updateMessage(lastReason.ts, { text: (lastReason.text || '') + piece })
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
        // 参数: { step, output, error, run_id }
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'code_result',
          text: (params?.output as string) || '',
          stepNumber: params?.step as number | undefined,
          codeError: (params?.error as string | null) ?? null,
          runId: params?.run_id as string | undefined,
        })
        break
      }
      case 'chat.tool_start': {
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'tool',
          toolName: params?.name as string,
          toolArgs: params?.args as Record<string, unknown>,
          toolStatus: 'running',
        })
        break
      }
      case 'chat.tool_result': {
        const conv = state.activeConversation()
        // 找到最近的运行中工具消息并标记为完成
        if (conv) {
          for (let i = conv.messages.length - 1; i >= 0; i--) {
            const m = conv.messages[i]
            if (m.say === 'tool' && m.toolStatus === 'running') {
              state._updateMessage(m.ts, {
                toolStatus: params?.error ? 'failed' : 'completed',
                text: params?.output as string,
              })
              break
            }
          }
        }
        break
      }
      case 'chat.error': {
        state._addMessage({
          ts: Date.now(),
          type: 'say',
          say: 'error',
          text: (params?.error as string) || 'Unknown error',
        })
        get().setStreaming(false)
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
        get().setStreaming(false)
        break
      }
      case 'chat.progress': {
        // 进度指示器：更新或添加进度消息
        // 首先，清除所有残留的 thinking 消息
        const conv = state.activeConversation()
        const last = conv?.messages[conv.messages.length - 1]
        const stage = (params?.stage as string) || 'processing'
        if (last && last.say === 'thinking' && last.partial) {
          // 用进度替换 thinking
          state._updateMessage(last.ts, { partial: false })
        }
        const convAfter = state.activeConversation()
        const lastAfter = convAfter?.messages[convAfter.messages.length - 1]
        if (lastAfter && lastAfter.say === 'progress') {
          // 更新现有进度消息
          state._updateMessage(lastAfter.ts, { progressStage: stage, partial: true })
        } else {
          state._addMessage({
            ts: Date.now(),
            type: 'say',
            say: 'progress',
            progressStage: stage,
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
    isWaitingForUser: false,
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
        return {
          conversations,
          activeConversationId:
            state.activeConversationId === id
              ? conversations[0]?.id ?? null
              : state.activeConversationId,
        }
      })
    },

    setActiveConversation: (id) => set({ activeConversationId: id }),
    setStreaming: (isStreaming) => set({ isStreaming }),

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
        pythonClient.send('rpc.agent.interrupt', {}).catch(() => {})
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === s.activeConversationId
              ? { ...c, messages: [], updatedAt: Date.now() }
              : c
          ),
          isStreaming: false,
          isWaitingForUser: false,
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
      let conversationId = state.activeConversationId
      if (!conversationId) {
        conversationId = get().createConversation()
      }

      // 根据第一条消息自动生成标题
      const conv = get().conversations.find((c) => c.id === conversationId)
      if (conv && conv.messages.length === 0) {
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

      set({ isStreaming: true })

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
          set({ isStreaming: false })
          return
        }

        await pythonClient.send('chat.user_message', {
          message: text,
          conversation_id: conversationId,
          workspace_path: workspacePath || undefined,
          attachments: attachments?.map((a) => ({
            name: a.name,
            path: a.path,
            type: a.type,
            ...(a.skill_groups ? { skill_groups: a.skill_groups } : {}),
          })) || undefined,
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
        set({ isStreaming: false })
      }
    },

    abortTask: async () => {
      // 立即设置 streaming 为 false 以提高 UI 响应速度
      set({ isStreaming: false })
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
        const result = await pythonClient.send('rpc.agent.interrupt', {})
        console.log('[chatStore] agent 中断结果:', result)
      } catch (e) {
        console.warn('[chatStore] agent 中断失败:', e)
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
      useChatStore.setState({ conversations: [], activeConversationId: null, _persistenceReady: false })
      useChatStore.getState().loadFromDisk()
    }
    if (prev.workspacePath) {
      store.flushToDisk(prev.workspacePath).then(doLoad)
    } else {
      doLoad()
    }
  }
})

// 如果加载此模块时工作区已设置，则触发初始加载
setTimeout(() => {
  const wp = useAssetStore.getState().workspacePath
  if (wp) {
    useChatStore.getState().loadFromDisk()
  }
}, 100)
