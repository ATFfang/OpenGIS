import { memo, useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Code2,
  ExternalLink,
  AlertCircle,
} from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { useChatCodeTheme } from './useChatCodeTheme'
import MarkdownBlock from './MarkdownBlock'

interface CodeStepRowProps {
  stepNumber?: number
  scriptPath?: string
  scriptAbsPath?: string
  code: string
  isStreaming: boolean
  isExpanded: boolean
  onToggleExpand: () => void
}

// Delay before auto-collapsing a freshly-finished code block. Long enough
// for the eye to register "done", short enough to feel snappy.
const AUTO_COLLAPSE_DELAY_MS = 350
const STREAMING_CODE_RENDER_LIMIT = 12_000

/**
 * CodeStepRow — renders one Python step emitted by the agent runtime.
 *
 * Streaming behaviour:
 *   - While the block is being written (`message.partial === true`), it
 *     is force-expanded so the user can watch the code appear in real
 *     time. The chevron is hidden — the user can't manually collapse a
 *     mid-flight stream.
 *   - The instant the block finishes (`partial` flips to false), we
 *     wait ~AUTO_COLLAPSE_DELAY_MS so the eye can register "done", then
 *     auto-collapse. After that point the chevron returns and the user
 *     can re-expand normally via the parent's expandedRows store.
 */
