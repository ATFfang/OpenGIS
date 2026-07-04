/**
 * WorkflowsPanel — sidebar content panel for the Workflows tab.
 *
 * Shows the list of `*.flow.json` files in `<workspace>/workflows/`.
 * Clicking a row opens the workflow as a tab (handled by viewStore);
 * the "+ New" button creates a fresh .flow.json and opens it.
 *
 * Design note: this panel mirrors the visual language of the Layers
 * and Assets panels so the left sidebar feels cohesive — 36px header
 * row, small secondary-text entries, hover row highlight, row-level
 * context menu for rename/delete.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  GitBranch,
  Plus,
  RefreshCw,
  Pencil,
  Trash2,
  FolderPlus,
  AlertCircle,
} from 'lucide-react'
import { useAssetStore } from '@/stores/assetStore'
import {
  useWorkflowStore,
  type WorkflowIndexEntry,
} from '@/stores/workflowStore'
import { useViewStore } from '@/stores/viewStore'
import { useDialog } from '@/components/Dialog'
import { useT } from '@/i18n'

// ─── Main panel ───────────────────────────────────────────────────

export function WorkflowsPanel() {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const entries = useWorkflowStore((s) => s.entries)
  const isLoading = useWorkflowStore((s) => s.isLoading)
  const error = useWorkflowStore((s) => s.error)
  const loaded = useWorkflowStore((s) => s.loaded)
  const refresh = useWorkflowStore((s) => s.refresh)
  const createWorkflow = useWorkflowStore((s) => s.createWorkflow)
  const { prompt } = useDialog()

  // First mount: populate the list from disk. The zustand subscribe
  // hook in workflowStore handles workspace *changes*, but not the
  // initial load when the panel is opened after the workspace was
  // already set (e.g. rehydrated from persisted asset store).
  useEffect(() => {
    if (workspacePath) {
      refresh().catch(() => { /* error lives in store.error */ })
    }
  }, [workspacePath, refresh])

  const handleCreate = useCallback(async () => {
    // Open the in-app prompt dialog. `window.prompt` is not
    // supported in Electron renderers, so we route through the
    // DialogHost singleton mounted at App root.
    const raw = await prompt({
      title: t.workflow.panel.newWorkflow,
      message: t.workflow.panel.newWorkflowPrompt,
      defaultValue: t.workflow.panel.newWorkflowDefault,
      placeholder: t.workflow.panel.newWorkflowPlaceholder,
      okLabel: t.common.create,
    })
    if (raw == null) return
    const path = await createWorkflow(raw)
    if (path) {
      // Open the freshly-created workflow as a tab.
      openWorkflowTab(path, deriveName(path))
    }
  }, [createWorkflow, prompt])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-1">
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          {t.workflow.title}
        </span>

        <button
          onClick={() => workspacePath && refresh()}
          disabled={!workspacePath}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-text-muted"
          title={t.workflow.panel.refreshList}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
        </button>

        <button
          onClick={handleCreate}
          disabled={!workspacePath}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-text-muted"
          title={t.workflow.panel.newWorkflow}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {!workspacePath ? (
          <NoWorkspaceState />
        ) : error ? (
          <ErrorRow message={error} onRetry={() => refresh()} />
        ) : entries.length === 0 ? (
          isLoading ? <LoadingState /> : <EmptyState onCreate={handleCreate} />
        ) : (
          <div className="py-1">
            {entries.map((entry) => (
              <WorkflowRow
                key={entry.path}
                entry={entry}
                isDirty={!!loaded[entry.path]?.dirty}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Row ──────────────────────────────────────────────────────────

interface WorkflowRowProps {
  entry: WorkflowIndexEntry
  isDirty: boolean
}

function WorkflowRow({ entry, isDirty }: WorkflowRowProps) {
  const t = useT()
  const activeTabId = useViewStore((s) => s.activeTabId)
  const tabs = useViewStore((s) => s.tabs)
  const renameWorkflow = useWorkflowStore((s) => s.renameWorkflow)
  const deleteWorkflow = useWorkflowStore((s) => s.deleteWorkflow)
  const { prompt, confirm } = useDialog()

  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)

  // Highlight the row when its workflow is the active tab.
  const openTab = tabs.find((t) => t.filePath === entry.path)
  const isActive = openTab && openTab.id === activeTabId

  const handleClick = useCallback(() => {
    openWorkflowTab(entry.path, entry.name)
  }, [entry.path, entry.name])

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY })
  }, [])

  const handleRename = useCallback(async () => {
    setContextMenu(null)
    const raw = await prompt({
      title: t.workflow.panel.renameWorkflow,
      defaultValue: entry.name,
      okLabel: t.common.rename,
    })
    if (raw == null || raw === entry.name) return
    const newPath = await renameWorkflow(entry.path, raw)
    if (newPath) {
      // If the workflow was open as a tab, rewire it to the new path.
      // viewStore doesn't have a "change filePath" action, but closing
      // and reopening is cheap and preserves the UX.
      const view = useViewStore.getState()
      const tab = view.tabs.find((t) => t.filePath === entry.path)
      if (tab) {
        view.closeTab(tab.id)
        openWorkflowTab(newPath, deriveName(newPath))
      }
    }
  }, [entry.path, entry.name, renameWorkflow, prompt])

  const handleDelete = useCallback(async () => {
    setContextMenu(null)
    const ok = await confirm({
      title: t.workflow.panel.deleteWorkflow,
      message: t.workflow.panel.deleteConfirm.replace('{name}', entry.name),
      okLabel: t.common.delete,
      danger: true,
    })
    if (!ok) return
    const removed = await deleteWorkflow(entry.path)
    if (removed) {
      // Close the tab if it's open.
      const view = useViewStore.getState()
      const tab = view.tabs.find((t) => t.filePath === entry.path)
      if (tab) view.closeTab(tab.id)
    }
  }, [entry.path, entry.name, deleteWorkflow, confirm])

  return (
    <>
      <div
        onClick={handleClick}
        onContextMenu={handleContextMenu}
        className={`
          group flex items-center gap-2 px-3 py-1.5 cursor-pointer transition-colors
          ${isActive
            ? 'bg-accent-primary/12 text-text-primary'
            : 'hover:bg-bg-hover text-text-secondary'}
        `}
        title={entry.path}
      >
        <GitBranch className="w-3.5 h-3.5 shrink-0 text-accent-geo" />
        <span className="text-xs truncate flex-1">{entry.name}</span>
        {isDirty && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-accent-warning shrink-0"
            title={t.workflow.panel.unsavedChanges}
          />
        )}
      </div>

      {contextMenu && (
        <WorkflowContextMenu
          position={contextMenu}
          onClose={() => setContextMenu(null)}
          onRename={handleRename}
          onDelete={handleDelete}
        />
      )}
    </>
  )
}

