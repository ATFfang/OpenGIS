/**
 * CodeViewer — displays source code with syntax highlighting,
 * line numbers, and execution results panel.
 *
 * Features:
 * - Syntax highlighting via react-syntax-highlighter
 * - Line numbers
 * - Copy button
 * - Code execution panel (for .py files)
 * - Execution result display (stdout, stderr, return value)
 */

import { useState, useCallback, useEffect, useMemo } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import {
  Copy,
  Check,
  X,
  AlertCircle,
  CheckCircle2,
  Clock,
  Terminal,
  FileCode,
  GitBranch,
  Eye,
  FileCode2,
} from 'lucide-react'
import { useViewStore, type CodeExecutionResult } from '@/stores/viewStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useT } from '@/i18n'
import type { ViewTab } from '@/stores/viewStore'
import MarkdownRenderer from '@/features/chat/components/MarkdownBlock'
import { pathToImageUrl } from '@/services/rpc/handlers/_image_url'

// ─── Syntax highlighter themes ──────────────────────────────────

const darkTheme = {
  ...oneDark,
  'pre[class*="language-"]': {
    ...oneDark['pre[class*="language-"]'],
    background: 'var(--bg-tertiary)',
    margin: 0,
    padding: '12px',
    fontSize: '13px',
    lineHeight: '1.5',
  },
  'code[class*="language-"]': {
    ...oneDark['code[class*="language-"]'],
    background: 'var(--bg-tertiary)',
    fontSize: '13px',
    lineHeight: '1.5',
  },
}

const lightTheme = {
  ...oneLight,
  'pre[class*="language-"]': {
    ...oneLight['pre[class*="language-"]'],
    background: 'var(--bg-tertiary)',
    margin: 0,
    padding: '12px',
    fontSize: '13px',
    lineHeight: '1.5',
  },
  'code[class*="language-"]': {
    ...oneLight['code[class*="language-"]'],
    background: 'var(--bg-tertiary)',
    fontSize: '13px',
    lineHeight: '1.5',
  },
}

// ─── Main Component ──────────────────────────────────────────────

interface CodeViewerProps {
  tab: ViewTab
}

