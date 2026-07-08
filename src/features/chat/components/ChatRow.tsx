import { memo } from 'react'
import { AlertCircle, Check, Cpu, ChevronRight, Hourglass, AlertTriangle } from 'lucide-react'
import type { UIMessage, ApiReqInfo, MessagePart } from '@/types/chat'
import { useT } from '@/i18n'
import MarkdownBlock from './MarkdownBlock'
import { ThinkingRow } from './ThinkingRow'
import { ToolCallRow } from './ToolCallRow'
import { CodeStepRow, CodeResultRow } from './CodeStepRow'
import { ImageRow } from './ImageRow'
import PlanRow from './PlanRow'
import { SubagentRow } from './SubagentRow'
import { ScreenshotRow } from './ScreenshotRow'
import { useChatStore } from '@/stores/chatStore'
import { messagePartsForRender } from '@/services/chatMessageParts'

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

function dirname(path: string): string {
  const normalized = path.replace(/\\/g, '/')
  const idx = normalized.lastIndexOf('/')
  return idx > 0 ? normalized.slice(0, idx) : ''
}

function markdownBaseDirFor(message: UIMessage): string | undefined {
  if (message.markdownBaseDir) return message.markdownBaseDir
  const mdFile = message.files?.find((file) => /\.md$/i.test(file))
  if (mdFile) return dirname(mdFile)
  return undefined
}

