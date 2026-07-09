import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import { ChevronDown } from 'lucide-react'
import { useChatStore, type ChatAttachment } from '@/stores/chatStore'
import { useT } from '@/i18n'
import ChatRow from './components/ChatRow'
import { ApprovalInline } from '@/features/approval/ApprovalGate'
import { ChatComposer } from './components/ChatComposer'
import { ChatHeader } from './components/ChatHeader'
import { ListSpacer, TypingFooter } from './components/ChatListChrome'
import { ChatSearchCapsule } from './components/ChatSearchCapsule'
import { FileBrowserDialog, type FileBrowserResult } from './components/FileBrowserDialog'
import { MessageGroup } from './components/MessageGroup'
import { WelcomeContent } from './components/WelcomeContent'
import type { ChatMessage } from '@/types/chat'
import { groupMessages } from './groupMessages'
import { messagePartsForRender } from '@/services/chatMessageParts'

// Stable empty reference so the `messages` selector fallback doesn't create a
// new array each render (which would defeat memoization downstream).
const EMPTY_MESSAGES: ChatMessage[] = []

export function ChatView({
  variant = 'default',
}: {
  variant?: 'default' | 'floating'
} = {}) {
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
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchIndex, setSearchIndex] = useState(0)
  const headerTitleInputRef = useRef<HTMLInputElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  const virtuosoRef = useRef<VirtuosoHandle>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  // Fine-grained selectors: subscribe to exactly what we use so unrelated
  // store changes don't re-render the whole panel. The streaming hot-path
  // still re-renders ChatView (it owns the list), but virtualization + the
  // memoized ChatRow keep that cheap — only the changed row actually repaints.
  const activeConversationId = useChatStore((s) => s.activeConversationId)
  const conversations = useChatStore((s) => s.conversations)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const isCancelling = useChatStore((s) => s.isCancelling)
  const workflowPlanActive = useChatStore((s) => s.workflowPlanActive)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const abortTask = useChatStore((s) => s.abortTask)
  const createConversation = useChatStore((s) => s.createConversation)
  const setActiveConversation = useChatStore((s) => s.setActiveConversation)

  const conversation = useMemo(
    () => conversations.find((c) => c.id === activeConversationId) ?? null,
    [conversations, activeConversationId],
  )
  const messages: ChatMessage[] = conversation?.messages ?? EMPTY_MESSAGES
  const isBusy = isStreaming || isCancelling

  // 把消息按 "一轮 assistant 回答" 分组：两次 user_feedback 之间的所有 assistant
  // 消息（text / reasoning / code / code_result / tool / ...）
  // 合成一组，整组只画一个机器人头像。否则 Python 侧一轮回答会同时推 N 条消息，
  // 每条都挂头像，用户会误以为"机器人回答了 N 次"。
  const messageGroups = useMemo(() => groupMessages(messages), [messages])

  const compactWorkflowRunIds = useMemo(() => {
    const ids = new Set<string>()
    for (const msg of messages) {
      if (msg.say === 'plan' && msg.planData?.workflow && msg.planData.runId) {
        ids.add(msg.planData.runId)
      }
    }
    return ids
  }, [messages])

  const userHistory = useMemo<string[]>(() => {
    const acc: string[] = []
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      const userPart = messagePartsForRender(m).find((part) => part.type === 'text' && part.data?.role === 'user')
      if (userPart?.text) acc.push(userPart.text)
    }
    return acc
  }, [messages])

  const searchMatches = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    if (!query) return []
    return messageGroups
      .map((group, groupIndex) => ({
        groupIndex,
        groupKey: group.items[0]?.ts ?? groupIndex,
        text: extractSearchText(group.items).toLowerCase(),
      }))
      .filter((item) => item.text.includes(query))
  }, [messageGroups, searchQuery])

  const currentSearchMatch = searchMatches.length > 0
    ? searchMatches[Math.min(searchIndex, searchMatches.length - 1)]
    : null

  const focusSearch = useCallback(() => {
    setSearchOpen(true)
    requestAnimationFrame(() => {
      searchInputRef.current?.focus()
      searchInputRef.current?.select()
    })
  }, [])

  const closeSearch = useCallback(() => {
    setSearchOpen(false)
    setSearchQuery('')
    setSearchIndex(0)
    textAreaRef.current?.focus()
  }, [])

  const jumpToSearchMatch = useCallback((index: number, behavior: 'auto' | 'smooth' = 'smooth') => {
    const match = searchMatches[index]
    if (!match) return
    setSearchIndex(index)
    virtuosoRef.current?.scrollToIndex({
      index: match.groupIndex,
      align: 'center',
      behavior,
    })
  }, [searchMatches])

  const stepSearch = useCallback((delta: 1 | -1) => {
    if (searchMatches.length === 0) return
    const next = (searchIndex + delta + searchMatches.length) % searchMatches.length
    jumpToSearchMatch(next)
  }, [jumpToSearchMatch, searchIndex, searchMatches.length])

  // --- Auto-resize textarea ---
  const adjustTimerRef = useRef<number | null>(null)
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

  // --- Scroll behavior (virtualized) ---
  // Virtuoso owns the scroll container. `followOutput` keeps us pinned to the
  // bottom while streaming (text grows in place), and `atBottomStateChange`
  // drives the floating "scroll to bottom" affordance.
  const scrollToBottom = useCallback((behavior: 'auto' | 'smooth' = 'smooth') => {
    virtuosoRef.current?.scrollToIndex({ index: 'LAST', align: 'end', behavior })
  }, [])

  const scheduleScrollToBottom = useCallback((behavior: 'auto' | 'smooth' = 'smooth') => {
    requestAnimationFrame(() => {
      scrollToBottom(behavior)
      requestAnimationFrame(() => scrollToBottom(behavior))
    })
  }, [scrollToBottom])

  const handleAtBottomChange = useCallback((atBottom: boolean) => {
    setShowScrollToBottom((prev) => (prev !== !atBottom ? !atBottom : prev))
  }, [])

  // Load text from a past user message back into the composer for editing /
  // resending — the standard "edit & re-run" affordance.
  const handleEditUserMessage = useCallback((text: string) => {
    setInputValue(text)
    setHistoryIndex(-1)
    draftBeforeHistoryRef.current = ''
    requestAnimationFrame(() => {
      const ta = textAreaRef.current
      if (!ta) return
      ta.focus()
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
      const end = text.length
      ta.setSelectionRange(end, end)
    })
  }, [])

  useEffect(() => {
    textAreaRef.current?.focus()
    setHistoryIndex(-1)
    draftBeforeHistoryRef.current = ''
    setSearchQuery('')
    setSearchIndex(0)
  }, [activeConversationId])

  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        focusSearch()
      }
    }
    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => window.removeEventListener('keydown', handleGlobalKeyDown)
  }, [focusSearch])

  useEffect(() => {
    if (!searchOpen || !searchQuery.trim()) return
    if (searchMatches.length === 0) {
      if (searchIndex !== 0) setSearchIndex(0)
      return
    }
    const nextIndex = Math.min(searchIndex, searchMatches.length - 1)
    if (nextIndex !== searchIndex) {
      setSearchIndex(nextIndex)
      return
    }
    jumpToSearchMatch(nextIndex, 'auto')
  }, [jumpToSearchMatch, searchIndex, searchMatches.length, searchOpen, searchQuery])

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
    if (isStreaming || isCancelling) return

    const currentAttachments = attachments.length > 0 ? [...attachments] : undefined
    setInputValue('')
    setAttachments([])
    setShowAttachPanel(false)
    setHistoryIndex(-1)
    draftBeforeHistoryRef.current = ''
    if (textAreaRef.current) {
      textAreaRef.current.style.height = 'auto'
    }
    scheduleScrollToBottom('auto')

    try {
      await sendMessage(trimmed || '(see attached files)', undefined, currentAttachments)
      scheduleScrollToBottom('smooth')
    } catch (error: any) {
      console.error('[ChatView] Failed to send message:', error)
    }
  }, [inputValue, isStreaming, isCancelling, sendMessage, attachments, scheduleScrollToBottom])

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

  const handleAttachToolGroup = useCallback((name: string, groups: string[]) => {
    setAttachments((prev) => {
      // Remove existing tool-group attachment with same name (toggle behavior).
      const filtered = prev.filter(a => !(a.type === 'tool_group' && a.name === name))
      // If already attached, just remove (toggle off)
      if (prev.some(a => a.type === 'tool_group' && a.name === name)) return filtered
      return [...filtered, { name, path: name.toLowerCase(), type: 'tool_group' as const, tool_groups: groups }]
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
    if (isBusy) return
    try {
      await sendMessage(text)
    } catch (error: any) {
      console.error('[ChatView] Failed to send suggestion:', error)
    }
  }, [isBusy, sendMessage])

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

  const activeWorkState = useMemo(() => {
    if (isCancelling) return { label: t.chat.cancelling, tone: 'working' as const }
    if (!isStreaming) return null
    const lastMsg = messages[messages.length - 1]
    if (!lastMsg) return { label: t.chat.thinking, tone: 'thinking' as const }

    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i]
      if (msg.say === 'tool' && msg.toolStatus === 'running') {
        return { label: getRunningToolLabel(msg, t), tone: 'working' as const }
      }
      if (msg.say === 'code' && msg.partial) {
        return { label: t.chat.progress.generating, tone: 'code' as const }
      }
      if (msg.say === 'progress' && msg.partial) {
        return {
          label: displayProgressDetail(msg.progressStage, msg.progressDetail, t),
          tone: 'working' as const,
        }
      }
      if (msg.say === 'plan' && msg.planData?.steps?.some((s) => s.status === 'in_progress')) {
        return { label: t.chat.progressProcessing, tone: 'working' as const }
      }
      if (msg.say === 'subagent' && msg.subagentData?.status === 'running') {
        return { label: t.chat.progressProcessing, tone: 'working' as const }
      }
      if (msg.partial && (msg.say === 'text' || msg.say === 'reasoning')) {
        return { label: t.chat.thinking, tone: 'thinking' as const }
      }
      if (msg.say === 'user_feedback') break
    }

    if (lastMsg.say === 'code_result' || lastMsg.say === 'text') {
      return { label: t.chat.thinking, tone: 'thinking' as const }
    }
    return { label: t.chat.progressProcessing, tone: 'working' as const }
  }, [isStreaming, isCancelling, messages, t])

  const hasTask = messages.length > 0

  useEffect(() => {
    if (!hasTask) return
    const last = messages[messages.length - 1]
    if (isStreaming || last?.say === 'user_feedback') {
      scheduleScrollToBottom(last?.say === 'user_feedback' ? 'auto' : 'smooth')
    }
  }, [hasTask, isStreaming, messages.length, scheduleScrollToBottom])

  return (
    <div className={`w-full h-full flex flex-col bg-[var(--chat-bg)] overflow-hidden ${
      variant === 'floating'
        ? 'rounded-2xl shadow-2xl'
        : ''
    }`}>
      <ChatHeader
        variant={variant}
        hasTask={hasTask}
        isStreaming={isStreaming}
        conversation={conversation}
        conversations={conversations}
        activeConversationId={activeConversationId}
        isEditingTitle={isEditingTitle}
        editingTitle={editingTitle}
        titleInputRef={headerTitleInputRef}
        searchOpen={searchOpen}
        showConversationList={showConversationList}
        onEditingTitleChange={setIsEditingTitle}
        onEditingTitleTextChange={setEditingTitle}
        onShowConversationListChange={setShowConversationList}
        onSelectConversation={setActiveConversation}
        onCreateConversation={createConversation}
        onFocusSearch={focusSearch}
      />

      {searchOpen && (
        <ChatSearchCapsule
          query={searchQuery}
          inputRef={searchInputRef}
          current={searchMatches.length > 0 ? searchIndex + 1 : 0}
          total={searchMatches.length}
          onQueryChange={(value) => {
            setSearchQuery(value)
            setSearchIndex(0)
          }}
          onPrevious={() => stepSearch(-1)}
          onNext={() => stepSearch(1)}
          onClose={closeSearch}
        />
      )}

      {/* === Messages Area (virtualized) === */}
      {hasTask ? (
        <Virtuoso
          key={activeConversationId ?? 'none'}
          ref={virtuosoRef}
          data={messageGroups}
          className="flex-1 min-h-0"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--text-muted) transparent' }}
          followOutput={(atBottom) => (isStreaming || atBottom ? 'smooth' : false)}
          atBottomThreshold={100}
          atBottomStateChange={handleAtBottomChange}
          increaseViewportBy={{ top: 600, bottom: 600 }}
          initialTopMostItemIndex={Math.max(0, messageGroups.length - 1)}
          computeItemKey={(index, group) => `g-${group.items[0]?.ts ?? index}`}
          components={{ Header: ListSpacer, Footer: TypingFooter }}
          context={activeWorkState}
          itemContent={(_index, group) => {
            const groupKey = group.items[0]?.ts ?? _index
            return (
              <MessageGroup
                role={group.role}
                items={group.items}
                onEditUser={handleEditUserMessage}
                highlighted={currentSearchMatch?.groupKey === groupKey}
              >
                {group.items
                  .filter((msg) => {
                    // Workflow runs are shown in compact mode: keep the plan
                    // and final text visible, hide noisy code/tool internals
                    // for that workflow run only. Later normal agent runs in
                    // the same conversation must still show their details.
                    const compactWorkflowRun = msg.runId && compactWorkflowRunIds.has(msg.runId)
                    if (!workflowPlanActive && !compactWorkflowRun) return true
                    const say = msg.say
                    if (say === 'plan' || say === 'text' || say === 'error' || say === 'user_feedback') return true
                    return !compactWorkflowRun
                  })
                  .map((msg, index) => {
                  return (
                    <ChatRow
                      key={`${msg.ts}-${index}`}
                      message={msg}
                      isExpanded={expandedRows.has(msg.ts)}
                      onToggleExpand={toggleRowExpansion}
                    />
                  )
                })}
              </MessageGroup>
            )
          }}
        />
      ) : (
        <div className="flex-1 overflow-y-auto">
          <WelcomeContent onSuggestionClick={handleSuggestionClick} />
        </div>
      )}

      {/* === Footer === */}
      <ApprovalInline />
      <footer className="shrink-0 border-t border-border bg-[var(--chat-footer-bg)]">
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

        <ChatComposer
          inputValue={inputValue}
          attachments={attachments}
          showAttachPanel={showAttachPanel}
          isBusy={isBusy}
          isCancelling={isCancelling}
          textAreaRef={textAreaRef}
          onInputChange={(value) => {
            setInputValue(value)
            if (historyIndex !== -1) {
              setHistoryIndex(-1)
              draftBeforeHistoryRef.current = ''
            }
          }}
          onTextAreaResize={adjustTextareaHeight}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onSend={handleSend}
          onStop={handleStop}
          onToggleAttachPanel={() => setShowAttachPanel(!showAttachPanel)}
          onCloseAttachPanel={() => setShowAttachPanel(false)}
          onAttachFile={handleAttachFile}
          onAttachWorkflow={handleAttachWorkflow}
          onAttachToolGroup={handleAttachToolGroup}
          onRemoveAttachment={removeAttachment}
        />
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

