import { memo, useMemo } from 'react'
import { AlertCircle, Check, Loader2, Cpu, ChevronRight, Hourglass, AlertTriangle, RefreshCw, Brain } from 'lucide-react'
import type { UIMessage, ApiReqInfo } from '@/types/chat'
import MarkdownBlock from './MarkdownBlock'
import { ThinkingRow } from './ThinkingRow'
import { ToolCallRow } from './ToolCallRow'
import { CodeStepRow, CodeResultRow } from './CodeStepRow'
import { ImageRow } from './ImageRow'
import { useChatStore } from '@/stores/chatStore'

interface ChatRowProps {
  message: UIMessage
  isExpanded: boolean
  onToggleExpand: (ts: number) => void
  isLast: boolean
}

/**
 * ChatRow — Cline-inspired message renderer.
 * Routes each message to the appropriate renderer based on type.
 */
const ChatRow = memo(({ message, isExpanded, onToggleExpand, isLast }: ChatRowProps) => {
  return (
    <div className="relative">
      <ChatRowContent
        message={message}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
        isLast={isLast}
      />
    </div>
  )
})

ChatRow.displayName = 'ChatRow'
export default ChatRow

// --- ChatRowContent ---

const ChatRowContent = memo(({ message, isExpanded, onToggleExpand, isLast }: ChatRowProps) => {
  const type = message.type === 'ask' ? message.ask : message.say

  const handleToggle = () => onToggleExpand(message.ts)

  // API request started — cost/status indicator
  if (type === 'api_req_started') {
    const info = parseApiReqInfo(message)
    return <ApiRequestRow info={info} isLast={isLast} />
  }

  // Reasoning/thinking
  if (type === 'reasoning') {
    const isStreaming = message.partial === true
    const hasText = !!message.text?.trim()
    return (
      <ThinkingRow
        isExpanded={isStreaming || isExpanded}
        isStreaming={isStreaming}
        isVisible={true}
        onToggle={isStreaming ? undefined : handleToggle}
        reasoningContent={message.text}
        showChevron={!isStreaming || hasText}
        showTitle={true}
        title={isStreaming ? 'Thinking...' : 'Thought'}
      />
    )
  }

  // User message
  if (type === 'user_feedback') {
    return <UserMessageRow message={message} />
  }

  // Tool calls
  if (type === 'tool' || type === 'command') {
    return (
      <ToolCallRow
        message={message}
        isExpanded={isExpanded}
        onToggleExpand={handleToggle}
        isLast={isLast}
      />
    )
  }

  // CodeAgent step: Python block emitted by the agent
  if (type === 'code') {
    return (
      <CodeStepRow
        message={message}
        isExpanded={isExpanded}
        onToggleExpand={handleToggle}
      />
    )
  }

  // CodeAgent step: sandbox output / error for the most recent code block
  if (type === 'code_result') {
    return <CodeResultRow message={message} />
  }

  // Inline image (matplotlib plot etc.) emitted via rpc.ui.chat.show_image.
  if (type === 'image') {
    return <ImageRow message={message} />
  }

  // Soft stop: agent hit its step budget. Shows summary (already streamed
  // in preceding text msg) + a "Continue" button that kicks off a new run.
  if (type === 'max_steps_reached') {
    return <MaxStepsReachedRow message={message} isLast={isLast} />
  }

  // Error
  if (type === 'error') {
    return <ErrorRow message={message} />
  }

  // Progress indicator
  if (type === 'progress') {
    if (!message.partial) return <div className="h-px" aria-hidden />
    return <ProgressRow message={message} />
  }

  // Completion result
  if (type === 'completion_result') {
    return <CompletionRow message={message} />
  }

  // Text (assistant response)
  if (type === 'text') {
    return (
      <div className="w-full min-w-0 overflow-hidden">
        <MarkdownBlock markdown={message.text} showCursor={message.partial} />
      </div>
    )
  }

  // MCP server response
  if (type === 'mcp_server_response') {
    return (
      <div className="bg-bg-tertiary rounded-xl border border-border p-3 text-sm">
        <pre className="whitespace-pre-wrap break-words text-text-secondary overflow-x-auto text-[13px]">
          {message.text}
        </pre>
      </div>
    )
  }

  // Followup question from agent
  if (type === 'followup') {
    return (
      <div className="bg-accent-primary/5 border border-accent-primary/15 rounded-xl p-3.5">
        <div className="flex items-center gap-2 mb-1.5">
          <ChevronRight className="w-3.5 h-3.5 text-accent-primary" />
          <span className="text-accent-primary font-semibold text-xs uppercase tracking-wider">Question</span>
        </div>
        <p className="text-sm text-text-primary leading-relaxed">{message.text}</p>
      </div>
    )
  }

  // Invisible spacer for unhandled types
  return <div className="h-px" aria-hidden />
})

ChatRowContent.displayName = 'ChatRowContent'

// --- Sub-components ---

