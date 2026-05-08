import { useState } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { Map, Code2, SplitSquareHorizontal, SplitSquareVertical, X } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { MapView } from '@/features/map/MapView'
import { ChatView } from '@/features/chat/ChatView'
import { DataTable } from '@/features/data/DataTable'
import { SettingsView } from '@/features/settings/SettingsView'
import { LayerPanel } from '@/features/layers/LayerPanel'
import { AssetExplorer } from '@/features/assets/AssetExplorer'
import { CodeViewer, CodeTabHeader } from '@/features/code/CodeViewer'
import { CsvTableView } from '@/features/code/CsvTableView'
import { ScriptRunnerView } from '@/features/script-runner/ScriptRunnerView'
import { WorkflowsPanel } from '@/features/workflows/WorkflowsPanel'
import { WorkflowEditorView } from '@/features/workflows/WorkflowEditorView'
import { RunsPanel } from '@/features/runs/RunsPanel'
import { SkillsPanel } from '@/features/skills/SkillsPanel'
import { useMapStore } from '@/stores/mapStore'
import { useViewStore, type ViewTab } from '@/stores/viewStore'

/**
 * Main application layout with resizable panels.
 *
 * ┌─────────┬────────────┬──────────────────────────────┬──────────────────┐
 * │         │  Sidebar   │  Primary Panel               │  Secondary Panel │
 * │  Icons  │  Content   │  (Map / Code tab view)       │  (Chat)          │
 * │  (52px) │  (240px)   │                              │                  │
 * │         │            ├──────────────────────────────┴──────────────────┤
 * │         │            │  Bottom Panel (Data Table)                      │
 * └─────────┴────────────┴────────────────────────────────────────────────┘
 */
