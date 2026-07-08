import { memo, useEffect, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Terminal,
  Wrench,
  FileCode2,
  FilePlus2,
  PencilIcon,
  Search,
  FolderOpen,
  SquareMinus,
  Loader2,
  Globe,
  BarChart3,
  Database,
  Code2,
  AlertCircle,
} from 'lucide-react'
import type { UIMessage } from '@/types/chat'

interface ToolInfo {
  tool: string
  path?: string
  content?: string
  diff?: string
  regex?: string
  description?: string
}

interface ToolCallRowProps {
  message: UIMessage
  isExpanded: boolean
  onToggleExpand: () => void
}

interface EditFileResult {
  path?: string
  diff: string
  matchStrategy?: string
  replacements?: number
  additions?: number
  deletions?: number
}

/**
 * ToolCallRow — Cline-inspired tool call renderer.
 * Shows tool calls with icons, collapsible content, and status indicators.
 */
export const ToolCallRow = memo(({ message, isExpanded, onToggleExpand }: ToolCallRowProps) => {
  const [editDiffExpanded, setEditDiffExpanded] = useState(true)
  const tool = parseTool(message)
  const editResult = tool && isEditFileTool(tool.tool) ? getEditFileResult(message, tool) : null

  useEffect(() => {
    if (editResult?.diff) setEditDiffExpanded(true)
  }, [message.toolCallId, editResult?.diff])

  if (!tool) return null

  const isRunning = message.toolStatus === 'running'
  const isFailed = message.toolStatus === 'failed'
  const isCompleted = message.toolStatus === 'completed'
  const isCodeTool = isCodeExecutionTool(tool.tool)
  const codeExecution = isCodeTool ? getCodeExecutionContent(message) : null

  const { icon, title, content } = getToolDisplay(tool, message)
  const showDetails = shouldShowToolDetails(tool.tool, message, editResult)
  const detailLabel = getDetailLabel(tool.tool, tool)
  const durationLabel = formatDuration(message.durationMs ?? 0)
  const effectiveExpanded = editResult
    ? editDiffExpanded
    : isCodeTool && isRunning && !!codeExecution?.output.trim()
      ? true
      : isExpanded

  const handleToggleDetails = () => {
    if (editResult) {
      setEditDiffExpanded((value) => !value)
      return
    }
    onToggleExpand()
  }

  return (
    <div className="group">
      {/* Header */}
      <div className="flex items-center gap-2.5 mb-2">
        <div className={`w-5 h-5 rounded-md flex items-center justify-center ${
          isRunning ? 'bg-accent-primary/10' :
          isFailed ? 'bg-accent-danger/10' :
          isCompleted ? 'bg-accent-success/10' :
          'bg-bg-tertiary'
        }`}>
          {isRunning ? (
            <Loader2 className="w-3 h-3 animate-spin text-accent-primary" />
          ) : (
            <span className={isFailed ? 'text-accent-danger' : 'text-text-secondary'}>{icon}</span>
          )}
        </div>
        <span className={`font-semibold text-[13px] ${
          isFailed ? 'text-accent-danger' : 'text-text-primary'
        }`}>
          {title}
        </span>
        {isRunning && (
          <span className="text-[10px] text-accent-primary font-medium animate-pulse">running</span>
        )}
        {isCompleted && (
          <span className="text-[10px] text-text-muted font-mono">{durationLabel}</span>
        )}
      </div>

      {/* Collapsible content */}
      {showDetails && content && (
        <div className="bg-bg-tertiary/50 rounded-xl overflow-hidden border border-border/30 ml-[30px]">
          <button
            onClick={handleToggleDetails}
            className="w-full flex items-center text-text-muted py-2 px-3 cursor-pointer select-none bg-transparent border-none text-left text-[12px] hover:text-text-secondary hover:bg-bg-hover/50 transition-all duration-150"
          >
            <span className="whitespace-nowrap overflow-hidden text-ellipsis mr-2 flex-1 text-left font-mono [direction:rtl]">
              {detailLabel}
            </span>
            <span className="transition-transform duration-150">
              {effectiveExpanded ? (
                <ChevronDown className="w-3.5 h-3.5 shrink-0" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 shrink-0" />
              )}
            </span>
          </button>

          {effectiveExpanded && (
            <div className="border-t border-border/25">
              {isCodeTool && codeExecution ? (
                <div className="max-h-[420px] overflow-y-auto scrollbar-thin">
                  {codeExecution.output && (
                    <pre className="m-0 text-[12px] text-text-secondary p-3 overflow-x-auto whitespace-pre-wrap break-words font-mono leading-relaxed bg-bg-tertiary/50">
                      <code>{codeExecution.output}</code>
                    </pre>
                  )}
                </div>
              ) : editResult ? (
                <EditFileDiff result={editResult} />
              ) : (
                <pre className="text-[12px] text-text-secondary p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-[300px] overflow-y-auto scrollbar-thin font-mono leading-relaxed">
                  <code>{content}</code>
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
})

ToolCallRow.displayName = 'ToolCallRow'

// --- Helpers ---

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}

function parseTool(message: UIMessage): ToolInfo | null {
  if (!message.toolName && !message.text) return null

  if (message.toolName) {
    const result = isEditFileTool(message.toolName) ? parseEditFileOutput(message.text) : null
    return {
      tool: message.toolName,
      path: result?.path || (message.toolArgs?.path as string | undefined),
      content: buildToolContent(message),
      diff: result?.diff,
    }
  }

  try {
    return JSON.parse(message.text || '{}') as ToolInfo
  } catch {
    return null
  }
}

function buildToolContent(message: UIMessage): string {
  const name = message.toolName || ''
  const args = message.toolArgs || {}
  const output = message.text || ''
  if (isCodeExecutionTool(name)) {
    return output
  }
  if (isEditFileTool(name)) {
    const result = parseEditFileOutput(output)
    if (result?.diff) return result.diff
  }
  if (message.toolStatus === 'failed') {
    return output || JSON.stringify(args, null, 2)
  }
  return ''
}

function getCodeExecutionContent(message: UIMessage): { code: string; output: string } {
  const args = message.toolArgs || {}
  return {
    code: typeof args.code === 'string' ? args.code.trim() : '',
    output: message.text || '',
  }
}

function isCodeExecutionTool(toolName: string): boolean {
  return toolName === 'execute_code' || toolName === 'gis_execute_python'
}

function isEditFileTool(toolName: string): boolean {
  return toolName === 'edit_file' || toolName === 'replace_in_file' || toolName === 'editedExistingFile'
}

function shouldShowToolDetails(toolName: string, message: UIMessage, editResult?: EditFileResult | null): boolean {
  if (isCodeExecutionTool(toolName)) {
    return !!message.text?.trim()
  }
  if (isEditFileTool(toolName)) {
    return !!editResult?.diff || message.toolStatus === 'failed'
  }
  return message.toolStatus === 'failed'
}

function getDetailLabel(toolName: string, tool: ToolInfo): string {
  if (isCodeExecutionTool(toolName)) return 'Python output'
  if (isEditFileTool(toolName)) {
    return 'File changes'
  }
  return tool.path ? `${tool.path}\u200E` : 'Error details'
}

function getEditFileResult(message: UIMessage, tool: ToolInfo): EditFileResult | null {
  const parsed = parseEditFileOutput(message.text)
  if (parsed) return parsed
  if (tool.diff) {
    return {
      path: tool.path,
      diff: tool.diff,
    }
  }
  return null
}

function parseEditFileOutput(output?: string): EditFileResult | null {
  const data = parseJsonObject(output)
  if (!data) return null
  const diff = typeof data.diff === 'string' ? data.diff : ''
  if (!diff.trim()) return null
  return {
    path: typeof data.path === 'string' ? data.path : undefined,
    diff,
    matchStrategy: typeof data.match_strategy === 'string' ? data.match_strategy : undefined,
    replacements: numberOrUndefined(data.replacements),
    additions: numberOrUndefined(data.additions),
    deletions: numberOrUndefined(data.deletions),
  }
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

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

const EditFileDiff = memo(({ result }: { result: EditFileResult }) => {
  const lines = result.diff
    .split('\n')
    .map((line) => line.replace(/\r$/, ''))
    .filter((line) => line.length > 0)
  const additions = result.additions ?? lines.filter((line) => line.startsWith('+') && !line.startsWith('+++')).length
  const deletions = result.deletions ?? lines.filter((line) => line.startsWith('-') && !line.startsWith('---')).length

  return (
    <div className="bg-bg-secondary/40">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/20 text-[11px]">
        <span className="font-mono text-accent-success">+{additions}</span>
        <span className="font-mono text-accent-danger">-{deletions}</span>
        {result.replacements != null && (
          <span className="text-text-muted">
            {result.replacements} {result.replacements === 1 ? 'replacement' : 'replacements'}
          </span>
        )}
        {result.matchStrategy && (
          <span className="ml-auto text-text-muted/75 font-mono">
            {result.matchStrategy}
          </span>
        )}
      </div>
      <div className="max-h-[420px] overflow-auto scrollbar-thin">
        <div className="min-w-max font-mono text-[12px] leading-[1.55]">
          {lines.map((line, index) => {
            const style = diffLineStyle(line)
            return (
              <div
                key={`${index}:${line.slice(0, 24)}`}
                className={`grid grid-cols-[46px_1fr] ${style.bg}`}
              >
                <span className={`select-none px-2 py-0.5 text-right border-r border-border/15 ${style.gutter}`}>
                  {index + 1}
                </span>
                <span className={`px-3 py-0.5 whitespace-pre ${style.text}`}>
                  {line || ' '}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
})
EditFileDiff.displayName = 'EditFileDiff'

function diffLineStyle(line: string): { bg: string; text: string; gutter: string } {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) {
    return {
      bg: 'bg-transparent',
      text: 'text-text-primary/80 font-semibold',
      gutter: 'text-text-muted/45 bg-transparent',
    }
  }
  if (line.startsWith('@@')) {
    return {
      bg: 'bg-accent-primary/6',
      text: 'text-accent-primary/85',
      gutter: 'text-accent-primary/50 bg-accent-primary/6',
    }
  }
  if (line.startsWith('+')) {
    return {
      bg: 'bg-green-500/10',
      text: 'text-accent-success',
      gutter: 'text-accent-success/65 bg-green-500/10',
    }
  }
  if (line.startsWith('-')) {
    return {
      bg: 'bg-red-500/10',
      text: 'text-accent-danger',
      gutter: 'text-accent-danger/65 bg-red-500/10',
    }
  }
  return {
    bg: 'bg-transparent',
    text: 'text-text-secondary',
    gutter: 'text-text-muted/30 bg-transparent',
  }
}

function getToolDisplay(
  tool: ToolInfo,
  message: UIMessage
): { icon: React.ReactNode; title: string; content: string | null } {
  const iconSize = 'w-3 h-3'

  switch (tool.tool) {
    case 'editedExistingFile':
    case 'edit_file':
    case 'replace_in_file':
      return {
        icon: <PencilIcon className={iconSize} />,
        title: 'Editing file',
        content: tool.content || null,
      }
    case 'newFileCreated':
    case 'create_file':
    case 'write_to_file':
      return {
        icon: <FilePlus2 className={iconSize} />,
        title: 'Creating file',
        content: tool.content || null,
      }
    case 'fileDeleted':
    case 'delete_file':
      return {
        icon: <SquareMinus className={iconSize} />,
        title: 'Deleting file',
        content: tool.content || null,
      }
    case 'readFile':
    case 'read_file':
      return {
        icon: <FileCode2 className={iconSize} />,
        title: 'Reading file',
        content: null,
      }
    case 'listFilesTopLevel':
    case 'listFilesRecursive':
    case 'list_files':
      return {
        icon: <FolderOpen className={iconSize} />,
        title: 'Listing directory',
        content: tool.content || null,
      }
    case 'searchFiles':
    case 'search_files':
      return {
        icon: <Search className={`${iconSize} rotate-90`} />,
        title: `Searching "${tool.regex || ''}"`,
        content: tool.content || null,
      }
    case 'execute_command':
      return {
        icon: <Terminal className={iconSize} />,
        title: 'Running command',
        content: tool.content || null,
      }
    case 'gis_load_data':
      return {
        icon: <Database className={iconSize} />,
        title: 'Loading GIS data',
        content: tool.content || null,
      }
    case 'gis_buffer_analysis':
    case 'gis_overlay_analysis':
      return {
        icon: <Globe className={iconSize} />,
        title: `Running ${tool.tool.replace('gis_', '').replace('_', ' ')}`,
        content: tool.content || null,
      }
    case 'gis_render_map':
      return {
        icon: <Globe className={iconSize} />,
        title: 'Rendering map',
        content: tool.content || null,
      }
    case 'gis_render_chart':
      return {
        icon: <BarChart3 className={iconSize} />,
        title: 'Rendering chart',
        content: tool.content || null,
      }
    case 'gis_execute_python':
    case 'execute_code':
      return {
        icon: <Code2 className={iconSize} />,
        title: 'Executing Python',
        content: tool.content || null,
      }
    default:
      return {
        icon: message.toolStatus === 'failed' ? <AlertCircle className={iconSize} /> : <Wrench className={iconSize} />,
        title: `Using ${tool.tool}`,
        content: tool.content || null,
      }
  }
}
