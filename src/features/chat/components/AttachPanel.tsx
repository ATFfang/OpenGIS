import { useEffect, useRef } from 'react'
import { Database, FolderOpen, GitBranch, Globe, Wrench } from 'lucide-react'
import { useWorkflowStore } from '@/stores/workflowStore'
import { useT } from '@/i18n'

interface AttachPanelProps {
  onAttachFile: () => void
  onAttachWorkflow: (entry: { path: string; name: string }) => void
  onAttachToolGroup: (name: string, groups: string[]) => void
  attachedToolGroups: string[]
  onClose: () => void
}

export function AttachPanel({
  onAttachFile,
  onAttachWorkflow,
  onAttachToolGroup,
  attachedToolGroups,
  onClose,
}: AttachPanelProps) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const workflowEntries = useWorkflowStore((s) => s.entries)
  const refreshWorkflows = useWorkflowStore((s) => s.refresh)

  useEffect(() => {
    refreshWorkflows()
  }, [refreshWorkflows])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 right-0 mb-2 bg-bg-secondary border border-border rounded-xl shadow-xl z-[999] animate-fade-in overflow-hidden"
    >
      <div className="p-2">
        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5">
          {t.chat.attachFile}
        </div>

        <button
          onClick={onAttachFile}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-all duration-150 group"
        >
          <div className="w-7 h-7 rounded-lg bg-blue-500/10 flex items-center justify-center shrink-0 ring-1 ring-blue-500/20">
            <FolderOpen className="w-3.5 h-3.5 text-blue-400" />
          </div>
          <div className="text-left">
            <p className="text-[12px] font-medium leading-tight">{t.chat.browseFiles}</p>
            <p className="text-[10px] text-text-muted mt-0.5">
              {t.fileBrowser.selectFiles}
            </p>
          </div>
        </button>

        {workflowEntries.length > 0 && (
          <>
            <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5 mt-1 flex items-center gap-1">
              <GitBranch className="w-3 h-3" />
              {t.chat.attachWorkflow}
            </div>
            <div className="max-h-32 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
              {workflowEntries.map((entry) => (
                <button
                  key={entry.path}
                  onClick={() => onAttachWorkflow(entry)}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-all duration-150"
                >
                  <div className="w-6 h-6 rounded-md bg-purple-500/10 flex items-center justify-center shrink-0 ring-1 ring-purple-500/20">
                    <GitBranch className="w-3 h-3 text-purple-400" />
                  </div>
                  <div className="text-left flex-1 min-w-0">
                    <p className="text-[12px] font-medium leading-tight truncate">
                      {entry.name}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5 mt-1 flex items-center gap-1">
          <Wrench className="w-3 h-3" />
          {t.chat.attachTools}
        </div>
        <ToolGroupButton
          name="QGIS4+"
          active={attachedToolGroups.includes('QGIS4+')}
          icon={<Wrench className="w-3 h-3 text-amber-400" />}
          activeClass="bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30"
          inactiveIconClass="bg-amber-500/10 ring-amber-500/20"
          activeIconClass="bg-amber-500/20 ring-amber-500/30"
          description={attachedToolGroups.includes('QGIS4+') ? t.chat.attachedClickDetach : t.chat.qgisMcpCommands}
          onClick={() => onAttachToolGroup('QGIS4+', ['qgis'])}
        />
        <ToolGroupButton
          name="OSM"
          active={attachedToolGroups.includes('OSM')}
          icon={<Globe className="w-3 h-3 text-green-400" />}
          activeClass="bg-green-500/10 text-green-300 ring-1 ring-green-500/30"
          inactiveIconClass="bg-green-500/10 ring-green-500/20"
          activeIconClass="bg-green-500/20 ring-green-500/30"
          description={attachedToolGroups.includes('OSM') ? t.chat.attachedClickDetach : t.chat.osmDataDownload}
          onClick={() => onAttachToolGroup('OSM', ['osm'])}
        />
        <ToolGroupButton
          name={t.chat.dataSources}
          active={attachedToolGroups.includes('DataSources')}
          icon={<Database className="w-3 h-3 text-cyan-400" />}
          activeClass="bg-cyan-500/10 text-cyan-300 ring-1 ring-cyan-500/30"
          inactiveIconClass="bg-cyan-500/10 ring-cyan-500/20"
          activeIconClass="bg-cyan-500/20 ring-cyan-500/30"
          description={attachedToolGroups.includes('DataSources') ? t.chat.attachedClickDetach : t.chat.dataSourcesGuide}
          onClick={() => onAttachToolGroup('DataSources', ['datasource'])}
        />

        <div className="px-2 pt-2 pb-1">
          <p className="text-[10px] text-text-muted/60 leading-relaxed">
            💡 {t.chat.attachWorkflowHint}
          </p>
        </div>
      </div>
    </div>
  )
}

function ToolGroupButton({
  name,
  active,
  icon,
  activeClass,
  inactiveIconClass,
  activeIconClass,
  description,
  onClick,
}: {
  name: string
  active: boolean
  icon: React.ReactNode
  activeClass: string
  inactiveIconClass: string
  activeIconClass: string
  description: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-150 ${
        active ? activeClass : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
      }`}
    >
      <div className={`w-6 h-6 rounded-md flex items-center justify-center shrink-0 ring-1 ${
        active ? activeIconClass : inactiveIconClass
      }`}>
        {icon}
      </div>
      <div className="text-left flex-1 min-w-0">
        <p className="text-[12px] font-medium leading-tight">{name}</p>
        <p className="text-[10px] text-text-muted mt-0.5">{description}</p>
      </div>
    </button>
  )
}