const ChatRowContent = memo(({ message, isExpanded, onToggleExpand, isLast }: ChatRowProps) => {
  const t = useT()
  const type = message.type === 'ask' ? message.ask : message.say
  const parts = messagePartsForRender(message)

  const handleToggle = () => onToggleExpand(message.ts)

  if (parts.length > 0) {
    return (
      <MessagePartsRenderer
        message={message}
        parts={parts}
        isExpanded={isExpanded}
        onToggleExpand={handleToggle}
        isLast={isLast}
      />
    )
  }

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
        title={isStreaming ? `${t.chat.thinkingLabel}...` : t.chat.thoughtLabel}
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

  // Plan / TODO checklist emitted via rpc.ui.chat.plan_update.
  if (type === 'plan') {
    return <PlanRow planData={message.planData} />
  }

  // Sub-agent running indicator emitted via rpc.ui.chat.subagent_update.
  if (type === 'subagent') {
    return <SubagentRow data={message.subagentData} />
  }

  // Interactive screenshot card emitted via rpc.ui.chat.interactive_snapshot.
  if (type === 'screenshot' && message.screenshotData) {
    return (
      <ScreenshotRow
        requestId={message.screenshotData.requestId}
        savePath={message.screenshotData.savePath}
        prompt={message.screenshotData.prompt}
      />
    )
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
        <MarkdownBlock
          markdown={message.text}
          showCursor={message.partial}
          baseDir={markdownBaseDirFor(message)}
        />
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
          <span className="text-accent-primary font-semibold text-xs uppercase tracking-wider">{t.chat.questionLabel}</span>
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

function MessagePartsRenderer({
  message,
  parts,
  isExpanded,
  onToggleExpand,
  isLast,
}: {
  message: UIMessage
  parts: MessagePart[]
  isExpanded: boolean
  onToggleExpand: () => void
  isLast: boolean
}) {
  return (
    <div className="space-y-2">
      {parts.map((part, index) => (
        <MessagePartRow
          key={part.id || `${message.ts}:${index}`}
          message={message}
          part={part}
          isExpanded={isExpanded}
          onToggleExpand={onToggleExpand}
          isLast={isLast}
        />
      ))}
    </div>
  )
}

function MessagePartRow({
  message,
  part,
  isExpanded,
  onToggleExpand,
  isLast,
}: {
  message: UIMessage
  part: MessagePart
  isExpanded: boolean
  onToggleExpand: () => void
  isLast: boolean
}) {
  const t = useT()
  const data = part.data ?? {}
  const status = part.status
  const isStreaming = status === 'streaming' || (status === 'running' && part.type === 'reasoning')
  const partText = part.text ?? ''

  if (part.type === 'text') {
    if (data.role === 'user') {
      return (
        <UserMessageRow
          message={{
            ...message,
            text: partText,
            images: Array.isArray(data.images) ? data.images as string[] : message.images,
            files: Array.isArray(data.files) ? data.files as string[] : message.files,
          }}
        />
      )
    }
    if (data.kind === 'completion_result') {
      return <CompletionRow message={{ ...message, text: partText }} />
    }
    return (
      <div className="w-full min-w-0 overflow-hidden">
        <MarkdownBlock
          markdown={partText}
          showCursor={status === 'streaming' || message.partial}
          baseDir={typeof data.markdownBaseDir === 'string' ? data.markdownBaseDir : markdownBaseDirFor(message)}
        />
      </div>
    )
  }

  if (part.type === 'reasoning') {
    const hasText = !!partText.trim()
    return (
      <ThinkingRow
        isExpanded={isStreaming || isExpanded}
        isStreaming={isStreaming}
        isVisible={true}
        onToggle={isStreaming ? undefined : onToggleExpand}
        reasoningContent={partText}
        showChevron={!isStreaming || hasText}
        showTitle={true}
        title={isStreaming ? `${t.chat.thinkingLabel}...` : t.chat.thoughtLabel}
      />
    )
  }

  if (part.type === 'tool') {
    return (
      <ToolCallRow
        message={toolMessageFromPart(message, part)}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    )
  }

  if (part.type === 'tool_output') {
    if (part.tool === 'execute_code' || data.stepNumber != null || data.error != null) {
      return <CodeResultRow message={codeResultMessageFromPart(message, part)} />
    }
    if (!partText.trim()) return <div className="h-px" aria-hidden />
    return (
      <div className="ml-[30px] -mt-1">
        <div className="py-2 px-1 text-[13px] leading-[1.7] text-text-primary/85">
          <MarkdownBlock markdown={partText} />
        </div>
      </div>
    )
  }

  if (part.type === 'code') {
    return (
      <CodeStepRow
        message={codeMessageFromPart(message, part)}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    )
  }

  if (part.type === 'artifact') {
    if (data.kind === 'image') {
      return <ImageRow message={artifactMessageFromPart(message, part)} />
    }
    if (!partText.trim()) return <div className="h-px" aria-hidden />
    return <MarkdownBlock markdown={partText} baseDir={markdownBaseDirFor(message)} />
  }

  if (part.type === 'plan') {
    return <PlanRow planData={data.planData as UIMessage['planData']} />
  }

  if (part.type === 'progress') {
    if (data.kind === 'subagent') {
      return <SubagentRow data={data.subagentData as UIMessage['subagentData']} />
    }
    if (data.kind === 'max_steps_reached') {
      return (
        <MaxStepsReachedRow
          message={{ ...message, maxStepsInfo: data.maxStepsInfo as UIMessage['maxStepsInfo'] }}
          isLast={isLast}
        />
      )
    }
    if (status !== 'running' && !message.partial) return <div className="h-px" aria-hidden />
    return (
      <ProgressRow
        message={{
          ...message,
          progressStage: typeof data.stage === 'string' ? data.stage : message.progressStage,
          progressDetail: partText || (typeof data.detail === 'string' ? data.detail : message.progressDetail),
          partial: status === 'running' || status === 'streaming',
        }}
      />
    )
  }

  if (part.type === 'approval') {
    if (data.kind === 'screenshot') {
      const screenshotData = data.screenshotData as UIMessage['screenshotData']
      if (!screenshotData) return <div className="h-px" aria-hidden />
      return (
        <ScreenshotRow
          requestId={screenshotData.requestId}
          savePath={screenshotData.savePath}
          prompt={screenshotData.prompt}
        />
      )
    }
    return (
      <div className="bg-accent-primary/5 border border-accent-primary/15 rounded-xl p-3.5">
        <div className="flex items-center gap-2 mb-1.5">
          <ChevronRight className="w-3.5 h-3.5 text-accent-primary" />
          <span className="text-accent-primary font-semibold text-xs uppercase tracking-wider">{t.chat.questionLabel}</span>
        </div>
        <p className="text-sm text-text-primary leading-relaxed">{partText}</p>
      </div>
    )
  }

  if (part.type === 'error') {
    return <ErrorRow message={{ ...message, text: partText }} />
  }

  if (part.type === 'turn') {
    if (data.kind === 'api_req_started') {
      return <ApiRequestRow info={parseApiReqInfo({ ...message, text: partText })} isLast={isLast} />
    }
    return <div className="h-px" aria-hidden />
  }

  return <div className="h-px" aria-hidden />
}

function toolMessageFromPart(message: UIMessage, part: MessagePart): UIMessage {
  const data = part.data ?? {}
  return {
    ...message,
    say: 'tool',
    text: part.text || valueToString(data.output),
    toolName: part.tool || message.toolName,
    toolCallId: part.callId || part.call_id || message.toolCallId,
    toolArgs: (data.args as Record<string, unknown> | undefined) ?? (data.input as Record<string, unknown> | undefined) ?? message.toolArgs,
    toolStatus: part.status === 'failed'
      ? 'failed'
      : part.status === 'running' || part.status === 'streaming'
        ? 'running'
        : 'completed',
    durationMs: typeof data.durationMs === 'number' ? data.durationMs : message.durationMs,
  }
}

function codeMessageFromPart(message: UIMessage, part: MessagePart): UIMessage {
  const data = part.data ?? {}
  return {
    ...message,
    say: 'code',
    text: part.text || message.text || '',
    partial: part.status === 'running' || part.status === 'streaming',
    stepNumber: typeof data.stepNumber === 'number' ? data.stepNumber : message.stepNumber,
    scriptPath: typeof data.scriptPath === 'string' ? data.scriptPath : message.scriptPath,
    scriptAbsPath: typeof data.scriptAbsPath === 'string' ? data.scriptAbsPath : message.scriptAbsPath,
    runId: part.runId || part.run_id || message.runId,
  }
}

function codeResultMessageFromPart(message: UIMessage, part: MessagePart): UIMessage {
  const data = part.data ?? {}
  return {
    ...message,
    say: 'code_result',
    text: part.text || valueToString(data.output),
    stepNumber: typeof data.stepNumber === 'number' ? data.stepNumber : message.stepNumber,
    codeError: typeof data.error === 'string' ? data.error : message.codeError,
    durationMs: typeof data.durationMs === 'number' ? data.durationMs : message.durationMs,
    runId: part.runId || part.run_id || message.runId,
  }
}

function artifactMessageFromPart(message: UIMessage, part: MessagePart): UIMessage {
  const data = part.data ?? {}
  return {
    ...message,
    say: 'image',
    text: part.text || message.text,
    images: Array.isArray(data.images) ? data.images as string[] : message.images,
    files: Array.isArray(data.files) ? data.files as string[] : message.files,
  }
}

function valueToString(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function UserMessageRow({ message }: { message: UIMessage }) {
  return (
    <div
      className="px-4 py-2.5 rounded-2xl rounded-tr-sm text-[13px] shadow-sm bg-accent-primary/15 border border-accent-primary/25"
      style={{
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowWrap: 'break-word',
        maxWidth: '100%',
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
    <div className="text-[13px] bg-accent-danger/5 border border-accent-danger/15 rounded-xl px-4 py-3 shadow-sm">
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

// --- Progress stage labels (keys match backend progress_callback stage names) ---
function useProgressLabels(): Record<string, string> {
  const t = useT()
  return {
    installing_packages: `📦 ${t.chat.progressInstalling}`,
    loading_geodata: `🗺️ ${t.chat.progressLoadingGeodata}`,
    loading_raster: `🛰️ ${t.chat.progressLoadingRaster}`,
    loading_data: `📊 ${t.chat.progressLoadingData}`,
    spatial_analysis: `📐 ${t.chat.progressSpatialAnalysis}`,
    generating_visualization: `🎨 ${t.chat.progressVisualization}`,
    rendering_map: `🗺️ ${t.chat.progressRendering}`,
    saving_results: `💾 ${t.chat.progressSaving}`,
    executing_code: `⚙️ ${t.chat.progressExecuting}`,
    processing: `⏳ ${t.chat.progressProcessing}`,
  }
}

function ProgressRow({ message }: { message: UIMessage }) {
  const labels = useProgressLabels()
  const stage = message.progressStage || 'processing'
  const label = labels[stage] || labels.processing
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
  const t = useT()
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
    sendMessage(t.chat.continue)
  }

  return (
    <div className="flex items-start gap-3 bg-bg-tertiary/40 border border-border/30 rounded-xl px-4 py-3">
      <Hourglass className="w-4 h-4 shrink-0 mt-0.5 text-text-muted" />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-text-primary leading-relaxed">
          {t.chat.maxStepsReached.replace('{maxSteps}', String(info?.maxSteps ?? '?'))}
        </div>
        <div className="text-[11px] text-text-muted mt-1">
          {t.chat.maxStepsHint}
        </div>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={handleContinue}
            disabled={!canContinue}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium bg-accent-primary/10 hover:bg-accent-primary/15 text-accent-primary border border-accent-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {t.chat.continue}
          </button>
        </div>
      </div>
    </div>
  )
}

function CompletionRow({ message }: { message: UIMessage }) {
  const t = useT()
  return (
    <div className="bg-accent-success/5 border border-accent-success/15 rounded-xl p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-2.5">
        <div className="w-5 h-5 rounded-full bg-accent-success/15 flex items-center justify-center">
          <Check className="w-3 h-3 text-accent-success" />
        </div>
        <span className="text-accent-success font-semibold text-sm">{t.chat.taskCompleted}</span>
      </div>
      {message.text && (
        <div className="pl-7">
          <MarkdownBlock
            markdown={message.text}
            baseDir={markdownBaseDirFor(message)}
          />
        </div>
      )}
    </div>
  )
}

function ApiRequestRow({ info }: { info: ApiReqInfo; isLast: boolean }) {
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
