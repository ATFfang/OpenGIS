import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  Send,
  Paperclip,
  Square,
  ChevronDown,
  Plus,
  Globe,
  BarChart3,
  Layers,
  Cpu,
  MessageSquare,
  Zap,
  History,
  Trash2,
  Undo2,
  RefreshCw,
  X,
  FileText,
  GitBranch,
  FolderOpen,
  Pencil,
  Wrench,
} from 'lucide-react'
import { useChatStore, type ChatAttachment } from '@/stores/chatStore'
import { useAssetStore } from '@/stores/assetStore'
import { useRunsStore } from '@/stores/runsStore'
import { useWorkflowStore } from '@/stores/workflowStore'
import { useDialog } from '@/components/Dialog'
import { useT } from '@/i18n'
import logoImg from '@/assets/logo.png'
import thinkingGif from '@/assets/thinking.gif'
import machineAvatar from '@/assets/machine.png'
import ChatRow from './components/ChatRow'
import { FileBrowserDialog, type FileBrowserResult } from './components/FileBrowserDialog'
import type { UIMessage } from '@/types/chat'
import { groupMessages, type MessageRole } from './groupMessages'

/**
 * ChatView — Cline-inspired AI chat panel with polished UI.
 *
 * Layout:
 * 1. Header with model indicator and actions
 * 2. Scrollable messages area
 * 3. Sticky input footer with rich controls
 */