export function MainLayout() {
  const [activeSidebarTab, setActiveSidebarTab] = useState<string>('layers')
  const [showBottomPanel, setShowBottomPanel] = useState(false)
  const [showChat, setShowChat] = useState(true)

  const isSettingsView = activeSidebarTab === 'settings'

  // Determine if sidebar content panel should be shown
  const showSidebarContent = !isSettingsView && (activeSidebarTab === 'layers' || activeSidebarTab === 'files' || activeSidebarTab === 'skills' || activeSidebarTab === 'workflows' || activeSidebarTab === 'runs')

  return (
    <div className="h-screen w-screen flex overflow-hidden">
      {/* Icon Sidebar */}
      <Sidebar
        activeTab={activeSidebarTab}
        onTabChange={setActiveSidebarTab}
        showChat={showChat}
        onToggleChat={() => setShowChat(!showChat)}
      />

      {/* Sidebar Content Panel (Layer Panel, Files, Skills) */}
      {showSidebarContent && (
        <div className="w-[240px] h-full border-r border-border shrink-0 relative">
          <SidebarContent activeTab={activeSidebarTab} />
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        {isSettingsView ? (
          /* Settings takes full width when active */
          <div className="flex-1 overflow-hidden">
            <SettingsView />
          </div>
        ) : (
          <>
            {/* Top panels: Primary (Map/Code) + Chat */}
            <PanelGroup direction="horizontal" className="flex-1">
              {/* Primary panel: Map + Code tab container */}
              <Panel defaultSize={showChat ? 60 : 100} minSize={30}>
                <PrimaryPanel />
              </Panel>

              {showChat && (
                <>
                  <PanelResizeHandle className="w-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-col-resize" />

                  {/* Secondary panel: Chat */}
                  <Panel defaultSize={40} minSize={25}>
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
        <StatusBar
          showBottomPanel={showBottomPanel}
          onToggleBottomPanel={() => setShowBottomPanel(!showBottomPanel)}
        />
      </div>
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
    case 'skills':
      return <SkillsPanel />
    default:
      return null
  }
}

/**
 * Status bar at the bottom of the application.
 */
function StatusBar({
  showBottomPanel,
  onToggleBottomPanel,
}: {
  showBottomPanel: boolean
  onToggleBottomPanel: () => void
}) {
  const layers = useMapStore((s) => s.layers)
  const activeLayerId = useMapStore((s) => s.activeLayerId)
  const activeLayer = layers.find((l) => l.id === activeLayerId)

  const crs = activeLayer?.data?.crs || 'EPSG:4326'
  const featureCount = activeLayer?.data?.kind === 'vector' ? activeLayer.data.featureCount : 0

  return (
    <div className="h-6 bg-bg-secondary border-t border-border flex items-center px-3 text-2xs text-text-muted select-none">
      <button
        onClick={onToggleBottomPanel}
        className="hover:text-text-primary transition-colors mr-4"
      >
        {showBottomPanel ? '▼ Hide Table' : '▲ Show Table'}
      </button>
      <span className="mr-4">{crs}</span>
      {activeLayer && (
        <>
          <span className="mr-4">{featureCount.toLocaleString()} features</span>
          <span className="mr-4 text-text-muted/60">|</span>
          <span className="truncate max-w-[200px]">{activeLayer.name}</span>
        </>
      )}
      <div className="flex-1" />
      <span className="mr-3">{layers.length} layer{layers.length !== 1 ? 's' : ''}</span>
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

  const status = 'stopped' // Placeholder

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
function PrimaryPanel() {
  const tabs = useViewStore((s) => s.tabs)
  const activeTabId = useViewStore((s) => s.activeTabId)
  const setActiveTab = useViewStore((s) => s.setActiveTab)
  const closeTab = useViewStore((s) => s.closeTab)
  const showSplitView = useViewStore((s) => s.showSplitView)
  const toggleSplitView = useViewStore((s) => s.toggleSplitView)
  const splitDirection = useViewStore((s) => s.splitDirection)
  const setSplitDirection = useViewStore((s) => s.setSplitDirection)

  const codeTabs = tabs.filter((t) => t.type === 'code' || t.type === 'text')
  const activeCodeTab = tabs.find((t) => t.id === activeTabId)
  const isViewingCode = activeCodeTab && activeTabId !== 'map'

  // No code tabs — just show the map
  if (codeTabs.length === 0) {
    return <MapView />
  }

  // Split view: map + code side by side or top/bottom
  if (showSplitView && isViewingCode) {
    return (
      <PanelGroup direction={splitDirection} className="h-full">
        <Panel defaultSize={50} minSize={20}>
          <MapView />
        </Panel>
        <PanelResizeHandle
          className={
            splitDirection === 'horizontal'
              ? 'w-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-col-resize'
              : 'h-[3px] bg-border hover:bg-accent-primary transition-colors duration-150 cursor-row-resize'
          }
        />
        <Panel defaultSize={50} minSize={20}>
          {activeCodeTab && <CodeTabContent tab={activeCodeTab} />}
        </Panel>
      </PanelGroup>
    )
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
          <span className="text-xs">Map</span>
        </button>

        {/* Code tabs */}
        <CodeTabHeader
          tabs={codeTabs}
          activeTabId={activeTabId}
          onTabClick={setActiveTab}
          onTabClose={closeTab}
        />

        <div className="flex-1" />

        {/* Split view controls */}
        {isViewingCode && (
          <div className="flex items-center gap-0.5 px-2">
            <button
              onClick={() => {
                setSplitDirection('horizontal')
                if (!showSplitView) toggleSplitView()
              }}
              className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${
                showSplitView && splitDirection === 'horizontal'
                  ? 'text-accent-primary bg-accent-primary/10'
                  : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
              }`}
              title="Split left/right"
            >
              <SplitSquareHorizontal className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => {
                setSplitDirection('vertical')
                if (!showSplitView) toggleSplitView()
              }}
              className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${
                showSplitView && splitDirection === 'vertical'
                  ? 'text-accent-primary bg-accent-primary/10'
                  : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
              }`}
              title="Split top/bottom"
            >
              <SplitSquareVertical className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTabId === 'map' ? (
          <MapView />
        ) : activeCodeTab ? (
          <CodeTabContent tab={activeCodeTab} />
        ) : (
          <MapView />
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

  const isPython = lang === 'python' || path.endsWith('.py')
  if (isPython) {
    return <ScriptRunnerView tab={tab} />
  }

  const isCsv = lang === 'csv' || lang === 'tsv'
    || path.endsWith('.csv') || path.endsWith('.tsv')
  if (isCsv) {
    return <CsvTableView tab={tab} />
  }

  return <CodeViewer tab={tab} />
}
