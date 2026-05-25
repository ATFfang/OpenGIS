/**
 * ScriptRunnerView — a code editor + runner panel that lets the user
 * author Python scripts and execute them inside the Stage 2 subprocess
 * sandbox *without* going through the LLM. Same executor, same skill
 * bindings, same workspace cwd. Think: "Jupyter cell" for OpenGIS.
 *
 * Layout:
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │ toolbar  ▶ Run  ⏹ Stop  💾 Save  📂 Open  ✨ Clear  │
 *   ├──────────────────────────────────────────────────────┤
 *   │                                                      │
 *   │                Monaco editor (Python)                │
 *   │                                                      │
 *   ├──────────────────────────────────────────────────────┤
 *   │ ▶ Output  (stdout / stderr streamed live)           │
 *   └──────────────────────────────────────────────────────┘
 *
 * Design notes:
 * - Tab, Ctrl+S, Ctrl+F, multi-cursor etc. come for free with Monaco.
 * - We keep a single-file model ("untitled" → user can Save As).
 * - The editor and the terminal-like output panel are resizable via
 *   react-resizable-panels (vertical split). Defaults to 65/35.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import Editor, { type OnMount } from '@monaco-editor/react'
import {
  Play,
  Square,
  Save,
  FolderOpen,
  Trash2,
  FileCode,
  CheckCircle2,
  AlertCircle,
  Clock,
} from 'lucide-react'
import { useT } from '@/i18n'
import { useAssetStore } from '@/stores/assetStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useViewStore, type ViewTab } from '@/stores/viewStore'
import { useScriptRunner, type OutputChunk } from './useScriptRunner'

const DEFAULT_SCRIPT = `# OpenGIS Script Runner
# ----------------------------------------------------------------------
# This script runs in the same subprocess sandbox that the AI agent uses.
# All registered skills are available as top-level functions.
#
# Example: load a CSV-derived GeoJSON and add it to the map.
#
# from pathlib import Path
# import json
#
# points = {
#     "type": "FeatureCollection",
#     "features": [
#         {
#             "type": "Feature",
#             "geometry": {"type": "Point", "coordinates": [116.40, 39.90]},
#             "properties": {"name": "Beijing"},
#         },
#     ],
# }
# layer_id = add_layer_from_geojson(name="demo", geojson=points)
# print("added", layer_id)

print("hello from opengis script runner")
`

export interface ScriptRunnerViewProps {
  /**
   * When provided, this view is bound to a ViewTab from the main
   * editor area — opening a .py file via AssetExplorer lands here.
   * Code buffer lives in the ViewTab (so tab switches preserve it);
   * Save writes to the tab's filePath.
   *
   * When omitted, the view runs standalone (untitled, local state only).
   */
  tab?: ViewTab
}