function getRunningToolLabel(message: ChatMessage, t: ReturnType<typeof useT>): string {
  const name = message.toolName || ''
  if (name === 'execute_code' || name === 'gis_execute_python') return t.chat.progressExecuting
  if (name === 'add_layer' || name === 'add_raster' || name.includes('map')) return t.chat.progressRendering
  if (name === 'read_file' || name === 'write_file' || name === 'edit_file') return t.chat.progressProcessing
  return name ? `${t.chat.progressProcessing} · ${name}` : t.chat.progressProcessing
}

function progressLabelForStage(
  stage: string | undefined,
  t: ReturnType<typeof useT>,
): string {
  switch (stage) {
    case 'calling_llm':
    case 'thinking_next_step':
      return t.chat.thinking
    case 'installing_packages':
      return t.chat.progressInstalling
    case 'loading_geodata':
      return t.chat.progressLoadingGeodata
    case 'loading_raster':
      return t.chat.progressLoadingRaster
    case 'loading_data':
      return t.chat.progressLoadingData
    case 'spatial_analysis':
      return t.chat.progressSpatialAnalysis
    case 'generating_visualization':
      return t.chat.progressVisualization
    case 'rendering_map':
      return t.chat.progressRendering
    case 'saving_results':
      return t.chat.progressSaving
    case 'executing_code':
      return t.chat.progressExecuting
    case 'processing':
    default:
      return t.chat.progressProcessing
  }
}