export function CodeViewer({ tab }: CodeViewerProps) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const [showResult, setShowResult] = useState(!!tab.executionResult)
  const [mdRendered, setMdRendered] = useState(true) // Default to rendered view for .md

  const isMarkdown = tab.language === 'markdown'

  // Detect current dark/light mode
  const theme = useSettingsStore((s) => s.appearance.theme)
  const isDarkMode = theme === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    : theme === 'dark'

  const syntaxTheme = isDarkMode ? darkTheme : lightTheme
  const lineNumberColor = isDarkMode ? '#4a4a6a' : '#b0b0b0'

  // Directory of the markdown file for resolving relative image paths
  const mdDir = useMemo(() => {
    if (!tab.filePath) return ''
    return tab.filePath.substring(0, tab.filePath.lastIndexOf('/'))
  }, [tab.filePath])

  // Auto-show result when it arrives
  useEffect(() => {
    if (tab.executionResult) {
      setShowResult(true)
    }
  }, [tab.executionResult])

  // ─── Copy handler ───────────────────────────────────────────

  const handleCopy = useCallback(async () => {
    if (!tab.content) return
    await navigator.clipboard.writeText(tab.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [tab.content])

  // ─── Toggle result panel ─────────────────────────────────────

  const handleToggleResult = useCallback(() => {
    setShowResult((prev) => !prev)
  }, [])

  // ─── Determine language ──────────────────────────────────────

  const language = tab.language || 'text'

  return (
    <div className="flex flex-col h-full bg-bg-tertiary">
      {/* Toolbar */}
      <div className="h-9 border-b border-border bg-bg-secondary flex items-center px-3 shrink-0 gap-1">
        {/* File icon + name */}
        <FileCode className="w-3.5 h-3.5 text-yellow-400 shrink-0" />
        <span className="text-xs text-text-secondary truncate flex-1">{tab.title}</span>

        {/* Execution status indicator */}
        {tab.isExecuting && (
          <div className="flex items-center gap-1 text-xs text-accent-warning">
            <Clock className="w-3 h-3 animate-spin" />
            <span>{t.codeViewer.running}</span>
          </div>
        )}
        {tab.executionResult && !tab.isExecuting && (
          <div className="flex items-center gap-1 text-xs">
            {tab.executionResult.success ? (
              <CheckCircle2 className="w-3 h-3 text-accent-success" />
            ) : (
              <AlertCircle className="w-3 h-3 text-accent-danger" />
            )}
            <span className="text-text-muted">
              {Math.round(tab.executionResult.execution_time_ms)}ms
            </span>
          </div>
        )}

        {/* Toggle result panel */}
        {tab.executionResult && (
          <button
            onClick={handleToggleResult}
            className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${
              showResult
                ? 'text-accent-primary bg-accent-primary/10'
                : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
            }`}
            title={showResult ? t.codeViewer.hideOutput : t.codeViewer.showOutput}
          >
            <Terminal className="w-3.5 h-3.5" />
          </button>
        )}

        {/* Copy button */}
        <button
          onClick={handleCopy}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title={t.codeViewer.copyCode}
        >
          {copied ? (
            <Check className="w-3.5 h-3.5 text-accent-success" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {/* Toggle rendered/raw for markdown */}
      {isMarkdown && (
        <div className="h-8 border-b border-border bg-bg-secondary flex items-center px-3 shrink-0 gap-2">
          <button
            onClick={() => setMdRendered(true)}
            className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${
              mdRendered
                ? 'bg-accent-primary/10 text-accent-primary'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <Eye className="w-3 h-3" />
            <span>Rendered</span>
          </button>
          <button
            onClick={() => setMdRendered(false)}
            className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${
              !mdRendered
                ? 'bg-accent-primary/10 text-accent-primary'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <FileCode2 className="w-3 h-3" />
            <span>Source</span>
          </button>
        </div>
      )}

      {/* Code + Result split */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {/* Code area */}
        <div className="flex-1 overflow-auto min-h-0">
          {isMarkdown && mdRendered ? (
            <div className="p-6 max-w-[800px] mx-auto text-[14px] leading-[1.8] text-text-primary">
              <MarkdownRenderer
                markdown={tab.content || ''}
                resolveImageSrc={mdDir ? (relPath: string) => {
                  // If it's already an absolute URL, return as-is
                  if (/^https?:\/\//.test(relPath)) return Promise.resolve(relPath)
                  // Resolve relative path against the markdown file's directory
                  const absPath = `${mdDir}/${relPath}`
                  return pathToImageUrl(absPath)
                } : undefined}
              />
            </div>
          ) : (
            <SyntaxHighlighter
              language={language}
              style={syntaxTheme}
              showLineNumbers
              lineNumberStyle={{
                minWidth: '3em',
                paddingRight: '1em',
                color: lineNumberColor,
                userSelect: 'none',
              }}
              customStyle={{
                margin: 0,
                borderRadius: 0,
                height: '100%',
              }}
              wrapLines
              wrapLongLines
            >
              {tab.content || ''}
            </SyntaxHighlighter>
          )}
        </div>

        {/* Execution result panel */}
        {showResult && tab.executionResult && (
          <ExecutionResultPanel
            result={tab.executionResult}
            onClose={() => setShowResult(false)}
          />
        )}
      </div>
    </div>
  )
}

// ─── Execution Result Panel ─────────────────────────────────────

interface ExecutionResultPanelProps {
  result: CodeExecutionResult
  onClose: () => void
}

function ExecutionResultPanel({ result, onClose }: ExecutionResultPanelProps) {
  const t = useT()
  const [activeSection, setActiveSection] = useState<'stdout' | 'stderr' | 'result'>(
    result.error ? 'stderr' : 'stdout'
  )

  const hasStdout = !!result.stdout?.trim()
  const hasStderr = !!result.stderr?.trim()
  const hasReturnValue = result.return_value !== null && result.return_value !== undefined

  return (
    <div className="border-t border-border bg-bg-primary shrink-0 max-h-[40%] flex flex-col">
      {/* Result header */}
      <div className="h-8 border-b border-border flex items-center px-3 gap-2 shrink-0">
        {result.success ? (
          <CheckCircle2 className="w-3.5 h-3.5 text-accent-success shrink-0" />
        ) : (
          <AlertCircle className="w-3.5 h-3.5 text-accent-danger shrink-0" />
        )}
        <span className="text-xs text-text-secondary">
          {result.success ? t.codeViewer.executedSuccessfully : t.codeViewer.errorType.replace('{type}', result.error_type || t.codeViewer.unknown)}
        </span>
        <span className="text-2xs text-text-muted">
          {Math.round(result.execution_time_ms)}ms
        </span>
        <div className="flex-1" />

        {/* Section tabs */}
        <div className="flex items-center gap-0.5">
          {hasStdout && (
            <SectionTab
              active={activeSection === 'stdout'}
              onClick={() => setActiveSection('stdout')}
              label={t.codeViewer.output}
            />
          )}
          {hasStderr && (
            <SectionTab
              active={activeSection === 'stderr'}
              onClick={() => setActiveSection('stderr')}
              label={t.codeViewer.stderr}
              hasError
            />
          )}
          {hasReturnValue && (
            <SectionTab
              active={activeSection === 'result'}
              onClick={() => setActiveSection('result')}
              label={t.codeViewer.result}
            />
          )}
        </div>

        <button
          onClick={onClose}
          className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
        >
          <X className="w-3 h-3" />
        </button>
      </div>

      {/* Error message */}
      {result.error && (
        <div className="px-3 py-2 bg-accent-danger/10 border-b border-accent-danger/20 shrink-0">
          <p className="text-xs text-accent-danger font-mono">{result.error}</p>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-auto min-h-0 p-3">
        {activeSection === 'stdout' && (
          <pre className="text-xs text-text-primary font-mono whitespace-pre-wrap break-words">
            {result.stdout || t.codeViewer.noOutput}
          </pre>
        )}
        {activeSection === 'stderr' && (
          <pre className="text-xs text-accent-warning font-mono whitespace-pre-wrap break-words">
            {result.stderr || t.codeViewer.noStderr}
          </pre>
        )}
        {activeSection === 'result' && (
          <pre className="text-xs text-text-primary font-mono whitespace-pre-wrap break-words">
            {JSON.stringify(result.return_value, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}

// ─── Section Tab ────────────────────────────────────────────────

function SectionTab({
  active,
  onClick,
  label,
  hasError,
}: {
  active: boolean
  onClick: () => void
  label: string
  hasError?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2 py-0.5 rounded text-2xs font-medium transition-colors ${
        active
          ? hasError
            ? 'bg-accent-danger/15 text-accent-danger'
            : 'bg-accent-primary/15 text-accent-primary'
          : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'
      }`}
    >
      {label}
    </button>
  )
}

// ─── Code Tab Header (used in MainLayout) ────────────────────────

interface CodeTabHeaderProps {
  tabs: ViewTab[]
  activeTabId: string
  onTabClick: (tabId: string) => void
  onTabClose: (tabId: string) => void
}

export function CodeTabHeader({ tabs, activeTabId, onTabClick, onTabClose }: CodeTabHeaderProps) {
  if (tabs.length === 0) return null

  return (
    <div className="h-9 border-b border-border bg-bg-secondary flex items-center shrink-0 overflow-x-auto scrollbar-none">
      {tabs.map((tab) => {
        // Workflow tabs get a dedicated icon so they stand out from
        // regular source files in a crowded tab bar.
        const isWorkflow = tab.language === 'workflow'
        return (
        <div
          key={tab.id}
          className={`
            group/tab flex items-center gap-1.5 px-3 h-full cursor-pointer
            border-r border-border shrink-0 transition-colors
            ${tab.id === activeTabId
              ? 'bg-bg-primary text-text-primary'
              : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
            }
          `}
          onClick={() => onTabClick(tab.id)}
        >
          {isWorkflow ? (
            <GitBranch className="w-3 h-3 text-accent-geo shrink-0" />
          ) : (
            <FileCode className="w-3 h-3 text-yellow-400 shrink-0" />
          )}
          <span className="text-xs truncate max-w-[120px]">{tab.title}</span>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onTabClose(tab.id)
            }}
            className="w-4 h-4 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors opacity-0 group-hover/tab:opacity-100"
          >
            <X className="w-2.5 h-2.5" />
          </button>
        </div>
        )
      })}
    </div>
  )
}