// ─── Context menu ─────────────────────────────────────────────────

interface WorkflowContextMenuProps {
  position: { x: number; y: number }
  onClose: () => void
  onRename: () => void
  onDelete: () => void
}

function WorkflowContextMenu({
  position,
  onClose,
  onRename,
  onDelete,
}: WorkflowContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  return (
    <div
      ref={menuRef}
      className="bg-bg-secondary border border-border rounded-lg shadow-xl py-1 min-w-[160px] animate-fade-in"
      style={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        zIndex: 9999,
      }}
    >
      <MenuItem
        icon={<Pencil className="w-3.5 h-3.5" />}
        label="Rename"
        onClick={onRename}
      />
      <div className="h-px bg-border mx-2 my-1" />
      <MenuItem
        icon={<Trash2 className="w-3.5 h-3.5" />}
        label="Delete"
        onClick={onDelete}
        danger
      />
    </div>
  )
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={`
        w-full flex items-center gap-2.5 px-3 py-1.5 text-xs transition-colors
        ${danger
          ? 'text-text-secondary hover:text-accent-danger hover:bg-accent-danger/10'
          : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'}
      `}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

// ─── Empty / loading / error states ──────────────────────────────

function NoWorkspaceState() {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-primary/10 flex items-center justify-center mx-auto mb-3">
          <FolderPlus className="w-5 h-5 text-accent-primary/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">{t.workflow.panel.noWorkspace}</p>
        <p className="text-2xs text-text-muted/60 max-w-[180px]">
          {t.workflow.panel.noWorkspaceHint}
        </p>
      </div>
    </div>
  )
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-geo/10 flex items-center justify-center mx-auto mb-3">
          <GitBranch className="w-5 h-5 text-accent-geo/60" />
        </div>
        <p className="text-xs text-text-muted mb-1">{t.workflow.panel.noWorkflows}</p>
        <p className="text-2xs text-text-muted/60 mb-3 max-w-[180px]">
          {t.workflow.panel.noWorkflowsHint}
        </p>
        <button
          onClick={onCreate}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors flex items-center gap-1 mx-auto"
        >
          <Plus className="w-3 h-3" />
          {t.workflow.panel.newWorkflow}
        </button>
      </div>
    </div>
  )
}

function LoadingState() {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <RefreshCw className="w-5 h-5 text-text-muted animate-spin mx-auto mb-2" />
        <p className="text-xs text-text-muted">{t.workflow.panel.loadingWorkflows}</p>
      </div>
    </div>
  )
}

function ErrorRow({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-danger/10 flex items-center justify-center mx-auto mb-3">
          <AlertCircle className="w-5 h-5 text-accent-danger/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">Failed to load</p>
        <p className="text-2xs text-text-muted/60 mb-2 max-w-[180px] truncate">
          {message}
        </p>
        <button
          onClick={onRetry}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors"
        >
          Retry
        </button>
      </div>
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────

function deriveName(path: string): string {
  const base = path.split(/[\\/]/).pop() || path
  // Strip .flow.json
  return base.replace(/\.flow\.json$/i, '')
}

/**
 * Open a workflow as a tab in the primary panel.
 *
 * Exported side-effect-y function rather than a hook because it's
 * called from the panel row and from the context menu (after rename
 * or create). All it does is forward to viewStore.openTab with the
 * right discriminator — the tab renderer in MainLayout recognises
 * `language: 'workflow'` and mounts WorkflowEditorView.
 */
function openWorkflowTab(path: string, name: string) {
  const view = useViewStore.getState()

  // Already open? Just focus.
  const existing = view.tabs.find((t) => t.filePath === path)
  if (existing) {
    view.setActiveTab(existing.id)
    return
  }

  view.openTab({
    title: `${name}${nameSuffix()}`,
    type: 'code',
    filePath: path,
    language: 'workflow',
  })
}

/**
 * Tiny helper to avoid title collisions with a .py file of the same
 * name — we append an invisible marker-less suffix (nothing by
 * default) but leave the hook in case we want a visual tag later
 * (e.g. " · flow"). Keeping it as a separate function so any future
 * change lives in one place.
 */
function nameSuffix(): string {
  return ''
}