export const CodeStepRow = memo(({
  stepNumber = 0,
  scriptPath = '',
  scriptAbsPath = '',
  code,
  isStreaming,
  isExpanded,
  onToggleExpand,
}: CodeStepRowProps) => {
  const absPath = scriptAbsPath
  const { style: codeTheme } = useChatCodeTheme()

  // Local "expanded for streaming" override. Independent of the parent
  // expandedRows set so collapsing on stream-end doesn't poison the
  // user's manual toggle history.
  const [streamExpanded, setStreamExpanded] = useState<boolean>(isStreaming)
  const wasStreamingRef = useRef(isStreaming)

  useEffect(() => {
    if (isStreaming) {
      // Newly streaming — keep open.
      setStreamExpanded(true)
      wasStreamingRef.current = true
      return
    }
    if (wasStreamingRef.current) {
      // Just finished streaming. Hold the open state for one beat,
      // then collapse.
      wasStreamingRef.current = false
      const t = window.setTimeout(() => {
        setStreamExpanded(false)
      }, AUTO_COLLAPSE_DELAY_MS)
      return () => window.clearTimeout(t)
    }
  }, [isStreaming])

  const effectiveExpanded = isStreaming ? true : (streamExpanded || isExpanded)

  const handleOpenScript = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation()
      if (!absPath) return
      try {
        // Read file off disk and open it in a CodeViewer tab.
        const api = (window as any).electronAPI
        if (!api?.readFile) return
        const result = await api.readFile(absPath)
        if (!result?.success) {
          console.warn('[CodeStepRow] failed to read script:', result?.error)
          return
        }
        const { useViewStore } = await import('@/stores/viewStore')
        const fileName = scriptPath.split('/').pop() || absPath.split(/[\\/]/).pop() || 'script.py'
        useViewStore.getState().openFileAsTab(absPath, fileName, result.content)
      } catch (err) {
        console.error('[CodeStepRow] open script failed:', err)
      }
    },
    [absPath, scriptPath],
  )

  const handleHeaderClick = useCallback(() => {
    if (isStreaming) return // can't toggle while streaming
    // Sync our local override to the parent's set so subsequent renders
    // are consistent, then notify parent.
    setStreamExpanded(!effectiveExpanded)
    onToggleExpand()
  }, [isStreaming, effectiveExpanded, onToggleExpand])

  // Always render the body during streaming, even if code is still empty,
  // so the user sees the empty editor open up immediately.
  const showBody = code.length > 0 || isStreaming
  const lineCount = code ? code.split('\n').length : 0
  const streamingTruncated = isStreaming && code.length > STREAMING_CODE_RENDER_LIMIT
  const streamingCode = streamingTruncated
    ? code.slice(0, STREAMING_CODE_RENDER_LIMIT)
    : code

  return (
    <div className="group">
      {/* Header: step + clickable path */}
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-[18px] h-[18px] rounded flex items-center justify-center bg-accent-primary/10">
          <Code2 className={`w-3 h-3 text-accent-primary ${isStreaming ? 'animate-pulse' : ''}`} />
        </div>
        <span className="font-semibold text-[12px] text-text-primary">
          Python
          {isStreaming && (
            <span className="ml-1.5 text-[10px] uppercase tracking-wider font-normal text-accent-primary/70">
              writing…
            </span>
          )}
        </span>
        {scriptPath && (
          <button
            type="button"
            onClick={handleOpenScript}
            title={`Open ${absPath || scriptPath}`}
            className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent-primary font-mono transition-colors max-w-[60%]"
          >
            <span className="truncate">{scriptPath}</span>
            <ExternalLink className="w-3 h-3 shrink-0 opacity-60" />
          </button>
        )}
      </div>

      {/* Collapsible code body */}
      {showBody && (
        <div
          className="bg-bg-tertiary/90 rounded-lg overflow-hidden"
          style={{ border: '1px solid color-mix(in srgb, var(--border-color) 50%, transparent)' }}
        >
          <button
            onClick={handleHeaderClick}
            disabled={isStreaming}
            className={`w-full flex items-center text-text-muted py-2 px-3 select-none border-none text-left text-[11px] transition-all duration-150 ${
              isStreaming
                ? 'cursor-default bg-bg-hover/35'
                : 'cursor-pointer bg-bg-hover/45 hover:text-text-secondary hover:bg-bg-hover/65'
            }`}
          >
            <span className="mr-2 flex-1 text-left text-[11px] uppercase tracking-wider">
              {effectiveExpanded ? 'Hide code' : 'Show code'}
              {lineCount > 0 && (
                <span className="text-text-muted/60 normal-case tracking-normal ml-2">
                  ({lineCount} {lineCount === 1 ? 'line' : 'lines'}{stepNumber ? ` · #${stepNumber}` : ''})
                </span>
              )}
            </span>
            {!isStreaming && (
              <span className="transition-transform duration-150">
                {effectiveExpanded ? (
                  <ChevronDown className="w-3 h-3 shrink-0" />
                ) : (
                  <ChevronRight className="w-3 h-3 shrink-0" />
                )}
              </span>
            )}
          </button>

          {effectiveExpanded && (
            <div style={{ borderTop: '1px solid color-mix(in srgb, var(--border-color) 50%, transparent)' }}>
              {isStreaming ? (
                <pre
                  className="m-0 max-h-[340px] overflow-auto bg-bg-tertiary/65 px-3.5 py-3 font-mono text-[11.75px] leading-[1.58] text-text-primary whitespace-pre"
                  style={{ textShadow: 'none' }}
                >
                  {streamingCode || ' '}
                  {streamingTruncated && (
                    `\n\n… live preview paused after ${STREAMING_CODE_RENDER_LIMIT.toLocaleString()} chars. Full code will be available when writing completes.`
                  )}
                </pre>
              ) : (
                <SyntaxHighlighter
                  style={codeTheme}
                  language="python"
                  PreTag="div"
                  customStyle={{
                    margin: 0,
                    borderRadius: 0,
                    fontSize: '11.75px',
                    lineHeight: '1.58',
                    padding: '12px 14px',
                    background: 'color-mix(in srgb, var(--bg-tertiary) 66%, transparent)',
                    maxHeight: '340px',
                    overflowY: 'auto',
                    textShadow: 'none',
                  }}
                >
                  {code || ' '}
                </SyntaxHighlighter>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
})

CodeStepRow.displayName = 'CodeStepRow'


/**
 * CodeResultRow — renders the sandbox output of an agent Python step.
 * Features:
 *   - Duration display (execution time)
 *   - Diff detection and syntax-highlighted rendering
 *   - DataFrame/pipe-table detection and HTML table rendering
 *   - Collapsible long outputs
 */
interface CodeResultRowProps {
  output: string
  error?: string | null
  durationMs?: number
  stepNumber?: number
}

/** Format milliseconds as a human-readable duration string. */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}

/** Detect if output looks like a unified diff. */
function isDiffOutput(text: string): boolean {
  const lines = text.split('\n')
  let diffMarkers = 0
  for (const line of lines.slice(0, 30)) {
    if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@') || line.startsWith('diff --git')) {
      diffMarkers++
    }
  }
  return diffMarkers >= 2
}

/** Detect if output looks like a pipe-separated DataFrame table. */
function isDataFrameOutput(text: string): boolean {
  const lines = text.split('\n').filter((l) => l.trim())
  if (lines.length < 3) return false
  // Look for a separator line like |---|---|---|
  const hasSeparator = lines.some((l) => /^\|?[\s]*:?-{3,}/.test(l) && l.includes('|'))
  // Look for pipe-delimited data rows
  const pipeRows = lines.filter((l) => l.includes('|') && l.trim().startsWith('|')).length
  return hasSeparator && pipeRows >= 2
}

/**
 * DiffBlock — renders a unified diff with syntax highlighting.
 */
const DiffBlock = memo(({ diff }: { diff: string }) => {
  const lines = diff.split('\n')
  return (
    <div className="rounded-lg overflow-hidden border border-border/30 font-mono text-[12px] leading-[1.6]">
      {lines.map((line, i) => {
        let bg = 'bg-bg-tertiary/30'
        let color = 'text-text-secondary'
        if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) {
          bg = 'bg-bg-tertiary/60'
          color = 'text-text-primary font-semibold'
        } else if (line.startsWith('@@')) {
          bg = 'bg-accent-primary/8'
          color = 'text-accent-primary/80'
        } else if (line.startsWith('+')) {
          bg = 'bg-green-500/10'
          color = 'text-green-400'
        } else if (line.startsWith('-')) {
          bg = 'bg-red-500/10'
          color = 'text-red-400'
        }
        return (
          <div key={i} className={`px-3 py-0.5 ${bg} ${color} whitespace-pre`}>
            {line || ' '}
          </div>
        )
      })}
    </div>
  )
})
DiffBlock.displayName = 'DiffBlock'

/**
 * DataFrameTable — renders a pipe-separated DataFrame output as an HTML table.
 */
const DataFrameTable = memo(({ text }: { text: string }) => {
  const lines = text.split('\n').filter((l) => l.trim())
  // Find the separator line to split header from body
  const sepIdx = lines.findIndex((l) => /^\|?[\s]*:?-{3,}/.test(l) && l.includes('|'))
  if (sepIdx < 1) return <MarkdownBlock markdown={text} />

  const parseRow = (line: string) =>
    line
      .split('|')
      .map((c) => c.trim())
      .filter((c) => c.length > 0)

  const headers = parseRow(lines[sepIdx - 1])
  const bodyLines = lines.slice(sepIdx + 1).filter((l) => l.includes('|') && !/^\|?[\s]*:?-{3,}/.test(l))
  const rows = bodyLines.map(parseRow)

  if (headers.length === 0 || rows.length === 0) return <MarkdownBlock markdown={text} />

  return (
    <div className="overflow-x-auto rounded-lg border border-border/30">
      <table className="border-collapse w-full text-[12px] font-mono">
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th
                key={i}
                className="px-2.5 py-1.5 border-b border-border/35 text-left bg-bg-tertiary/50 font-semibold text-text-primary whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className={ri % 2 === 0 ? '' : 'bg-bg-tertiary/20'}>
              {row.map((cell, ci) => (
                <td key={ci} className="px-2.5 py-1 border-b border-border/30 text-text-secondary whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})
DataFrameTable.displayName = 'DataFrameTable'

/**
 * Smart output renderer — picks the best rendering strategy.
 */
function SmartOutput({ text }: { text: string }) {
  if (isDiffOutput(text)) return <DiffBlock diff={text} />
  if (isDataFrameOutput(text)) return <DataFrameTable text={text} />
  return <MarkdownBlock markdown={text} />
}

export const CodeResultRow = memo(({ output, error = null, durationMs, stepNumber }: CodeResultRowProps) => {
  const hasOutput = !!output.trim()
  const hasError = !!error

  // Long outputs (>800 chars) or error rows default to collapsed.
  const OUTPUT_COLLAPSE_THRESHOLD = 800
  const isLongOutput = output.length > OUTPUT_COLLAPSE_THRESHOLD
  const [collapsed, setCollapsed] = useState(hasError || isLongOutput)

  // Nothing to show — skip the row entirely.
  if (!hasOutput && !hasError) return null

  // Format duration badge
  const durationBadge = durationMs != null && durationMs > 0 ? formatDuration(durationMs) : null

  // ── Success path ──
  if (!hasError) {
    // Short output — render inline without collapse
    if (!isLongOutput) {
      return (
        <div className="-mt-1">
          <div className="py-2 px-1 text-[13px] leading-[1.7] text-text-primary/85">
            <SmartOutput text={output} />
          </div>
          {durationBadge && (
            <div className="px-1 pb-1">
              <span className="text-[10px] text-text-muted/50 font-mono">{durationBadge}</span>
            </div>
          )}
        </div>
      )
    }

    // Long output — render with collapsible wrapper
    const lineCount = output.split('\n').length
    return (
      <div className="-mt-1">
        <div className="rounded-lg overflow-hidden border bg-bg-secondary/55 border-border/12">
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="w-full flex items-center gap-1.5 py-1.5 px-3 text-[10.5px] hover:bg-bg-hover/35 transition-colors"
          >
            <Code2 className="w-3 h-3 text-accent-primary/70 shrink-0" />
            <span className="uppercase tracking-wider font-semibold text-text-secondary/80">
              Output
              <span className="text-text-muted/50 normal-case tracking-normal ml-2">
                ({lineCount} {lineCount === 1 ? 'line' : 'lines'}, {output.length.toLocaleString()} chars)
              </span>
            </span>
            {durationBadge && (
              <span className="text-[10px] text-text-muted/40 font-mono">{durationBadge}</span>
            )}
            <span className="flex-1" />
            {collapsed ? (
              <ChevronRight className="w-3 h-3 text-text-muted" />
            ) : (
              <ChevronDown className="w-3 h-3 text-text-muted" />
            )}
          </button>

          {!collapsed && (
            <div className="border-t border-border/12 p-3 max-h-[340px] overflow-y-auto scrollbar-thin">
              <div className="text-[12px] leading-[1.6] text-text-primary/85">
                <SmartOutput text={output} />
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Error path ──
  const lineCount = error!.split('\n').length

  return (
    <div className="-mt-1">
      <div className="rounded-lg overflow-hidden border bg-bg-secondary/55 border-border/12">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="w-full flex items-center gap-1.5 py-1.5 px-3 text-[10.5px] hover:bg-bg-hover/35 transition-colors"
        >
          <AlertCircle className="w-3 h-3 text-accent-danger/70 shrink-0" />
          <span className="uppercase tracking-wider font-semibold text-accent-danger/80">
            Error
            {stepNumber ? <span className="text-text-muted/60 normal-case ml-1.5">· step {stepNumber}</span> : null}
            <span className="text-text-muted/50 normal-case tracking-normal ml-2">
              ({lineCount} {lineCount === 1 ? 'line' : 'lines'})
            </span>
          </span>
          {durationBadge && (
            <span className="text-[10px] text-text-muted/40 font-mono">{durationBadge}</span>
          )}
          <span className="flex-1" />
          {collapsed ? (
            <ChevronRight className="w-3 h-3 text-text-muted" />
          ) : (
            <ChevronDown className="w-3 h-3 text-text-muted" />
          )}
        </button>

        {!collapsed && (
          <div className="border-t border-border/12">
            <pre className="text-[11.5px] p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-[240px] overflow-y-auto scrollbar-thin font-mono leading-relaxed text-text-secondary">
              <code>{error}</code>
            </pre>
          </div>
        )}
      </div>
    </div>
  )
})

CodeResultRow.displayName = 'CodeResultRow'
