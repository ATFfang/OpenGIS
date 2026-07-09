import { ChevronRight } from 'lucide-react'
import { useT } from '@/i18n'
import { type MessagePart, type UIMessage } from '@/types/chat'
import MarkdownBlock from './MarkdownBlock'
import { ThinkingRow } from './ThinkingRow'
import { ToolCallRow } from './ToolCallRow'
import { CodeStepRow, CodeResultRow } from './CodeStepRow'
import { ImageRow } from './ImageRow'
import PlanRow from './PlanRow'
import { SubagentRow } from './SubagentRow'
import { ScreenshotRow } from './ScreenshotRow'
import { ErrorRow, MaxStepsReachedRow, ProgressRow, UserMessageRow, markdownBaseDirFor } from './MessagePartRows'

interface MessagePartRowProps {
  message: UIMessage
  part: MessagePart
  isExpanded: boolean
  onToggleExpand: () => void
  isLast: boolean
}

export function MessagePartRow({
  message,
  part,
  isExpanded,
  onToggleExpand,
  isLast,
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
    return (
      <ToolCallRow
        toolName={part.tool || message.toolName}
        toolCallId={part.callId || part.call_id || message.toolCallId}
        toolArgs={(data.args as Record<string, unknown> | undefined) ?? (data.input as Record<string, unknown> | undefined) ?? message.toolArgs}
        output={part.text || valueToString(data.output)}
        status={part.status === 'failed'
          ? 'failed'
          : part.status === 'running' || part.status === 'streaming'
            ? 'running'
            : 'completed'}
        durationMs={typeof data.durationMs === 'number' ? data.durationMs : message.durationMs}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    )
  }

  if (part.type === 'tool_output') {
    if (part.tool === 'execute_code' || data.stepNumber != null || data.error != null) {
      return (
        <CodeResultRow
          output={part.text || valueToString(data.output)}
          error={typeof data.error === 'string' ? data.error : message.codeError}
          durationMs={typeof data.durationMs === 'number' ? data.durationMs : message.durationMs}
          stepNumber={typeof data.stepNumber === 'number' ? data.stepNumber : message.stepNumber}
        />
      )
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
    return <PlanRow planData={data.planData as UIMessage['planData']} />
  }

  if (part.type === 'progress') {
    if (data.kind === 'subagent') {
      return <SubagentRow data={data.subagentData as UIMessage['subagentData']} />
    }
    if (data.kind === 'max_steps_reached') {
      return (
        <MaxStepsReachedRow
          maxSteps={(data.maxStepsInfo as UIMessage['maxStepsInfo'] | undefined)?.maxSteps}
          isLast={isLast}
        />
      )
    }
    if (status !== 'running' && !message.partial) return <div className="h-px" aria-hidden />
    return (
      <ProgressRow
        stage={typeof data.stage === 'string' ? data.stage : message.progressStage}
        detail={partText || (typeof data.detail === 'string' ? data.detail : message.progressDetail)}
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
