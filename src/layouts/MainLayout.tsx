import { useState, useRef, useCallback, useEffect } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { ArrowLeft, Map } from 'lucide-react'
import { useT } from '@/i18n'
import { Sidebar } from './Sidebar'
import { MapView } from '@/features/map/MapView'
import { ChatView } from '@/features/chat/ChatView'
import { DataTable } from '@/features/data/DataTable'
import { SettingsView } from '@/features/settings/SettingsView'
import { LayerPanel } from '@/features/layers/LayerPanel'
import { AssetExplorer } from '@/features/assets/AssetExplorer'
import { CodeViewer, CodeTabHeader } from '@/features/code/CodeViewer'
import { CsvTableView } from '@/features/code/CsvTableView'
import { ImageViewer } from '@/features/code/ImageViewer'
import { ScriptRunnerView } from '@/features/script-runner/ScriptRunnerView'
import { WorkflowsPanel } from '@/features/workflows/WorkflowsPanel'
import { WorkflowEditorView } from '@/features/workflows/WorkflowEditorView'
import { RunsPanel } from '@/features/runs/RunsPanel'
import { ToolAndSkillPanel } from '@/features/tool-catalog/ToolAndSkillPanel'
import { WorkersPanel } from '@/features/workers/WorkersPanel'
import { OperationsPanel } from '@/features/operations/OperationsPanel'
import { OperationEditorView } from '@/features/operations/OperationEditorView'
import { DataPivotPanel } from '@/features/pivot/DataPivotPanel'
import { LayoutComposerView } from '@/features/layout-composer/LayoutComposerView'
import { captureCurrentMapSnapshot } from '@/features/layout-composer/mapSnapshot'
import { useLayoutComposerStore } from '@/features/layout-composer/layoutComposerStore'
import { useMapStore } from '@/stores/mapStore'
import { useViewStore, type ViewTab } from '@/stores/viewStore'
import { mapEngine } from '@/features/map/engine/MapEngine'
import superAppIconImg from '../../resources/icons/super-app-icon.png'

const BOARD_MODE_EXIT_MS = 240

/**
 * Main application layout with resizable panels.
 *
 * ┌─────────┬────────────┬──────────────────────────────┬──────────────────┐
 * │         │  Sidebar   │  Primary Panel               │  Secondary Panel │
 * │  Icons  │  Content   │  (Map / Code tab view)       │  (Chat)          │
 * │  (52px) │  (200px)   │                              │                  │
 * │         │            ├──────────────────────────────┴──────────────────┤
 * │         │            │  Bottom Panel (Data Table)                      │
 * └─────────┴────────────┴────────────────────────────────────────────────┘
 */