function UserMessageRow({ message }: { message: UIMessage }) {
  return (
    <div
      className="px-4 py-2.5 rounded-2xl rounded-tr-sm text-[13px] shadow-sm"
      style={{
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowWrap: 'break-word',
        maxWidth: '100%',
        backgroundColor: 'rgba(74, 222, 128, 0.08)',
        borderWidth: '1px',
        borderStyle: 'solid',
        borderColor: 'rgba(74, 222, 128, 0.18)',
      }}
    >
      <span className="block text-text-primary leading-relaxed">{message.text}</span>
      {/* Attached files */}
      {message.files && message.files.length > 0 && (
        <div className="flex gap-1.5 mt-2 flex-wrap">
          {message.files.map((file, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-bg-tertiary/80 border border-border text-[10px] text-text-muted"
            >
              📎 {file}
            </span>
          ))}
        </div>
      )}
      {/* Image thumbnails */}
      {message.images && message.images.length > 0 && (
        <div className="flex gap-2 mt-2.5 flex-wrap">
          {message.images.map((img, i) => (
            <img
              key={i}
              src={img}
              alt=""
              className="w-16 h-16 object-cover rounded-lg border border-border shadow-sm"
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ErrorRow({ message }: { message: UIMessage }) {
  const errorText = message.text || 'Unknown error'
  // Detect if it's a humanized error (starts with ⚠️) or raw
  const isHumanized = errorText.startsWith('⚠️')

  // Extract actionable suggestion from humanized errors
  const mainText = isHumanized ? errorText : `⚠️ ${errorText}`

  return (
    <div className="text-[13px] bg-accent-danger/5 border border-accent-danger/12 rounded-xl px-4 py-3 shadow-sm">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-accent-danger" />
        <div className="flex-1 min-w-0">
          <span className="whitespace-pre-wrap break-words leading-relaxed text-text-primary">
            {mainText}
          </span>
        </div>
      </div>
    </div>
  )
}

// --- Progress stage labels ---
const PROGRESS_LABELS: Record<string, string> = {
  installing_packages: '📦 Installing packages...',
  loading_geodata: '🗺️ Loading geodata...',
  loading_raster: '🛰️ Loading raster data...',
  loading_data: '📊 Loading data...',
  spatial_analysis: '📐 Running spatial analysis...',
  generating_visualization: '🎨 Generating visualization...',
  rendering_map: '🗺️ Rendering to map...',
  saving_results: '💾 Saving results...',
  executing_code: '⚙️ Executing code...',
  processing: '⏳ Processing...',
}

function ProgressRow({ message }: { message: UIMessage }) {
  const stage = message.progressStage || 'processing'
  const label = PROGRESS_LABELS[stage] || PROGRESS_LABELS.processing
  const detail = message.progressDetail

  return (
    <div className="flex items-center gap-2.5 py-1.5 animate-fade-in">
      <div className="relative w-4 h-4">
        <div className="absolute inset-0 rounded-full border-2 border-accent-primary/20" />
        <div className="absolute inset-0 rounded-full border-2 border-accent-primary border-t-transparent animate-spin" />
      </div>
      <span className="text-[12px] text-text-muted font-medium">
        {detail || label}
      </span>
    </div>
  )
}

function MaxStepsReachedRow({ message, isLast }: { message: UIMessage; isLast: boolean }) {
  const info = message.maxStepsInfo
  const isStreaming = useChatStore((s) => s.isStreaming)
  const sendMessage = useChatStore((s) => s.sendMessage)

  // Only the most recent max-steps row should offer a Continue button —
  // older ones in the scrollback are historical records, not actionable.
  const canContinue = isLast && !isStreaming

  const handleContinue = () => {
    if (!canContinue) return
    // Keeping the wording natural so the LLM resumes the same thread of
    // thought instead of treating it as a new task.
    sendMessage('继续')
  }

  return (
    <div className="flex items-start gap-3 bg-bg-tertiary/40 border border-border/60 rounded-xl px-4 py-3">
      <Hourglass className="w-4 h-4 shrink-0 mt-0.5 text-text-muted" />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-text-primary leading-relaxed">
          已达到本轮 <span className="font-mono text-text-secondary">max_iterations = {info?.maxSteps ?? '?'}</span> 上限，
          Agent 给了一个阶段性总结（见上方）。
        </div>
        <div className="text-[11px] text-text-muted mt-1">
          想继续的话点下方按钮，或在 Settings → Agent → Max Iterations 调高上限再试。
        </div>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={handleContinue}
            disabled={!canContinue}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium bg-accent-primary/10 hover:bg-accent-primary/15 text-accent-primary border border-accent-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            继续
          </button>
        </div>
      </div>
    </div>
  )
}

function CompletionRow({ message }: { message: UIMessage }) {
  return (
    <div className="bg-accent-success/5 border border-accent-success/12 rounded-xl p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-2.5">
        <div className="w-5 h-5 rounded-full bg-accent-success/15 flex items-center justify-center">
          <Check className="w-3 h-3 text-accent-success" />
        </div>
        <span className="text-accent-success font-semibold text-sm">Task Completed</span>
      </div>
      {message.text && (
        <div className="pl-7">
          <MarkdownBlock markdown={message.text} />
        </div>
      )}
    </div>
  )
}

function ApiRequestRow({ info, isLast }: { info: ApiReqInfo; isLast: boolean }) {
  const isFailed = !!info.streamingFailedMessage || !!info.cancelReason

  if (isFailed) {
    return (
      <div className="flex items-center gap-2 text-xs text-accent-danger py-1.5 px-1">
        <AlertCircle className="w-3 h-3" />
        <span className="truncate">{info.streamingFailedMessage || info.cancelReason || 'Request failed'}</span>
      </div>
    )
  }

  // Show token usage inline
  if (info.tokensIn != null && info.tokensOut != null && (info.tokensIn > 0 || info.tokensOut > 0)) {
    return (
      <div className="flex items-center gap-1.5 text-[10px] text-text-muted/50 py-1 px-1">
        <Cpu className="w-2.5 h-2.5" />
        <span>{info.tokensIn?.toLocaleString()} in · {info.tokensOut?.toLocaleString()} out</span>
        {info.cost != null && info.cost > 0 && (
          <span className="ml-1">· ${info.cost.toFixed(4)}</span>
        )}
      </div>
    )
  }

  return <div className="h-px" aria-hidden />
}

// --- Helpers ---

function parseApiReqInfo(message: UIMessage): ApiReqInfo {
  try {
    if (message.text) {
      return JSON.parse(message.text) as ApiReqInfo
    }
  } catch {}
  return {}
}