export function ScriptRunnerView({ tab }: ScriptRunnerViewProps = {}) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const theme = useSettingsStore((s) => s.appearance.theme)
  const updateTabContent = useViewStore((s) => s.updateTabContent)
  const isDarkMode = useMemo(() => {
    if (theme === 'system') {
      return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return theme === 'dark'
  }, [theme])

  // When bound to a tab, the buffer is the tab's content; otherwise local.
  // We hold a local copy for fast typing — flush to the store on change.
  const initialCode = tab?.content ?? DEFAULT_SCRIPT
  const [code, setCode] = useState<string>(initialCode)
  const [filePath, setFilePath] = useState<string | null>(tab?.filePath ?? null)
  /** dirty = code in editor differs from last saved/opened disk content */
  const [isDirty, setIsDirty] = useState(false)
  const lastSavedRef = useRef<string>(initialCode)

  // If the bound tab changes (different file opened, or different tab
  // activated), re-sync our local buffer.
  const tabId = tab?.id
  useEffect(() => {
    if (!tab) return
    const next = tab.content ?? ''
    setCode(next)
    lastSavedRef.current = next
    setFilePath(tab.filePath ?? null)
    setIsDirty(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabId])

  const { status, chunks, result, run, stop, clearOutput } = useScriptRunner()

  // ─── File operations (via Electron IPC) ──────────────────────────
  const electronAPI = typeof window !== 'undefined' ? window.electronAPI : undefined

  const handleOpen = useCallback(async () => {
    if (!electronAPI) return
    const paths = await electronAPI.openFileDialog([
      { name: 'Python', extensions: ['py'] },
      { name: 'All Files', extensions: ['*'] },
    ])
    if (!paths || paths.length === 0) return
    const p = paths[0]
    const res = await electronAPI.readFile(p)
    if (res?.success) {
      setCode(res.content as string)
      lastSavedRef.current = res.content as string
      setFilePath(p)
      setIsDirty(false)
    }
  }, [electronAPI])

  const handleSave = useCallback(async () => {
    if (!electronAPI) return
    let target = filePath
    if (!target) {
      // Save-As. Prefer the workspace as defaultPath so new scripts
      // land in the same place the AI agent persists its own scripts.
      const defaultName = workspacePath
        ? `${workspacePath.replace(/[\\/]+$/, '')}/untitled.py`
        : 'untitled.py'
      target = await electronAPI.saveFileDialog(defaultName)
      if (!target) return
    }
    const res = await electronAPI.writeFile(target, code)
    if (res?.success) {
      setFilePath(target)
      lastSavedRef.current = code
      setIsDirty(false)
    }
  }, [electronAPI, filePath, code, workspacePath])

  // Ctrl+S is handled *inside* the Monaco editor (see handleEditorMount)
  // rather than via a global keydown listener. Global listeners would
  // fire even when the user is typing in the chat box or elsewhere,
  // and would stack up if multiple ScriptRunner tabs are mounted.

  // ─── Monaco wiring ───────────────────────────────────────────────
  const handleEditorMount: OnMount = useCallback((editor, monaco) => {
    // Ctrl+Enter → run. Works even when the editor has focus, which
    // is the usual case.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      // Read the *latest* editor text to avoid stale React-state races.
      const latest = editor.getValue()
      setCode(latest)
      void run(latest, { workspacePath })
    })
    // Ctrl+S inside the editor.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      void handleSave()
    })
    editor.focus()
  }, [run, workspacePath, handleSave])

  const handleChange = useCallback((val: string | undefined) => {
    const next = val ?? ''
    setCode(next)
    setIsDirty(next !== lastSavedRef.current)
    // When this runner is bound to a ViewTab, mirror edits into the
    // store so tab switches don't lose buffered changes.
    if (tabId) {
      updateTabContent(tabId, next)
    }
  }, [tabId, updateTabContent])

  // ─── Run / Stop ──────────────────────────────────────────────────
  const handleRun = useCallback(() => {
    void run(code, { workspacePath })
  }, [run, code, workspacePath])

  // ─── Derived ─────────────────────────────────────────────────────
  // Prefer the tab title (which came from the file node name),
  // fall back to the filePath leaf, finally "untitled.py".
  const title = tab?.title
    ?? (filePath ? (filePath.split(/[\\/]/).pop() || filePath) : 'untitled.py')

  return (
    <div className="flex flex-col h-full bg-bg-primary">
      {/* ─── Toolbar ────────────────────────────────────────────── */}
      <div className="h-9 border-b border-border bg-bg-secondary flex items-center px-2 shrink-0 gap-1">
        <FileCode className="w-3.5 h-3.5 text-yellow-400 shrink-0" />
        <span className="text-xs text-text-secondary truncate max-w-[200px]" title={filePath ?? undefined}>
          {title}
          {isDirty && <span className="text-accent-warning ml-1">●</span>}
        </span>
        <div className="w-px h-4 bg-border mx-2" />

        <ToolbarButton
          icon={<Play className="w-3.5 h-3.5" />}
          label={t.scriptRunner.run}
          accent
          disabled={status === 'running'}
          onClick={handleRun}
          shortcut="Ctrl+Enter"
        />
        <ToolbarButton
          icon={<Square className="w-3.5 h-3.5" />}
          label={t.scriptRunner.stop}
          disabled={status !== 'running'}
          onClick={() => void stop()}
        />

        <div className="w-px h-4 bg-border mx-2" />

        <ToolbarButton
          icon={<Save className="w-3.5 h-3.5" />}
          label={t.scriptRunner.save}
          onClick={() => void handleSave()}
          shortcut="Ctrl+S"
        />
        <ToolbarButton
          icon={<FolderOpen className="w-3.5 h-3.5" />}
          label={t.scriptRunner.open}
          onClick={() => void handleOpen()}
        />

        <div className="flex-1" />

        {/* Status indicator */}
        {status === 'running' && (
          <div className="flex items-center gap-1 text-xs text-accent-warning pr-1">
            <Clock className="w-3 h-3 animate-spin" />
            <span>{t.scriptRunner.running}</span>
          </div>
        )}
        {status === 'finished' && result && (
          <div className="flex items-center gap-1 text-xs pr-1">
            {result.ok ? (
              <CheckCircle2 className="w-3 h-3 text-accent-success" />
            ) : (
              <AlertCircle className="w-3 h-3 text-accent-danger" />
            )}
            <span className="text-text-muted">
              {Math.round(result.duration_ms ?? 0)}ms
            </span>
          </div>
        )}

        <ToolbarButton
          icon={<Trash2 className="w-3.5 h-3.5" />}
          label={t.scriptRunner.clear}
          onClick={clearOutput}
          disabled={chunks.length === 0}
        />
      </div>

      {/* ─── Editor + Output split ──────────────────────────────── */}
      <div className="flex-1 min-h-0">
        <PanelGroup direction="vertical">
          <Panel defaultSize={65} minSize={25}>
            <Editor
              height="100%"
              language="python"
              theme={isDarkMode ? 'vs-dark' : 'light'}
              value={code}
              onChange={handleChange}
              onMount={handleEditorMount}
              options={{
                fontSize: 13,
                fontFamily: 'Menlo, Consolas, "JetBrains Mono", monospace',
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                renderWhitespace: 'selection',
                tabSize: 4,
                insertSpaces: true,
                detectIndentation: true,
                automaticLayout: true,
                lineNumbersMinChars: 3,
                padding: { top: 8 },
                smoothScrolling: true,
                cursorBlinking: 'smooth',
                bracketPairColorization: { enabled: true },
              }}
            />
          </Panel>
          <PanelResizeHandle className="h-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-row-resize" />
          <Panel defaultSize={35} minSize={10}>
            <OutputPanel chunks={chunks} />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  )
}

