import {
  MessageSquare,
  Layers,
  Wrench,
  Settings,
  FolderOpen,
  GitBranch,
  ListRestart,
  FolderSync,
  LayoutTemplate,
  Monitor,
  Activity,
} from 'lucide-react'
import { useT } from '@/i18n'

interface SidebarProps {
  activeTab: string
  isContentVisible?: boolean
  onTabChange: (tab: string) => void
  showChat: boolean
  onToggleChat: () => void
  boardMode: boolean
  onToggleBoardMode: () => void
}

/**
 * Left sidebar with icon-based navigation tabs.
 * Compact mode: 52px wide, icon only.
 */
export function Sidebar({
  activeTab,
  isContentVisible = true,
  onTabChange,
  showChat,
  onToggleChat,
  boardMode,
  onToggleBoardMode,
}: SidebarProps) {
  const t = useT()

  const sidebarTabs = [
    { id: 'files', icon: FolderOpen, label: t.sidebar.files },
    { id: 'layers', icon: Layers, label: t.sidebar.layers },
    { id: 'canvas', icon: LayoutTemplate, label: t.sidebar.canvas },
    { id: 'workers', icon: Activity, label: t.sidebar.workers },
    { id: 'workflows', icon: GitBranch, label: t.sidebar.workflows },
    { id: 'runs', icon: ListRestart, label: t.sidebar.runs },
    { id: 'tools', icon: Wrench, label: t.sidebar.tools },
    { id: 'settings', icon: Settings, label: t.sidebar.settings },
  ]

  return (
    <div className="w-[52px] h-full bg-bg-sidebar border-r border-border flex flex-col items-center py-2 select-none">
      {/* Navigation tabs */}
      <div className="flex flex-col gap-1 flex-1">
        {sidebarTabs.map((tab) => {
          const isActive = activeTab === tab.id && (tab.id === 'settings' || isContentVisible)
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`
                w-10 h-10 rounded-lg flex items-center justify-center
                transition-all duration-150 group relative
                ${isActive
                  ? 'bg-accent-primary/15 text-accent-primary'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'
                }
              `}
              title={tab.label}
            >
              <tab.icon className="w-[18px] h-[18px]" strokeWidth={isActive ? 2.2 : 1.8} />

              {/* Active indicator */}
              {isActive && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-accent-primary rounded-r-full" />
              )}

              {/* Tooltip */}
              <div className="absolute left-full ml-2 px-2 py-1 bg-bg-tertiary text-text-primary text-xs rounded-md opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50 shadow-lg border border-border">
                {tab.label}
              </div>
            </button>
          )
        })}
      </div>

      {/* Bottom: Switch Project + Chat shortcut */}
      <button
        onClick={() => window.electronAPI?.switchProject?.()}
        className="w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-150 mb-1 text-text-muted hover:text-accent-primary hover:bg-accent-primary/10"
        title={t.sidebar.switchProject}
      >
        <FolderSync className="w-[18px] h-[18px]" strokeWidth={1.8} />
      </button>
      <button
        onClick={onToggleBoardMode}
        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-150 mb-1 ${
          boardMode
            ? 'text-accent-primary bg-accent-primary/10'
            : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
        }`}
        title={boardMode ? t.sidebar.exitBoard : t.sidebar.board}
      >
        <Monitor className="w-[18px] h-[18px]" strokeWidth={boardMode ? 2.2 : 1.8} />
      </button>
      <button
        onClick={onToggleChat}
        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-150 mb-2 ${
          showChat
            ? 'text-accent-primary bg-accent-primary/10'
            : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
        }`}
        title={showChat ? t.sidebar.hideChat : t.sidebar.showChat}
      >
        <MessageSquare className="w-[18px] h-[18px]" strokeWidth={showChat ? 2.2 : 1.8} />
      </button>
    </div>
  )
}