export function ChatView() {
  const t = useT()
  const [inputValue, setInputValue] = useState('')
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const [showConversationList, setShowConversationList] = useState(false)
  // 历史输入回放：-1 表示没有在翻历史（停在"当前编辑草稿"）；
  // 0..N-1 表示正在翻第几条历史（从最新往旧数）。
  const [historyIndex, setHistoryIndex] = useState(-1)
  // 翻历史前暂存用户的草稿，按 ↓ 退出历史模式时恢复。
  const draftBeforeHistoryRef = useRef<string>('')
  // Attach file state
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const [showAttachPanel, setShowAttachPanel] = useState(false)
  const [showFileBrowser, setShowFileBrowser] = useState(false)
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [editingTitle, setEditingTitle] = useState('')
  const headerTitleInputRef = useRef<HTMLInputElement>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  const {
    activeConversation,
    activeConversationId,
    conversations,
    isStreaming,
    sendMessage,
    abortTask,
    createConversation,
    setActiveConversation,
  } = useChatStore()

  const conversation = activeConversation()
  const messages: UIMessage[] = conversation?.messages ?? []

  // 把消息按 "一轮 assistant 回答" 分组：两次 user_feedback 之间的所有 assistant
  // 消息（text / reasoning / code / code_result / tool / completion_result / ...）
  // 合成一组，整组只画一个机器人头像。否则 Python 侧一轮回答会同时推 N 条消息，
  // 每条都挂头像，用户会误以为"机器人回答了 N 次"。
  const messageGroups = useMemo(() => groupMessages(messages), [messages])

  // O(1) global index lookup — replaces O(n) indexOf in the render loop.
  const messageIndexMap = useMemo(() => {
    const map = new Map<UIMessage, number>()
    for (let i = 0; i < messages.length; i++) map.set(messages[i], i)
    return map
  }, [messages])

  // 用户已发送消息的历史栈（最新在前），用于 ↑/↓ 在输入框中回放。
  // 说明：ChatRow 里一条 user_feedback 的 text 就是用户当时敲的文本。
  const userHistory = useMemo<string[]>(() => {
    const acc: string[] = []
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.say === 'user_feedback' && m.text) acc.push(m.text)
    }
    return acc
  }, [messages])


  // --- Auto-resize textarea ---
  const adjustTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const adjustTextareaHeight = useCallback(() => {
    // 使用 requestAnimationFrame 批处理，避免多次 keystroke 导致多次 reflow
    if (adjustTimerRef.current) cancelAnimationFrame(adjustTimerRef.current)
    adjustTimerRef.current = requestAnimationFrame(() => {
      const textarea = textAreaRef.current
      if (textarea) {
        textarea.style.height = 'auto'
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px'
      }
    })
  }, [])

  // --- Scroll behavior ---
  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView({ behavior })
  }, [])

  useEffect(() => {
    if (!showScrollToBottom) {
      scrollToBottom('smooth')
    }
  }, [messages.length, scrollToBottom, showScrollToBottom])

  const handleScroll = useCallback(() => {
    if (messagesContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100
      setShowScrollToBottom(prev => (prev !== !isNearBottom ? !isNearBottom : prev))
    }
  }, [])

  useEffect(() => {
    textAreaRef.current?.focus()
    setHistoryIndex(-1)
    draftBeforeHistoryRef.current = ''
  }, [activeConversationId])

  const toggleRowExpansion = useCallback((ts: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(ts)) {
        next.delete(ts)
      } else {
        next.add(ts)
      }
      return next
    })
  }, [])

  // --- Message handlers ---
  const handleSend = useCallback(async () => {
    const trimmed = inputValue.trim()
    if (!trimmed && attachments.length === 0) return
    if (isStreaming) return

    const currentAttachments = attachments.length > 0 ? [...attachments] : undefined
    setInputValue('')
    setAttachments([])
    setShowAttachPanel(false)
    setHistoryIndex(-1)
    draftBeforeHistoryRef.current = ''
    if (textAreaRef.current) {
      textAreaRef.current.style.height = 'auto'
    }

    try {
      await sendMessage(trimmed || '(see attached files)', undefined, currentAttachments)
    } catch (error: any) {
      console.error('[ChatView] Failed to send message:', error)
    }
  }, [inputValue, isStreaming, sendMessage, attachments])

  const handleAttachFile = useCallback(() => {
    setShowFileBrowser(true)
    setShowAttachPanel(false)
  }, [])

  const handleFileBrowserConfirm = useCallback((files: FileBrowserResult[]) => {
    const newAttachments: ChatAttachment[] = files.map((f) => {
      const isWorkflow = f.name.endsWith('.flow.json')
      return { name: f.name, path: f.path, type: isWorkflow ? 'workflow' : 'file' }
    })
    setAttachments((prev) => [...prev, ...newAttachments])
    setShowFileBrowser(false)
  }, [])

  const handleAttachWorkflow = useCallback((entry: { path: string; name: string }) => {
    setAttachments((prev) => [
      ...prev,
      { name: `${entry.name}.flow.json`, path: entry.path, type: 'workflow' },
    ])
    setShowAttachPanel(false)
  }, [])

  const handleAttachSkill = useCallback((name: string, groups: string[]) => {
    setAttachments((prev) => {
      // Remove existing skill attachment with same name (toggle behavior)
      const filtered = prev.filter(a => !(a.type === 'skill' && a.name === name))
      // If already attached, just remove (toggle off)
      if (prev.some(a => a.type === 'skill' && a.name === name)) return filtered
      return [...filtered, { name, path: name.toLowerCase(), type: 'skill' as const, skill_groups: groups }]
    })
  }, [])

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index))
  }, [])

  /**
   * Paste handler: detect file paths copied from file explorer.
   * Patterns matched:
   *   - Windows: C:\Users\...\file.shp  or  "C:\Users\...\file.shp"
   *   - Unix:    /home/user/file.shp
   *   - UNC:     \\server\share\file.shp
   *
   * When a file path is detected, it is:
   *   1. Inserted inline in the textarea as a short reference (📎filename)
   *      so it becomes part of the sentence the user is composing.
   *   2. Added to the attachments list with the full absolute path so the
   *      backend can locate the file.
   */
  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const text = e.clipboardData.getData('text/plain')?.trim()
    if (!text) return

    // Split by newlines and filter empty lines
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)

    // Regex: Windows drive path, UNC path, or Unix absolute path
    // Also strip surrounding quotes (common when copying from explorer address bar)
    const pathRegex = /^"?([A-Za-z]:\\[^"]+|\\\\[^"]+|\/[^"]+)"?$/

    const filePaths: string[] = []
    for (const line of lines) {
      const m = line.match(pathRegex)
      if (m) {
        filePaths.push(m[1])
      }
    }

    // Only intercept if ALL lines are file paths (avoid false positives)
    if (filePaths.length === 0 || filePaths.length !== lines.length) return

    e.preventDefault()

    // Build inline text snippet and attachment entries
    const newAttachments: ChatAttachment[] = []
    const inlineNames: string[] = []

    for (const fp of filePaths) {
      const name = fp.split(/[\\/]/).pop() ?? fp
      const isWorkflow = name.endsWith('.flow.json')
      newAttachments.push({ name, path: fp, type: isWorkflow ? 'workflow' : 'file' })
      inlineNames.push(`📎${name}`)
    }

    // Insert inline references at the current cursor position in the textarea
    const ta = textAreaRef.current
    if (ta) {
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const before = inputValue.slice(0, start)
      const after = inputValue.slice(end)
      // Add a space before/after the inline reference for readability
      const spaceBefore = before.length > 0 && !before.endsWith(' ') && !before.endsWith('\n') ? ' ' : ''
      const spaceAfter = after.length > 0 && !after.startsWith(' ') && !after.startsWith('\n') ? ' ' : ''
      const inlineText = inlineNames.join(' ')
      const newValue = before + spaceBefore + inlineText + spaceAfter + after
      setInputValue(newValue)

      // Move cursor to after the inserted text
      requestAnimationFrame(() => {
        const cursorPos = (before + spaceBefore + inlineText + spaceAfter).length
        ta.selectionStart = cursorPos
        ta.selectionEnd = cursorPos
        adjustTextareaHeight()
      })
    }

    setAttachments((prev) => [...prev, ...newAttachments])
  }, [inputValue, adjustTextareaHeight])

  const handleSuggestionClick = useCallback(async (text: string) => {
    if (isStreaming) return
    try {
      await sendMessage(text)
    } catch (error: any) {
      console.error('[ChatView] Failed to send suggestion:', error)
    }
  }, [isStreaming, sendMessage])

  const applyHistoryValue = useCallback((value: string) => {
    setInputValue(value)
    // 等 React 刷完 DOM 再调 textarea：选区设到末尾，高度重新自适应
    requestAnimationFrame(() => {
      const ta = textAreaRef.current
      if (!ta) return
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
      const end = value.length
      ta.setSelectionRange(end, end)
    })
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter 发送（Shift+Enter 换行）
    // IME 输入法合成中不拦截（中文选词时按回车不应发送）
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
      return
    }

    // ↑ / ↓：翻历史输入。只在"光标处于首行/末行"时接管，避免影响多行编辑里的正常移动。
    const ta = e.currentTarget
    if (e.key === 'ArrowUp' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const caret = ta.selectionStart
      const inFirstLine = ta.value.slice(0, caret).indexOf('\n') === -1
      if (!inFirstLine) return
      if (userHistory.length === 0) return
      e.preventDefault()
      const nextIdx = Math.min(historyIndex + 1, userHistory.length - 1)
      if (historyIndex === -1) {
        // 第一次按 ↑：暂存当前草稿
        draftBeforeHistoryRef.current = inputValue
      }
      if (nextIdx !== historyIndex) {
        setHistoryIndex(nextIdx)
        applyHistoryValue(userHistory[nextIdx])
      }
      return
    }

    if (e.key === 'ArrowDown' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (historyIndex === -1) return // 不在历史模式，交给浏览器默认行为
      const caret = ta.selectionStart
      const inLastLine = ta.value.slice(caret).indexOf('\n') === -1
      if (!inLastLine) return
      e.preventDefault()
      const nextIdx = historyIndex - 1
      if (nextIdx < 0) {
        // 退出历史模式，恢复草稿
        setHistoryIndex(-1)
        applyHistoryValue(draftBeforeHistoryRef.current)
        draftBeforeHistoryRef.current = ''
      } else {
        setHistoryIndex(nextIdx)
        applyHistoryValue(userHistory[nextIdx])
      }
      return
    }
  }, [handleSend, historyIndex, inputValue, userHistory, applyHistoryValue])

  const handleStop = useCallback(() => {
    abortTask()
  }, [abortTask])

  // Determine if we're waiting for a response — show thinking indicator
  // whenever the agent is running and the user might think it's stuck.
  const isWaitingForResponse = useMemo(() => {
    if (!isStreaming) return false
    const lastMsg = messages[messages.length - 1]
    if (!lastMsg) return true
    // Just sent a message — waiting for first response
    if (lastMsg.say === 'user_feedback') return true
    if (lastMsg.say === 'api_req_started') return true
    // Active progress / thinking indicator is already visible — no need for thinking
    if (lastMsg.say === 'progress' && lastMsg.partial) return false
    if (lastMsg.say === 'thinking' && lastMsg.partial) return false
    // After a code_result, the LLM is thinking about the next step
    if (lastMsg.say === 'code_result') return true
    // After a completed (non-partial) text message, LLM may be self-evaluating
    if (lastMsg.say === 'text' && !lastMsg.partial) return true
    return false
  }, [isStreaming, messages])

  const hasTask = messages.length > 0

  // Count tokens from last API request
  const lastApiReq = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].say === 'api_req_started' && messages[i].text) {
        try {
          return JSON.parse(messages[i].text!)
        } catch { return null }
      }
    }
    return null
  }, [messages])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden">
      {/* === Header === */}