// ─── Toolbar button ────────────────────────────────────────────────
function ToolbarButton({
  icon,
  label,
  onClick,
  disabled,
  accent,
  shortcut,
}: {
  icon: ReactNode
  label: string
  onClick: () => void
  disabled?: boolean
  accent?: boolean
  shortcut?: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={shortcut ? `${label} (${shortcut})` : label}
      className={`
        flex items-center gap-1 px-2 h-6 rounded text-xs transition-colors
        disabled:opacity-40 disabled:cursor-not-allowed
        ${accent
          ? 'text-accent-success hover:bg-accent-success/10 disabled:hover:bg-transparent'
          : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
        }
      `}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

// ─── Output panel ──────────────────────────────────────────────────
function OutputPanel({ chunks }: { chunks: OutputChunk[] }) {
  const t = useT()
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new chunks — unless the user has scrolled up.
  const stickToBottomRef = useRef(true)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [chunks])

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // Within 20px of bottom → keep sticking.
    stickToBottomRef.current = el.scrollHeight - el.clientHeight - el.scrollTop < 20
  }, [])

  return (
    <div className="h-full flex flex-col bg-bg-tertiary border-t border-border">
      <div className="h-7 border-b border-border flex items-center px-3 shrink-0 text-2xs text-text-muted uppercase tracking-wide font-semibold">
        {t.scriptRunner.output}
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-auto p-3 font-mono text-xs leading-relaxed"
      >
        {chunks.length === 0 ? (
          <span className="text-text-muted italic">{t.scriptRunner.noOutputYet}</span>
        ) : (
          chunks.map((c) => (
            <span
              key={c.id}
              className={chunkClass(c.stream)}
              style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            >
              {c.text}
            </span>
          ))
        )}
      </div>
    </div>
  )
}

function chunkClass(stream: OutputChunk['stream']): string {
  switch (stream) {
    case 'stdout':
      return 'text-text-primary'
    case 'stderr':
      return 'text-accent-warning'
    case 'info':
      return 'text-accent-primary'
    case 'error':
      return 'text-accent-danger'
    default:
      return 'text-text-primary'
  }
}
