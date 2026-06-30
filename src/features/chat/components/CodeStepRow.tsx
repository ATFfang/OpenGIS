import { memo, useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Code2,
  ExternalLink,
  AlertCircle,
} from 'lucide-react'
import type { UIMessage } from '@/types/chat'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { useChatCodeTheme } from './useChatCodeTheme'
import MarkdownBlock from './MarkdownBlock'

interface CodeStepRowProps {
  message: UIMessage
  /** Controlled expanded state (wired from parent). */
  isExpanded: boolean
  onToggleExpand: () => void
}

// Delay before auto-collapsing a freshly-finished code block. Long enough
// for the eye to register "done", short enough to feel snappy.
const AUTO_COLLAPSE_DELAY_MS = 350

/**
 * CodeStepRow — renders one Python step emitted by the CodeAgent.
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
export const CodeStepRow = memo(({ message, isExpanded, onToggleExpand }: CodeStepRowProps) => {
  const stepNumber = message.stepNumber ?? 0
  const scriptPath = message.scriptPath ?? ''
  const absPath = message.scriptAbsPath ?? ''
  const code = message.text ?? ''
  const isStreaming = message.partial === true
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

  return (
    <div className="group">
      {/* Header: step + clickable path */}
      <div className="flex items-center gap-2.5 mb-2">
        <div className="w-5 h-5 rounded-md flex items-center justify-center bg-accent-primary/10">
          <Code2 className={`w-3 h-3 text-accent-primary ${isStreaming ? 'animate-pulse' : ''}`} />
        </div>
        <span className="font-semibold text-[13px] text-text-primary">
          Step {stepNumber || '?'}
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
        <div className="bg-bg-tertiary/50 rounded-xl overflow-hidden border border-border/60 ml-[30px]">
          <button
            onClick={handleHeaderClick}
            disabled={isStreaming}
            className={`w-full flex items-center text-text-muted py-2 px-3 select-none bg-transparent border-none text-left text-[12px] transition-all duration-150 ${
              isStreaming
                ? 'cursor-default'
                : 'cursor-pointer hover:text-text-secondary hover:bg-bg-hover/50'
            }`}
          >
            <span className="mr-2 flex-1 text-left text-[11px] uppercase tracking-wider">
              {effectiveExpanded ? 'Hide code' : 'Show code'}
              {lineCount > 0 && (
                <span className="text-text-muted/60 normal-case tracking-normal ml-2">
                  ({lineCount} {lineCount === 1 ? 'line' : 'lines'})
                </span>
              )}
            </span>
            {!isStreaming && (
              <span className="transition-transform duration-150">
                {effectiveExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5 shrink-0" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 shrink-0" />
                )}
              </span>
            )}
          </button>

          {effectiveExpanded && (
            <div className="border-t border-border/60">
              {isStreaming ? (
                // While streaming, re-highlighting the whole block on every
                // code_delta is expensive (Prism re-tokenises the full text
                // each keystroke). Show plain monospace text live; we swap in
                // the highlighted view the instant the block finishes.
                <pre
                  className="m-0 font-mono overflow-auto whitespace-pre"
                  style={{
                    fontSize: '12px',
                    lineHeight: '1.6',
                    padding: '12px 16px',
                    background: 'var(--bg-tertiary)',
                    maxHeight: '400px',
                    color: 'var(--text-secondary)',
                  }}
                >
                  <code>{code}</code>
                </pre>
              ) : (
                <SyntaxHighlighter
                  style={codeTheme}
                  language="python"
                  PreTag="div"
                  customStyle={{
                    margin: 0,
                    borderRadius: 0,
                    fontSize: '12px',
                    lineHeight: '1.6',
                    padding: '12px 16px',
                    background: 'var(--bg-tertiary)',
                    maxHeight: '400px',
                    overflowY: 'auto',
                    textShadow: 'none',
                  }}
                >
                  {code}
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
 * CodeResultRow — renders the sandbox output of a CodeAgent step.
 * Default expanded (so you see the data); errors get an alert band.
 */
interface CodeResultRowProps {
  message: UIMessage
}

export const CodeResultRow = memo(({ message }: CodeResultRowProps) => {
  const output = message.text ?? ''
  const error = message.codeError || null

  const hasOutput = !!output.trim()
  const hasError = !!error

  // Long outputs (>800 chars) or error rows default to collapsed.
  const OUTPUT_COLLAPSE_THRESHOLD = 800
  const isLongOutput = output.length > OUTPUT_COLLAPSE_THRESHOLD
  const [collapsed, setCollapsed] = useState(hasError || isLongOutput)

  // Nothing to show — skip the row entirely.
  if (!hasOutput && !hasError) return null

  // ── Success path: render output as Markdown for rich formatting
  //    (tables, lists, bold, code blocks, etc.). ──
  if (!hasError) {
    // Short output — render inline without collapse
    if (!isLongOutput) {
      return (
        <div className="ml-[30px] -mt-1">
          <div className="py-2 px-1 text-[13px] leading-[1.7] text-text-primary/85">
            <MarkdownBlock markdown={output} />
          </div>
        </div>
      )
    }

    // Long output — render with collapsible wrapper
    const lineCount = output.split('\n').length
    return (
      <div className="ml-[30px] -mt-1">
        <div className="rounded-xl overflow-hidden border bg-bg-tertiary/30 border-border/60">
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="w-full flex items-center gap-2 py-1.5 px-3 text-[11px] hover:bg-bg-hover/50 transition-colors"
          >
            <Code2 className="w-3 h-3 text-accent-primary/70 shrink-0" />
            <span className="uppercase tracking-wider font-semibold text-text-secondary/80">
              Output
              <span className="text-text-muted/50 normal-case tracking-normal ml-2">
                ({lineCount} {lineCount === 1 ? 'line' : 'lines'}, {output.length.toLocaleString()} chars)
              </span>
            </span>
            <span className="flex-1" />
            {collapsed ? (
              <ChevronRight className="w-3 h-3 text-text-muted" />
            ) : (
              <ChevronDown className="w-3 h-3 text-text-muted" />
            )}
          </button>

          {!collapsed && (
            <div className="border-t border-border/40 p-3 max-h-[400px] overflow-y-auto scrollbar-thin">
              <div className="text-[13px] leading-[1.7] text-text-primary/85">
                <MarkdownBlock markdown={output} />
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Error path: keep collapsible card so long tracebacks don't
  //    dominate the conversation, but chrome stays neutral. ──
  const stepNumber = message.stepNumber
  const lineCount = error!.split('\n').length

  return (
    <div className="ml-[30px] -mt-1">
      <div className="rounded-xl overflow-hidden border bg-bg-tertiary/30 border-border/60">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="w-full flex items-center gap-2 py-1.5 px-3 text-[11px] hover:bg-bg-hover/50 transition-colors"
        >
          <AlertCircle className="w-3 h-3 text-accent-danger/70 shrink-0" />
          <span className="uppercase tracking-wider font-semibold text-accent-danger/80">
            Error
            {stepNumber ? <span className="text-text-muted/60 normal-case ml-1.5">· step {stepNumber}</span> : null}
            <span className="text-text-muted/50 normal-case tracking-normal ml-2">
              ({lineCount} {lineCount === 1 ? 'line' : 'lines'})
            </span>
          </span>
          <span className="flex-1" />
          {collapsed ? (
            <ChevronRight className="w-3 h-3 text-text-muted" />
          ) : (
            <ChevronDown className="w-3 h-3 text-text-muted" />
          )}
        </button>

        {!collapsed && (
          <div className="border-t border-border/40">
            <pre className="text-[12px] p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-[260px] overflow-y-auto scrollbar-thin font-mono leading-relaxed text-text-secondary">
              <code>{error}</code>
            </pre>
          </div>
        )}
      </div>
    </div>
  )
})

CodeResultRow.displayName = 'CodeResultRow'