export function MainLayout() {
  const [activeSidebarTab, setActiveSidebarTab] = useState<string>('layers')
  const [sidebarContentVisible, setSidebarContentVisible] = useState(true)
  const [showBottomPanel] = useState(false)
  const [showChat, setShowChat] = useState(true)
  const [mapFullscreen, setMapFullscreen] = useState(false)
  const [boardMode, setBoardMode] = useState(false)
  const [boardChatOpen, setBoardChatOpen] = useState(false)
  const [boardClosing, setBoardClosing] = useState(false)
  const previousActiveTabRef = useRef<string | null>(null)
  const boardExitTimerRef = useRef<number | null>(null)

  const isWindows = (window as any).electronAPI?.getPlatform?.() === 'win32'

  const isSettingsView = activeSidebarTab === 'settings'
  const isCanvasView = activeSidebarTab === 'canvas'
  const isWorkersView = activeSidebarTab === 'workers'

  useEffect(() => {
    return () => {
      if (boardExitTimerRef.current != null) {
        window.clearTimeout(boardExitTimerRef.current)
      }
    }
  }, [])

  const enterBoardMode = useCallback(() => {
    if (boardExitTimerRef.current != null) {
      window.clearTimeout(boardExitTimerRef.current)
      boardExitTimerRef.current = null
    }
    setBoardClosing(false)
    setBoardChatOpen(false)
    setBoardMode(true)
    if (!boardMode) {
      previousActiveTabRef.current = useViewStore.getState().activeTabId
      useViewStore.getState().setActiveTab('map')
    }
  }, [boardMode])

  const exitBoardMode = useCallback(() => {
    if (!boardMode || boardClosing) return
    setBoardChatOpen(false)
    setBoardClosing(true)
    boardExitTimerRef.current = window.setTimeout(() => {
      if (previousActiveTabRef.current) {
        useViewStore.getState().setActiveTab(previousActiveTabRef.current)
        previousActiveTabRef.current = null
      }
      setBoardMode(false)
      setBoardClosing(false)
      boardExitTimerRef.current = null
    }, BOARD_MODE_EXIT_MS)
  }, [boardMode, boardClosing])

  const toggleFullscreen = () => setMapFullscreen((v) => !v)
  const toggleBoardMode = () => {
    if (boardMode) exitBoardMode()
    else enterBoardMode()
  }

  const handleSidebarTabChange = useCallback((tab: string) => {
    if (tab === 'canvas') {
      const snapshot = captureCurrentMapSnapshot()
      if (snapshot) useLayoutComposerStore.getState().setMapSnapshotUrl(snapshot)
    }

    if (tab === 'settings') {
      setActiveSidebarTab(tab)
      setSidebarContentVisible(false)
      return
    }

    if (tab === activeSidebarTab) {
      setSidebarContentVisible((visible) => !visible)
      return
    }

    setActiveSidebarTab(tab)
    setSidebarContentVisible(true)
  }, [activeSidebarTab])

  // Fullscreen mode hides chrome, chat, and sidebars, but still keeps the
  // primary tab container alive so image / CSV / code tabs can open.
  if (mapFullscreen) {
    return (
      <div className="h-screen w-screen overflow-hidden">
        <PrimaryPanel onToggleFullscreen={toggleFullscreen} isFullscreen />
      </div>
    )
  }

  // Determine if sidebar content panel should be shown
  const showSidebarContent = sidebarContentVisible && !isSettingsView && !isCanvasView && !isWorkersView && (activeSidebarTab === 'layers' || activeSidebarTab === 'files' || activeSidebarTab === 'tools' || activeSidebarTab === 'workflows' || activeSidebarTab === 'runs' || activeSidebarTab === 'operations')

  return (
    <div className="relative h-screen w-screen overflow-hidden">
    <div
      className={`h-full w-full flex overflow-hidden transition-opacity duration-150 ${
        boardMode ? 'opacity-0 pointer-events-none select-none' : 'opacity-100'
      }`}
      aria-hidden={boardMode}
    >
      {/* Icon Sidebar */}
        <Sidebar
          activeTab={activeSidebarTab}
          isContentVisible={showSidebarContent || isSettingsView || isCanvasView}
        onTabChange={handleSidebarTabChange}
        showChat={showChat}
        onToggleChat={() => setShowChat(!showChat)}
        boardMode={boardMode}
        onToggleBoardMode={toggleBoardMode}
      />

      {/* Sidebar content panel (layers, files, tools, workflows, runs) */}
      {showSidebarContent && (
        <ResizableSidebarPanel activeTab={activeSidebarTab} />
      )}

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Windows frameless title bar drag region */}
        {isWindows && <div className="h-8 shrink-0 app-region-drag" />}
        {isSettingsView ? (
          /* Settings takes full width when active */
          <div className="flex-1 overflow-hidden">
            <SettingsView />
          </div>
        ) : isWorkersView ? (
          <div className="flex-1 overflow-hidden">
            <WorkersPanel
              onOpenScriptTab={() => {
                setActiveSidebarTab('layers')
                setSidebarContentVisible(false)
              }}
            />
          </div>
        ) : (
          <>
            {/* Top panels: Primary (Map/Code) + Chat */}
            <PanelGroup direction="horizontal" className="flex-1">
              {/* Primary panel: Map + Code tab container */}
              <Panel defaultSize={showChat ? 68 : 100} minSize={30}>
                <PrimaryPanel
                  onToggleFullscreen={toggleFullscreen}
                  mode={isCanvasView ? 'canvas' : 'map'}
                />
              </Panel>

              {showChat && (
                <>
                  <PanelResizeHandle className="w-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-col-resize" />

                  {/* Secondary panel: Chat */}
                  <Panel defaultSize={32} minSize={20}>
                    <ChatView />
                  </Panel>
                </>
              )}
            </PanelGroup>

            {/* Bottom panel: Data Table (collapsible) */}
            {showBottomPanel && (
              <>
                <PanelResizeHandle className="h-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-row-resize" />
                <div className="h-[250px] min-h-[150px]">
                  <DataTable />
                </div>
              </>
            )}
          </>
        )}

        {/* Bottom bar */}
        <StatusBar />
      </div>
      <DataPivotPanel />
    </div>
    {boardMode && (
      <BoardModeShell
        chatOpen={boardChatOpen}
        onToggleChat={() => setBoardChatOpen((v) => !v)}
        onExit={exitBoardMode}
        exiting={boardClosing}
      />
    )}
    </div>
  )
}