function displayProgressDetail(
  stage: string | undefined,
  detail: string | undefined,
  t: ReturnType<typeof useT>,
): string {
  if (stage === 'calling_llm' || stage === 'thinking_next_step') {
    return progressLabelForStage(stage, t)
  }
  return detail || progressLabelForStage(stage, t)
}

/** Concatenate searchable message content. Kept text-only so virtualized rows stay cheap. */
function extractSearchText(items: ChatMessage[]): string {
  const parts: string[] = []
  for (const m of items) {
    for (const part of messagePartsForRender(m)) {
      if (part.text) parts.push(part.text)
      if (part.tool) parts.push(part.tool)
      parts.push(searchTextForPartData(part.data))
    }
    if (m.planData) {
      if (m.planData.title) parts.push(m.planData.title)
      for (const step of m.planData.steps) {
        parts.push(step.title)
        if (step.note) parts.push(step.note)
      }
    }
    if (m.subagentData) {
      for (const task of m.subagentData.tasks) parts.push(task.title)
    }
    if (m.screenshotData) {
      parts.push(m.screenshotData.prompt, m.screenshotData.savePath)
    }
  }
  return parts.filter(Boolean).join('\n')
}

function searchTextForPartData(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const data = value as Record<string, unknown>
  const fields: string[] = []
  for (const key of ['stage', 'detail', 'kind', 'scriptPath', 'scriptAbsPath', 'error']) {
    const text = data[key]
    if (typeof text === 'string') fields.push(text)
  }
  const planData = data.planData as { title?: unknown; steps?: Array<{ title?: unknown; note?: unknown }> } | undefined
  if (planData?.title && typeof planData.title === 'string') fields.push(planData.title)
  if (Array.isArray(planData?.steps)) {
    for (const step of planData.steps) {
      if (typeof step.title === 'string') fields.push(step.title)
      if (typeof step.note === 'string') fields.push(step.note)
    }
  }
  return fields.join('\n').slice(0, 4000)
}
