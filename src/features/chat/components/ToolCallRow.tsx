import { memo } from 'react'
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

/**
 * ToolCallRow — Cline-inspired tool call renderer.
 * Shows tool calls with icons, collapsible content, and status indicators.
 */
export const ToolCallRow = memo(({ message, isExpanded, onToggleExpand }: ToolCallRowProps) => {
  const tool = parseTool(message)
  if (!tool) return null

  const isRunning = message.toolStatus === 'running'
  const isFailed = message.toolStatus === 'failed'
  const isCompleted = message.toolStatus === 'completed'
  const isCodeTool = isCodeExecutionTool(tool.tool)
  const codeExecution = isCodeTool ? getCodeExecutionContent(message) : null

  const { icon, title, content } = getToolDisplay(tool, message)
  const showDetails = shouldShowToolDetails(tool.tool, message)
  const detailLabel = getDetailLabel(tool.tool, tool)
  const durationLabel = formatDuration(message.durationMs ?? 0)
  const effectiveExpanded = isCodeTool && isRunning && !!codeExecution?.output.trim()
    ? true
    : isExpanded

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
            onClick={onToggleExpand}
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
    return {
      tool: message.toolName,
      path: message.toolArgs?.path as string | undefined,
      content: buildToolContent(message),
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

function shouldShowToolDetails(toolName: string, message: UIMessage): boolean {
  if (isCodeExecutionTool(toolName)) {
    return !!message.text?.trim()
  }
  return message.toolStatus === 'failed'
}

function getDetailLabel(toolName: string, tool: ToolInfo): string {
  if (isCodeExecutionTool(toolName)) return 'Python output'
  return tool.path ? `${tool.path}\u200E` : 'Error details'
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
    case 'use_skill': {
      const skillName = message.toolArgs?.skill_name as string || 'skill'
      return {
        icon: <Code2 className={iconSize} />,
        title: `Running Python skill: ${skillName}`,
        content: tool.content || null,
      }
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