function BoardModeShell({
  chatOpen,
  onToggleChat,
  onExit,
  exiting,
}: {
  chatOpen: boolean
  onToggleChat: () => void
  onExit: () => void
  exiting: boolean
}) {
  const t = useT()
  const mapHostRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const host = mapHostRef.current
    let originalParent: HTMLElement | null = null
    let originalNextSibling: ChildNode | null = null
    let movedContainer: HTMLElement | null = null

    const resize = () => mapEngine.getMap()?.resize()
    const attachMap = () => {
      if (!host) return false
      const map = mapEngine.getMap()
      const container = map?.getContainer()
      if (!map || !container) return false
      if (container.parentElement === host) return true
      originalParent = container.parentElement
      originalNextSibling = container.nextSibling
      movedContainer = container
      host.appendChild(container)
      resize()
      return true
    }

    const raf = window.requestAnimationFrame(() => {
      attachMap()
      window.setTimeout(resize, 180)
    })
    const retry = window.setTimeout(() => {
      attachMap()
      resize()
    }, 80)
    window.addEventListener('resize', resize)
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(retry)
      window.removeEventListener('resize', resize)
      if (movedContainer && originalParent) {
        originalParent.insertBefore(movedContainer, originalNextSibling)
        window.requestAnimationFrame(resize)
      }
    }
  }, [])

  useEffect(() => {
    const raf = window.requestAnimationFrame(() => mapEngine.getMap()?.resize())
    return () => window.cancelAnimationFrame(raf)
  }, [chatOpen])

  return (
    <div className={`board-mode-shell absolute inset-0 z-[9000] overflow-hidden bg-bg-primary p-3 sm:p-4 ${exiting ? 'board-mode-shell--exit' : ''}`}>
      <div className="board-mode-shell__stage relative h-full w-full overflow-hidden rounded-[22px] bg-bg-secondary shadow-2xl">
        <div ref={mapHostRef} className="board-mode-shell__map h-full w-full" />

        <div className="pointer-events-none absolute inset-0 z-30">
          {chatOpen && (
            <div className="pointer-events-auto absolute bottom-[108px] right-4 h-[min(680px,calc(100vh-148px))] w-[min(440px,calc(100vw-32px))] animate-slide-up">
              <ChatView variant="floating" />
            </div>
          )}

          <div className="pointer-events-auto absolute bottom-4 right-4 flex items-center gap-1.5">
            <button
              type="button"
              onClick={onExit}
              className="relative z-20 flex h-9 w-9 items-center justify-center rounded-full bg-bg-primary/55 text-text-secondary/90 shadow-lg backdrop-blur-xl transition-all duration-200 hover:-translate-x-0.5 hover:scale-105 hover:bg-bg-primary/75 hover:text-accent-primary active:scale-95"
              title={t.sidebar.exitBoard}
              aria-label={t.sidebar.exitBoard}
            >
              <ArrowLeft className="h-4 w-4" strokeWidth={2.4} />
            </button>
            <button
              type="button"
              onClick={onToggleChat}
              className="board-agent-orb group relative flex h-[76px] w-[76px] items-center justify-center rounded-[24px] bg-transparent transition-transform duration-200 hover:scale-[1.035] active:scale-95"
              title={chatOpen ? t.sidebar.hideChat : t.sidebar.showChat}
            >
              <span className="board-agent-orb__halo" aria-hidden />
              <span className="board-agent-orb__glow" aria-hidden />
              <img
                src={superAppIconImg}
                alt="OpenGIS Agent"
                className="relative z-10 h-[62px] w-[62px] rounded-[20px] object-contain drop-shadow-2xl"
              />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Resizable sidebar content panel with drag handle.
 * Min width: 150px, max width: 400px, default: 200px.
 */
function ResizableSidebarPanel({ activeTab }: { activeTab: string }) {
  const [width, setWidth] = useState(200)
  const [isDragging, setIsDragging] = useState(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(0)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)
    dragStartX.current = e.clientX
    dragStartWidth.current = width
  }, [width])

  useEffect(() => {
    if (!isDragging) return
    const handleMouseMove = (e: MouseEvent) => {
      const delta = e.clientX - dragStartX.current
      const newWidth = Math.max(150, Math.min(400, dragStartWidth.current + delta))
      setWidth(newWidth)
    }
    const handleMouseUp = () => setIsDragging(false)
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  return (
    <div className="h-full border-r border-border shrink-0 relative flex" style={{ width }}>
      <div className="flex-1 overflow-hidden">
        <SidebarContent activeTab={activeTab} />
      </div>
      {/* Drag handle */}
      <div
        onMouseDown={handleMouseDown}
        className={`w-[3px] shrink-0 cursor-col-resize transition-colors ${
          isDragging ? 'bg-accent-primary' : 'bg-border hover:bg-accent-primary'
        }`}
      />
    </div>
  )
}

/**
 * Sidebar content panel — renders the appropriate panel based on active tab.
 */
function SidebarContent({ activeTab }: { activeTab: string }) {
  switch (activeTab) {
    case 'layers':
      return <LayerPanel />
    case 'files':
      return <AssetExplorer />
    case 'workflows':
      return <WorkflowsPanel />
    case 'runs':
      return <RunsPanel />
    case 'operations':
      return <OperationsPanel />
    case 'workers':
      return <WorkersPanel />
    case 'tools':
      return <ToolAndSkillPanel />
    default:
      return null
  }
}

/**
 * Status bar at the bottom of the application.
 */
function StatusBar() {
  const t = useT()
  const layers = useMapStore((s) => s.layers)
  const activeLayerId = useMapStore((s) => s.activeLayerId)
  const activeLayer = layers.find((l) => l.id === activeLayerId)

  const crs = activeLayer?.data?.crs || 'EPSG:4326'
  const featureCount = activeLayer?.data?.kind === 'vector' ? activeLayer.data.featureCount : 0

  return (
    <div className="h-6 bg-bg-secondary border-t border-border flex items-center px-3 text-2xs text-text-muted select-none">
      <span className="mr-4">{crs}</span>
      {activeLayer && (
        <>
          <span className="mr-4">{featureCount.toLocaleString()} {t.layout.features}</span>
          <span className="mr-4 text-text-muted/60">|</span>
          <span className="truncate max-w-[200px]">{activeLayer.name}</span>
        </>
      )}
      <div className="flex-1" />
      <span className="mr-3">{layers.length} {t.layout.layers}</span>
      <PythonStatusIndicator />
    </div>
  )
}

/**
 * Python backend status indicator in the status bar.
 */
function PythonStatusIndicator() {
  const statusColors = {
    ready: 'bg-accent-success',
    starting: 'bg-accent-warning animate-pulse-soft',
    error: 'bg-accent-danger',
    stopped: 'bg-text-muted',
  }

  const [status, setStatus] = useState<'stopped' | 'starting' | 'ready' | 'error'>('stopped')

  useEffect(() => {
    const api = window.electronAPI
    if (!api) return

    // Get initial status
    api.getPythonStatus().then((s: any) => {
      if (s?.status) setStatus(s.status)
    }).catch(() => {})

    // Subscribe to status changes
    const unsubscribe = api.onPythonStatusChanged((s: any) => {
      if (s?.status) setStatus(s.status)
    })
    return unsubscribe
  }, [])

  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-1.5 h-1.5 rounded-full ${statusColors[status]}`} />
      <span>Python: {status}</span>
    </div>
  )
}

/**
 * Primary panel — contains the Map/Code tab bar and content area.
 *
 * When code tabs are open, displays a tab bar allowing the user to
 * switch between Map view and Code viewer. Supports split view
 * (map + code side by side or top/bottom).
 */
function PrimaryPanel({
  onToggleFullscreen,
  isFullscreen = false,
  mode = 'map',
}: {
  onToggleFullscreen: () => void
  isFullscreen?: boolean
  mode?: 'map' | 'canvas'
}) {
  const t = useT()
  const tabs = useViewStore((s) => s.tabs)
  const activeTabId = useViewStore((s) => s.activeTabId)
  const setActiveTab = useViewStore((s) => s.setActiveTab)
  const closeTab = useViewStore((s) => s.closeTab)

  const viewTabs = tabs.filter((t) => t.type === 'code' || t.type === 'text' || t.type === 'image')
  const activeViewTab = tabs.find((t) => t.id === activeTabId)

  if (mode === 'canvas') {
    return <LayoutComposerView />
  }

  // No file/image tabs — just show the map
  if (viewTabs.length === 0) {
    return <MapView onToggleFullscreen={onToggleFullscreen} isFullscreen={isFullscreen} />
  }

  // Tab view: switch between map and code
  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="h-9 border-b border-border bg-bg-secondary flex items-center shrink-0">
        {/* Map tab */}
        <button
          onClick={() => setActiveTab('map')}
          className={`
            flex items-center gap-1.5 px-3 h-full border-r border-border shrink-0 transition-colors
            ${activeTabId === 'map'
              ? 'bg-bg-primary text-text-primary'
              : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
            }
          `}
        >
          <Map className="w-3.5 h-3.5" />
          <span className="text-xs">{t.layout.map}</span>
        </button>

        {/* Code tabs */}
        <CodeTabHeader
          tabs={viewTabs}
          activeTabId={activeTabId}
          onTabClick={setActiveTab}
          onTabClose={closeTab}
        />
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTabId === 'map' ? (
          <MapView onToggleFullscreen={onToggleFullscreen} isFullscreen={isFullscreen} />
        ) : activeViewTab ? (
          <CodeTabContent tab={activeViewTab} />
        ) : (
          <MapView onToggleFullscreen={onToggleFullscreen} isFullscreen={isFullscreen} />
        )}
      </div>
    </div>
  )
}

/**
 * Pick the right view for a code/text tab.
 *
 * Python files get the full Script Runner (Monaco + Run + stdout) so the
 * user can edit and execute the same file they just double-clicked in
 * the Asset Explorer. CSV / TSV files get the spreadsheet-style grid
 * viewer. Everything else (including GeoJSON) falls back to the
 * read-only ``CodeViewer`` (syntax-highlighted, with execution-result
 * rendering) — GeoJSON is just JSON under the hood and benefits from
 * syntax highlighting more than a bespoke viewer.
 */
function CodeTabContent({ tab }: { tab: ViewTab }) {
  const lang = (tab.language || '').toLowerCase()
  const path = tab.filePath?.toLowerCase() ?? ''

  // Workflows are a distinct tab type — they edit a *.flow.json via a
  // visual canvas, never as raw code. Matched by language tag to avoid
  // accidentally routing here if the user names a regular file
  // ".flow.json" without going through the Workflows sidebar.
  if (lang === 'workflow') {
    return <WorkflowEditorView tab={tab} />
  }

  if (lang === 'operation') {
    return <OperationEditorView tab={tab} />
  }

  const isPython = lang === 'python' || path.endsWith('.py')
  if (isPython) {
    return <ScriptRunnerView tab={tab} />
  }

  const isCsv = lang === 'csv' || lang === 'tsv'
    || path.endsWith('.csv') || path.endsWith('.tsv')
  if (isCsv) {
    return <CsvTableView tab={tab} />
  }

  const isImage = lang === 'image'
    || /\.(png|jpe?g|gif|bmp|webp|svg)$/i.test(path)
  if (isImage) {
    return <ImageViewer tab={tab} />
  }

  return <CodeViewer tab={tab} />
}