<header className="shrink-0 border-b border-border bg-bg-primary/80 backdrop-blur-sm relative z-50">
        <div className="px-4 py-2 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="relative">
              <div className="w-7 h-7 rounded-lg overflow-hidden flex items-center justify-center">
                <img src={logoImg} alt="OpenGIS" className="w-7 h-7 object-contain" />
              </div>
              {isStreaming && (
                <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-accent-success ring-2 ring-bg-primary animate-pulse" />
              )}
            </div>
            <div className="flex flex-col">
              {isEditingTitle && hasTask ? (
                <form
                  className="flex items-center gap-1"
                  onSubmit={(e) => {
                    e.preventDefault()
                    const trimmed = editingTitle.trim()
                    if (trimmed && activeConversationId) {
                      useChatStore.getState().renameConversation(activeConversationId, trimmed)
                    }
                    setIsEditingTitle(false)
                  }}
                >
                  <input
                    ref={headerTitleInputRef}
                    className="text-[13px] font-semibold text-text-primary leading-tight bg-bg-secondary border border-border rounded px-1.5 py-0.5 outline-none focus:border-accent-primary w-[160px]"
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onBlur={() => {
                      const trimmed = editingTitle.trim()
                      if (trimmed && activeConversationId) {
                        useChatStore.getState().renameConversation(activeConversationId, trimmed)
                      }
                      setIsEditingTitle(false)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setIsEditingTitle(false)
                    }}
                    autoFocus
                  />
                </form>
              ) : (
                <span
                  className="text-[13px] font-semibold text-text-primary leading-tight cursor-pointer hover:text-accent-primary transition-colors"
                  onDoubleClick={() => {
                    if (hasTask && conversation) {
                      setEditingTitle(conversation.title || t.chat.newConversation)
                      setIsEditingTitle(true)
                    }
                  }}
                  title="Double-click to rename"
                >
                  {hasTask ? (conversation?.title || t.chat.newConversation) : 'OpenGIS Agent'}
                </span>
              )}
              <span className="text-[10px] text-text-muted leading-tight mt-0.5">
                {isStreaming ? (
                  <span className="text-accent-primary font-medium">{t.chat.progress.generating}</span>
                ) : (
                  'Powered by LLM'
                )}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {lastApiReq?.tokensIn != null && (
              <div className="hidden sm:flex items-center gap-1 px-2 py-1 rounded-md bg-bg-secondary text-[10px] text-text-muted mr-1">
                <Cpu className="w-3 h-3" />
                <span>{lastApiReq.tokensIn + (lastApiReq.tokensOut || 0)} tokens</span>
              </div>
            )}
            {/* Conversation history toggle */}
            {conversations.length > 1 && (
              <div className="relative">
                <button
                  onClick={() => setShowConversationList(!showConversationList)}
                  className="p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-all duration-150"
                  title="Conversation history"
                >
                  <History className="w-4 h-4" />
                </button>
                {showConversationList && (
                  <ConversationListDropdown
                    conversations={conversations}
                    activeId={activeConversationId}
                    onSelect={(id) => {
                      setActiveConversation(id)
                      setShowConversationList(false)
                    }}
                    onDelete={(id) => {
                      useChatStore.getState().deleteConversation(id)
                    }}
                    onClose={() => setShowConversationList(false)}
                  />
                )}
              </div>
            )}
            {/* New conversation button */}
            <button
              onClick={() => createConversation()}
              className="p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-all duration-150"
              title={t.chat.newConversation}
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>
      </header>

      {/* === Messages Area === */}
      {hasTask ? (
        <div
          ref={messagesContainerRef}
          onScroll={handleScroll}
          data-chat-scroll
          className="flex-1 overflow-y-auto overflow-x-hidden"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--text-muted) transparent' }}
        >
          <div className="py-3">
            {messageGroups.map((group, gi) => {
              // Find the first item in this group that carries a run_id.
              // Only assistant groups will have code/code_result/max_steps
              // messages with this field populated — user / system groups
              // will produce `undefined` and therefore hide the revert button.
              const runId = group.items.find((m) => m.runId)?.runId
              return (
                <MessageGroup
                  key={`g-${gi}-${group.items[0]?.ts ?? gi}`}
                  role={group.role}
                  runId={runId}
                >
                  {group.items.map((msg, index) => {
                    // 全局索引（在原始 messages 里的位置），用于 isLast 判定。
                    const globalIndex = messageIndexMap.get(msg) ?? 0
                    return (
                      <ChatRow
                        key={`${msg.ts}-${index}`}
                        message={msg}
                        isExpanded={expandedRows.has(msg.ts)}
                        onToggleExpand={toggleRowExpansion}
                        isLast={globalIndex === messages.length - 1}
                      />
                    )
                  })}
                </MessageGroup>
              )
            })}

            {/* Typing indicator */}
            {isWaitingForResponse && (
              <div className="px-5 py-3">
                <div className="flex items-start gap-3">
                  <div className="w-7 h-7 rounded-lg overflow-hidden shrink-0 ring-1 ring-accent-primary/10">
                    <img src={machineAvatar} alt="Bot" className="w-full h-full object-cover" />
                  </div>
                  <div className="flex items-center gap-2 pt-1">
                    <img src={thinkingGif} alt="Thinking" className="w-5 h-5" />
                    <span className="text-xs text-text-muted">{t.chat.thinking}...</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div ref={messagesEndRef} className="min-h-1" />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <WelcomeContent onSuggestionClick={handleSuggestionClick} />
        </div>
      )}

      {/* === Footer === */}
      <footer className="shrink-0 border-t border-border bg-bg-primary">
        {/* Scroll to bottom */}
        {showScrollToBottom && hasTask && (
          <div className="flex justify-center py-1.5">
            <button
              onClick={() => {
                scrollToBottom('smooth')
                setShowScrollToBottom(false)
              }}
              className="px-3 py-1 rounded-full bg-bg-secondary/80 backdrop-blur-sm border border-border text-text-muted hover:text-text-primary hover:border-accent-primary/30 text-xs transition-all shadow-sm hover:shadow-md"
            >
              <ChevronDown className="w-3 h-3 inline mr-1" />
              Scroll to bottom
            </button>
          </div>
        )}

        {/* Input area */}
        <div className="p-3 pt-2 relative">
          <div className="relative bg-bg-secondary rounded-2xl border border-border focus-within:border-accent-primary/40 focus-within:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all duration-200">
            {/* Attached files chips */}
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5 px-3 pt-2 pb-0">
                {attachments.map((att, i) => (
                  <div
                    key={`${att.path}-${i}`}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-accent-primary/10 border border-accent-primary/25 text-[11px] text-text-secondary group"
                  >
                    {att.type === 'workflow' ? (
                      <GitBranch className="w-3 h-3 text-accent-primary shrink-0" />
                    ) : att.type === 'skill' ? (
                      <Wrench className="w-3 h-3 text-amber-400 shrink-0" />
                    ) : (
                      <FileText className="w-3 h-3 text-text-muted shrink-0" />
                    )}
                    <span className="max-w-[120px] truncate">{att.name}</span>
                    <button
                      onClick={() => removeAttachment(i)}
                      className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-text-muted/60 hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
                    >
                      <X className="w-2.5 h-2.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <textarea
              ref={textAreaRef}
              value={inputValue}
              onChange={(e) => {
                setInputValue(e.target.value)
                adjustTextareaHeight()
                // 用户主动编辑 → 退出历史模式（下次按 ↑ 重新暂存草稿）
                if (historyIndex !== -1) {
                  setHistoryIndex(-1)
                  draftBeforeHistoryRef.current = ''
                }
              }}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              spellCheck={false}
              placeholder={hasTask ? t.chat.placeholder : t.chat.placeholder}
              rows={1}
              className="w-full bg-transparent text-[13px] text-text-primary placeholder:text-text-muted/50 py-2.5 pl-4 pr-24 resize-none outline-none max-h-[200px] overflow-y-auto leading-6 align-middle"
              style={{
                minHeight: '44px',
                scrollbarWidth: 'none',
                fontFamily: 'inherit',
              }}
            />

            {/* Action buttons */}
            <div className="absolute right-2 bottom-2 flex items-center gap-0.5">
              <button
                onClick={() => setShowAttachPanel(!showAttachPanel)}
                className={`p-2 rounded-lg transition-all duration-150 ${
                  showAttachPanel || attachments.length > 0
                    ? 'text-accent-primary bg-accent-primary/10'
                    : 'text-text-muted/40 hover:text-text-secondary hover:bg-bg-hover'
                }`}
                title={t.chat.attachFile}
              >
                <Paperclip className="w-4 h-4" />
              </button>

              {isStreaming ? (
                <button
                  onClick={handleStop}
                  className="p-2 bg-accent-danger/10 text-accent-danger hover:bg-accent-danger/20 rounded-lg transition-all duration-150 ring-1 ring-accent-danger/20"
                  title="Stop generation"
                >
                  <Square className="w-3.5 h-3.5" />
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!inputValue.trim() && attachments.length === 0}
                  className="p-2 bg-accent-primary text-white hover:bg-accent-primary/90 disabled:bg-bg-tertiary disabled:text-text-muted/30 disabled:cursor-not-allowed rounded-lg transition-all duration-150 shadow-sm disabled:shadow-none"
                  title={t.chat.send}
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Attach panel */}
          {showAttachPanel && (
            <AttachPanel
              onAttachFile={handleAttachFile}
              onAttachWorkflow={handleAttachWorkflow}
              onAttachSkill={handleAttachSkill}
              attachedSkills={attachments.filter(a => a.type === 'skill').map(a => a.name)}
              onClose={() => setShowAttachPanel(false)}
            />
          )}

          {/* Bottom hint */}
          <div className="flex items-center justify-between mt-1.5 px-1">
            <span className="text-[10px] text-text-muted/40">
              <kbd className="px-1 py-0.5 bg-bg-tertiary/50 rounded text-[9px] font-mono">↵</kbd> send
              <span className="mx-1.5">·</span>
              <kbd className="px-1 py-0.5 bg-bg-tertiary/50 rounded text-[9px] font-mono">⇧↵</kbd> new line
            </span>
            <span className="text-[10px] text-text-muted/40 flex items-center gap-1">
              {isStreaming ? (
                <>
                  <Zap className="w-2.5 h-2.5 text-accent-primary" />
                  <span className="text-accent-primary">Streaming</span>
                </>
              ) : (
                <>
                  <div className="w-1.5 h-1.5 rounded-full bg-accent-success/60" />
                  {t.common.success === '成功' ? '就绪' : 'Ready'}
                </>
              )}
            </span>
          </div>
        </div>
      </footer>

      {/* File Browser Dialog (decoupled component) */}
      <FileBrowserDialog
        open={showFileBrowser}
        onClose={() => setShowFileBrowser(false)}
        onConfirm={handleFileBrowserConfirm}
      />
    </div>
  )
}

// --- Message grouping ---
// `groupMessages` / `roleOf` / `MessageRole` 提取到 ./groupMessages 以便单测。

// --- Message group wrapper with a single avatar per group ---

function MessageGroup({
  role,
  runId,
  children,
}: {
  role: MessageRole
  runId?: string
  children: React.ReactNode
}) {
  if (role === 'system') {
    // 细条系统消息（token/cost 指示）不占头像位。
    return <div className="px-5">{children}</div>
  }

  if (role === 'user') {
    return (
      <div className="px-5 py-2 animate-fade-in">
        <div className="flex justify-end">
          <div className="max-w-[85%] min-w-0">{children}</div>
        </div>
      </div>
    )
  }

  // assistant：一组里可能有多条子消息（thought / code / code_result / text ...），
  // 全部堆在同一个头像右边，子消息之间留一点竖向间距。
  // 若整组涉及到过 code 执行（runId 非空），在头像下方挂一个 "Revert" 小按钮，
  // 一键把 workspace reset 到这次 run 开始前的 git SHA。
  return (
    <div className="px-5 py-2 animate-fade-in">
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center shrink-0">
          <div className="w-7 h-7 rounded-lg overflow-hidden mt-0.5 ring-1 ring-accent-primary/10">
            <img src={machineAvatar} alt="Bot" className="w-full h-full object-cover" />
          </div>
          {runId && <RevertRunButton runId={runId} />}
        </div>
        <div className="flex-1 min-w-0 space-y-2.5">{children}</div>
      </div>
    </div>
  )
}

/**
 * Small revert-run button under the assistant avatar.
 *
 * Only rendered when the group has at least one message with a runId
 * (i.e. the agent actually executed code / had a recorded run). It
 * confirms with the user via DialogHost and then calls
 * `runsStore.revertRun(runId)` which shells out to git reset on the
 * backend.
 */
function RevertRunButton({ runId }: { runId: string }) {
  const { confirm, alert } = useDialog()
  const [busy, setBusy] = useState(false)

  const handleClick = useCallback(async () => {
    if (busy) return
    const ok = await confirm({
      title: 'Revert this run?',
      message:
        'Reset the workspace to the git commit recorded before this ' +
        "agent run. Any file changes made during or after the run will " +
        'be lost.',
      okLabel: 'Revert',
      danger: true,
    })
    if (!ok) return

    setBusy(true)
    try {
      await useRunsStore.getState().revertRun(runId)
    } catch (err: any) {
      await alert({
        title: 'Revert failed',
        message: err?.message || String(err),
      })
    } finally {
      setBusy(false)
    }
  }, [busy, confirm, alert, runId])

  return (
    <button
      onClick={handleClick}
      disabled={busy}
      className="mt-1 w-5 h-5 rounded flex items-center justify-center text-text-muted/60 hover:text-accent-danger hover:bg-accent-danger/10 transition-colors disabled:opacity-40"
      title={`Revert workspace to the state before this run (${runId.slice(0, 8)})`}
    >
      {busy ? (
        <RefreshCw className="w-3 h-3 animate-spin" />
      ) : (
        <Undo2 className="w-3 h-3" />
      )}
    </button>
  )
}

// --- Welcome Content ---

function WelcomeContent({ onSuggestionClick }: { onSuggestionClick: (text: string) => void }) {
  const t = useT()
  // Check if workspace is open
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const hasWorkspace = !!workspacePath

  const suggestions = [
    {
      text: 'Load the shapefile and show it on the map',
      icon: <Globe className="w-4 h-4" />,
      desc: 'Data Loading',
      gradient: 'from-blue-500/10 to-cyan-500/10',
      iconColor: 'text-blue-400',
      borderColor: 'hover:border-blue-500/20',
    },
    {
      text: 'Create a 500m buffer around all schools',
      icon: <Layers className="w-4 h-4" />,
      desc: 'Spatial Analysis',
      gradient: 'from-emerald-500/10 to-teal-500/10',
      iconColor: 'text-emerald-400',
      borderColor: 'hover:border-emerald-500/20',
    },
    {
      text: 'Calculate the slope from this DEM raster',
      icon: <BarChart3 className="w-4 h-4" />,
      desc: 'Terrain Analysis',
      gradient: 'from-amber-500/10 to-orange-500/10',
      iconColor: 'text-amber-400',
      borderColor: 'hover:border-amber-500/20',
    },
    {
      text: 'Show me the spatial distribution of population',
      icon: <MessageSquare className="w-4 h-4" />,
      desc: 'Visualization',
      gradient: 'from-purple-500/10 to-pink-500/10',
      iconColor: 'text-purple-400',
      borderColor: 'hover:border-purple-500/20',
    },
  ]

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      {/* Logo */}
      <div className="relative mb-8 animate-fade-in">
        <div className="w-20 h-20 rounded-2xl overflow-hidden flex items-center justify-center">
          <img src={logoImg} alt="OpenGIS" className="w-20 h-20 object-contain" />
        </div>
      </div>

      <h2 className="text-xl font-bold text-text-primary mb-2 animate-fade-in">
        {t.chat.emptyState.title}
      </h2>
      <p className="text-sm text-text-muted mb-8 max-w-[320px] leading-relaxed animate-fade-in">
        {t.chat.emptyState.hint}
      </p>

      {/* Empty state: no workspace open */}
      {!hasWorkspace && (
        <div className="w-full max-w-[360px] mb-6 animate-fade-in">
          <div className="flex items-start gap-3 bg-amber-500/5 border border-amber-500/20 rounded-xl px-4 py-3">
            <div className="w-5 h-5 rounded-full bg-amber-500/15 flex items-center justify-center shrink-0 mt-0.5">
              <span className="text-amber-500 text-xs">⚠</span>
            </div>
            <div className="text-left">
              <p className="text-[12px] font-medium text-amber-600 dark:text-amber-400">
                {t.chat.emptyState.noWorkspace}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Suggestion cards */}
      <div className="grid grid-cols-1 gap-2 w-full max-w-[360px] animate-slide-up">
        {suggestions.map((s, i) => (
          <button
            key={i}
            onClick={() => onSuggestionClick(s.text)}
            className={`w-full text-left bg-bg-secondary hover:bg-bg-tertiary border border-border ${s.borderColor} rounded-xl px-4 py-3 transition-all duration-200 group hover:shadow-sm`}
            style={{ animationDelay: `${i * 50}ms` }}
          >
            <div className="flex items-center gap-3">
              <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${s.gradient} flex items-center justify-center shrink-0 ring-1 ring-white/5`}>
                <span className={s.iconColor}>{s.icon}</span>
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-[10px] uppercase tracking-wider text-text-muted group-hover:text-accent-primary transition-colors font-semibold">
                  {s.desc}
                </span>
                <p className="text-[13px] text-text-secondary group-hover:text-text-primary transition-colors truncate mt-0.5 leading-snug">
                  {s.text}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>

      {/* Powered by badge */}
      <div className="mt-8 flex items-center gap-2 text-[10px] text-text-muted/40 animate-fade-in">
        <Zap className="w-3 h-3" />
        <span>Ask anything about your geospatial data</span>
      </div>
    </div>
  )
}

// --- Conversation List Dropdown ---

function ConversationListDropdown({
  conversations,
  activeId,
  onSelect,
  onDelete,
  onClose,
}: {
  conversations: { id: string; title: string; messages: any[]; updatedAt: number }[]
  activeId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onClose: () => void
}) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const listTitleInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId && listTitleInputRef.current) {
      listTitleInputRef.current.focus()
      listTitleInputRef.current.select()
    }
  }, [editingId])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-64 max-h-80 overflow-y-auto bg-bg-primary border border-border rounded-xl shadow-xl z-[999] animate-fade-in"
      style={{ scrollbarWidth: 'thin' }}
    >
      <div className="p-2">
        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5">
          {t.chat.conversations} ({conversations.length})
        </div>
        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={`group flex items-center gap-2 px-2 py-2 rounded-lg cursor-pointer transition-all duration-150 ${
              conv.id === activeId
                ? 'bg-accent-primary/10 text-accent-primary'
                : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
            }`}
            onClick={() => onSelect(conv.id)}
          >
            <MessageSquare className="w-3.5 h-3.5 shrink-0" />
            <div className="flex-1 min-w-0">
              {editingId === conv.id ? (
                <input
                  ref={listTitleInputRef}
                  className="text-[12px] leading-tight bg-bg-primary border border-border rounded px-1 py-0.5 outline-none focus:border-accent-primary w-full"
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onBlur={() => {
                    const trimmed = editingTitle.trim()
                    if (trimmed) {
                      useChatStore.getState().renameConversation(conv.id, trimmed)
                    }
                    setEditingId(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      const trimmed = editingTitle.trim()
                      if (trimmed) {
                        useChatStore.getState().renameConversation(conv.id, trimmed)
                      }
                      setEditingId(null)
                    } else if (e.key === 'Escape') {
                      setEditingId(null)
                    }
                  }}
                />
              ) : (
                <p
                  className="text-[12px] truncate leading-tight"
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    setEditingTitle(conv.title || t.chat.newConversation)
                    setEditingId(conv.id)
                  }}
                >
                  {conv.title || t.chat.newConversation}
                </p>
              )}
              <p className="text-[10px] text-text-muted mt-0.5">
                {conv.messages.length} messages
              </p>
            </div>
            <div className="flex items-center gap-0.5">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  setEditingTitle(conv.title || t.chat.newConversation)
                  setEditingId(conv.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-accent-primary rounded transition-all"
                title="Rename conversation"
              >
                <Pencil className="w-3 h-3" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(conv.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-accent-danger rounded transition-all"
                title="Delete conversation"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// --- Attach Panel ---

function AttachPanel({
  onAttachFile,
  onAttachWorkflow,
  onAttachSkill,
  attachedSkills,
  onClose,
}: {
  onAttachFile: () => void
  onAttachWorkflow: (entry: { path: string; name: string }) => void
  onAttachSkill: (name: string, groups: string[]) => void
  attachedSkills: string[]
  onClose: () => void
}) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const workflowEntries = useWorkflowStore((s) => s.entries)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 right-0 mb-2 bg-bg-secondary border border-border rounded-xl shadow-xl z-[999] animate-fade-in overflow-hidden"
    >
      <div className="p-2">
        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5">
          {t.chat.attachFile}
        </div>

        {/* Attach file from disk */}
        <button
          onClick={onAttachFile}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-all duration-150 group"
        >
          <div className="w-7 h-7 rounded-lg bg-blue-500/10 flex items-center justify-center shrink-0 ring-1 ring-blue-500/20">
            <FolderOpen className="w-3.5 h-3.5 text-blue-400" />
          </div>
          <div className="text-left">
            <p className="text-[12px] font-medium leading-tight">{t.chat.browseFiles}</p>
            <p className="text-[10px] text-text-muted mt-0.5">
              {t.fileBrowser.selectFiles}
            </p>
          </div>
        </button>

        {/* Workflow section */}
        {workflowEntries.length > 0 && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5 mt-1 flex items-center gap-1">
              <GitBranch className="w-3 h-3" />
              {t.chat.attachWorkflow}
            </div>
            <div className="max-h-32 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
              {workflowEntries.map((entry) => (
                <button
                  key={entry.path}
                  onClick={() => onAttachWorkflow(entry)}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-all duration-150"
                >
                  <div className="w-6 h-6 rounded-md bg-purple-500/10 flex items-center justify-center shrink-0 ring-1 ring-purple-500/20">
                    <GitBranch className="w-3 h-3 text-purple-400" />
                  </div>
                  <div className="text-left flex-1 min-w-0">
                    <p className="text-[12px] font-medium leading-tight truncate">
                      {entry.name}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {/* Skills section */}
        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5 mt-1 flex items-center gap-1">
          <Wrench className="w-3 h-3" />
          Attach Skills
        </div>
        <button
          onClick={() => onAttachSkill('QGIS4+', ['qgis'])}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-150 ${
            attachedSkills.includes('QGIS4+')
              ? 'bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30'
              : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
          }`}
        >
          <div className={`w-6 h-6 rounded-md flex items-center justify-center shrink-0 ring-1 ${
            attachedSkills.includes('QGIS4+')
              ? 'bg-amber-500/20 ring-amber-500/30'
              : 'bg-amber-500/10 ring-amber-500/20'
          }`}>
            <Wrench className="w-3 h-3 text-amber-400" />
          </div>
          <div className="text-left flex-1 min-w-0">
            <p className="text-[12px] font-medium leading-tight">QGIS4+</p>
            <p className="text-[10px] text-text-muted mt-0.5">
              {attachedSkills.includes('QGIS4+') ? 'Attached — click to detach' : '42 QGIS MCP commands'}
            </p>
          </div>
        </button>

        {/* Hint */}
        <div className="px-2 pt-2 pb-1">
          <p className="text-[10px] text-text-muted/60 leading-relaxed">
            💡 {t.common.success === '成功'
              ? '附加工作流来引导 Agent 按预定义的流程执行，或附加数据文件提供上下文。'
              : 'Attach a workflow to guide the agent through a predefined pipeline, or attach data files for context.'}
          </p>
        </div>
      </div>
    </div>
  )
}
