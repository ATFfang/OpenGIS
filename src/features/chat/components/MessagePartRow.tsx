import { ChevronRight } from 'lucide-react'
import { useT } from '@/i18n'
import { type MessagePart, type ChatMessage } from '@/types/chat'
import MarkdownBlock from './MarkdownBlock'
import { ThinkingRow } from './ThinkingRow'
import { ToolCallRow } from './ToolCallRow'
import { OperationToolOutputRow, OperationToolRow, isOperationTool } from './OperationToolRow'
import { CodeStepRow } from './CodeStepRow'
import { ImageRow } from './ImageRow'
import PlanRow from './PlanRow'
import { SubagentRow } from './SubagentRow'
import { ScreenshotRow } from './ScreenshotRow'
import { ErrorRow, ProgressRow, UserMessageRow, markdownBaseDirFor } from './MessagePartRows'

interface MessagePartRowProps {
  message: ChatMessage
  part: MessagePart
  isExpanded: boolean
  onToggleExpand: () => void
}

export function MessagePartRow({
  message,
  part,
  isExpanded,
  onToggleExpand,
}: MessagePartRowProps) {
  const t = useT()
  const data = part.data ?? {}
  const status = part.status
  const isStreaming = status === 'streaming' || (status === 'running' && part.type === 'reasoning')
  const partText = part.text ?? ''

  if (part.type === 'text') {
    if (data.role === 'user') {
      return (
        <UserMessageRow
          text={partText}
          images={Array.isArray(data.images) ? data.images as string[] : message.images}
          files={Array.isArray(data.files) ? data.files as string[] : message.files}
        />
      )
    }
    return (
      <div className="w-full min-w-0 overflow-hidden">
        <MarkdownBlock
          markdown={partText}
          showCursor={status === 'streaming' || message.partial}
          baseDir={markdownBaseDirFor(
            typeof data.markdownBaseDir === 'string' ? data.markdownBaseDir : message.markdownBaseDir,
            message.files,
          )}
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
    const toolName = part.tool || message.toolName
    const toolArgs = (data.args as Record<string, unknown> | undefined)
      ?? (data.input as Record<string, unknown> | undefined)
      ?? message.toolArgs
    const toolStatus = part.status === 'failed'
      ? 'failed'
      : part.status === 'running' || part.status === 'streaming'
        ? 'running'
        : 'completed'
    const durationMs = typeof data.durationMs === 'number'
      ? data.durationMs
      : typeof data.duration_ms === 'number'
        ? data.duration_ms
        : message.durationMs

    if (isOperationTool(toolName)) {
      return (
        <OperationToolRow
          toolName={toolName}
          toolArgs={toolArgs}
          output={part.text || valueToString(data.output)}
          status={toolStatus}
          durationMs={durationMs}
        />
      )
    }

    return (
      <ToolCallRow
        toolName={toolName}
        toolCallId={part.callId || part.call_id || message.toolCallId}
        toolArgs={toolArgs}
        output={part.text || valueToString(data.output)}
        status={toolStatus}
        durationMs={durationMs}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    )
  }

  if (part.type === 'tool_output') {
    if (isCodeExecutionOutput(part)) {
      return <div className="h-px" aria-hidden />
    }
    if (!partText.trim()) return <div className="h-px" aria-hidden />
    if (isOperationTool(part.tool || message.toolName)) {
      return (
        <OperationToolOutputRow
          text={partText}
          failed={part.status === 'failed' || message.toolStatus === 'failed'}
        />
      )
    }
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
        code={part.text || message.text || ''}
        isStreaming={part.status === 'running' || part.status === 'streaming'}
        stepNumber={typeof data.stepNumber === 'number' ? data.stepNumber : message.stepNumber}
        scriptPath={typeof data.scriptPath === 'string' ? data.scriptPath : message.scriptPath}
        scriptAbsPath={typeof data.scriptAbsPath === 'string' ? data.scriptAbsPath : message.scriptAbsPath}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    )
  }

  if (part.type === 'artifact') {
    if (data.kind === 'image') {
      return (
        <ImageRow
          caption={part.text || message.text}
          images={Array.isArray(data.images) ? data.images as string[] : message.images}
          files={Array.isArray(data.files) ? data.files as string[] : message.files}
        />
      )
    }
    if (!partText.trim()) return <div className="h-px" aria-hidden />
    return <MarkdownBlock markdown={partText} baseDir={markdownBaseDirFor(message.markdownBaseDir, message.files)} />
  }

  if (part.type === 'plan') {
    return <PlanRow planData={data.planData as ChatMessage['planData']} />
  }

  if (part.type === 'progress') {
    if (data.kind === 'subagent') {
      return <SubagentRow data={data.subagentData as ChatMessage['subagentData']} />
    }
    if (shouldRenderProgressPart(data)) {
      return (
        <ProgressRow
          stage={typeof data.stage === 'string' ? data.stage : 'processing'}
          detail={partText || (typeof data.message === 'string' ? data.message : undefined)}
          status={part.status}
        />
      )
    }
    return <div className="h-px" aria-hidden />
  }

  if (part.type === 'approval') {
    if (data.kind === 'screenshot') {
      const screenshotData = data.screenshotData as ChatMessage['screenshotData']
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
    return <ErrorRow text={partText} />
  }

  return <div className="h-px" aria-hidden />
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

function isCodeExecutionOutput(part: MessagePart): boolean {
  const data = part.data ?? {}
  return (
    part.tool === 'execute_code'
    || part.tool === 'gis_execute_python'
    || data.stepNumber != null
    || data.step != null
  )
}

function shouldRenderProgressPart(
  data: Record<string, unknown>,
): boolean {
  const stage = typeof data.stage === 'string' ? data.stage : ''
  const kind = typeof data.kind === 'string' ? data.kind : ''
  if (kind === 'runner_control') return true
  if (stage === 'retrying' || stage === 'installing_packages') return true
  return false
}
