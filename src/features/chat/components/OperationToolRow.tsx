import { memo, useState } from 'react'
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  FileSearch,
  Loader2,
  PackageOpen,
  Pencil,
  Play,
  ShieldCheck,
} from 'lucide-react'
import type { ToolCallStatus } from './ToolCallRow'

interface OperationToolRowProps {
  toolName?: string
  toolArgs?: Record<string, unknown>
  output?: string
  status?: ToolCallStatus
  durationMs?: number
}

export const OperationToolRow = memo(({
  toolName = '',
  toolArgs,
  output = '',
  status = 'completed',
  durationMs = 0,
}: OperationToolRowProps) => {
  const parsed = parseJsonObject(output)
  const operationId = stringValue(toolArgs?.operation_id)
    || stringValue(parsed?.operation_id)
    || stringValue((parsed?.operation as Record<string, unknown> | undefined)?.id)
    || 'operation'
  const action = operationAction(toolName)
  const isRunning = status === 'running'
  const isFailed = status === 'failed' || parsed?.success === false
  const isCompleted = status === 'completed' && !isFailed
  const error = stringValue(parsed?.error) || stringValue(parsed?.reason)
  const detail = operationDetail(toolName, parsed, toolArgs)
  const Icon = operationIcon(toolName)

  return (
    <div className="w-full max-w-[560px] overflow-hidden rounded-md border border-[var(--operation-card-border)] bg-[var(--operation-card-bg)] shadow-sm select-none">
      <div className="flex min-h-[46px]">
        <div className={`w-1.5 shrink-0 ${
          isRunning ? 'bg-accent-success animate-pulse' : isFailed ? 'bg-accent-danger' : 'bg-accent-success/80'
        }`} />
        <div className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2">
          <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded bg-[var(--operation-card-muted)] ${
            isFailed ? 'text-accent-danger' : isRunning ? 'text-accent-success' : 'text-accent-primary'
          }`}>
            {isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-xs font-semibold text-text-primary">{action}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] leading-none ${
                isFailed
                  ? 'bg-accent-danger/12 text-accent-danger'
                  : isRunning
                    ? 'bg-accent-success/12 text-accent-success'
                    : 'bg-[var(--operation-card-soft)] text-text-muted'
              }`}>
                {isRunning ? 'running' : isFailed ? 'failed' : 'done'}
              </span>
              {isCompleted && durationMs > 0 && (
                <span className="ml-auto shrink-0 font-mono text-[10px] text-text-muted">{formatDuration(durationMs)}</span>
              )}
            </div>
            <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-text-muted">
              <PackageOpen className="h-3 w-3 shrink-0" />
              <span className="truncate font-mono">{operationId}</span>
              {detail && <span className="truncate text-text-muted/80">· {detail}</span>}
            </div>
          </div>
        </div>
      </div>
      {isFailed && error && (
        <div className="border-t border-[var(--operation-card-border)] bg-accent-danger/10 px-3 py-2 text-[11px] leading-relaxed text-accent-danger select-text">
          {error}
        </div>
      )}
    </div>
  )
})

OperationToolRow.displayName = 'OperationToolRow'

export const OperationToolOutputRow = memo(({
  text = '',
  failed = false,
}: {
  text?: string
  failed?: boolean
}) => {
  const [expanded, setExpanded] = useState(false)
  const trimmed = text.trim()
  if (!trimmed) return <div className="h-px" aria-hidden />

  return (
    <div className="w-full max-w-[560px] overflow-hidden rounded-md bg-[var(--operation-card-bg)] select-none">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex min-h-8 w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] text-text-muted hover:bg-[var(--operation-card-soft)]"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span className="min-w-0 flex-1 truncate">
          {failed ? 'Operation error output' : 'Operation output'}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-text-muted/70">
          {trimmed.length.toLocaleString()} chars
        </span>
      </button>
      {expanded && (
        <pre className={`scrollbar-none max-h-60 overflow-auto whitespace-pre-wrap px-3 pb-3 pt-1 text-[11px] leading-relaxed select-text ${
          failed ? 'text-accent-danger' : 'text-text-secondary'
        }`}>
          {trimmed}
        </pre>
      )}
    </div>
  )
})

OperationToolOutputRow.displayName = 'OperationToolOutputRow'

export function isOperationTool(toolName?: string): boolean {
  return !!toolName && OPERATION_TOOLS.has(toolName)
}

const OPERATION_TOOLS = new Set([
  'list_operations',
  'get_operation',
  'validate_operation',
  'run_operation',
  'create_operation',
  'edit_operation',
  'promote_script_to_operation',
])

function operationAction(toolName: string): string {
  switch (toolName) {
    case 'list_operations':
      return 'Listing operations'
    case 'get_operation':
      return 'Inspecting operation'
    case 'validate_operation':
      return 'Validating operation'
    case 'run_operation':
      return 'Running operation'
    case 'create_operation':
      return 'Creating operation'
    case 'edit_operation':
      return 'Editing operation'
    case 'promote_script_to_operation':
      return 'Promoting script'
    default:
      return 'Using operation'
  }
}

function operationIcon(toolName: string) {
  switch (toolName) {
    case 'get_operation':
    case 'list_operations':
      return FileSearch
    case 'validate_operation':
      return ShieldCheck
    case 'run_operation':
      return Play
    case 'edit_operation':
      return Pencil
    case 'create_operation':
    case 'promote_script_to_operation':
      return PackageOpen
    default:
      return AlertCircle
  }
}

function operationDetail(
  toolName: string,
  parsed: Record<string, unknown> | null,
  args?: Record<string, unknown>,
): string {
  if (toolName === 'list_operations') {
    const count = Array.isArray(parsed?.operations) ? parsed.operations.length : undefined
    return count == null ? '' : `${count} found`
  }
  if (toolName === 'validate_operation') {
    if (parsed?.ok === true) return 'contract ok'
    if (parsed?.ok === false) {
      const errors = Array.isArray(parsed.errors) ? parsed.errors.length : 0
      return `${errors} issue${errors === 1 ? '' : 's'}`
    }
  }
  if (toolName === 'run_operation') {
    const runId = stringValue(parsed?.run_id)
    return runId ? `run ${runId}` : ''
  }
  return stringValue(args?.description) || stringValue(parsed?.status)
}

function parseJsonObject(text?: string): Record<string, unknown> | null {
  if (!text?.trim()) return null
  try {
    const value = JSON.parse(text)
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}
