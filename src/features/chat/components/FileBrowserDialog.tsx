/**
 * FileBrowserDialog — A standalone file browser dialog for selecting files
 * to attach to chat messages.
 *
 * Features:
 * - Browse workspace directory tree
 * - Navigate into subdirectories / go back
 * - Multi-select files with checkboxes
 * - File type icons and size display
 * - Search/filter within current directory
 * - Keyboard shortcuts (Escape to close, Enter to confirm)
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  X,
  FolderOpen,
  FolderClosed,
  File as FileIcon,
  FileText,
  ChevronLeft,
  ChevronRight,
  Check,
  Search,
  Home,
  HardDrive,
  Image,
  Database,
  Globe,
  Table,
  FileCode,
  Loader2,
  GitBranch,
} from 'lucide-react'
import { useAssetStore } from '@/stores/assetStore'

// ─── Types ──────────────────────────────────────────────────────

interface FileEntry {
  path: string
  name: string
  isDirectory: boolean
  extension: string
  size: number
  modifiedTime: string
}

export interface FileBrowserResult {
  name: string
  path: string
}

interface FileBrowserDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: (files: FileBrowserResult[]) => void
  /** Initial directory to browse (defaults to workspace path) */
  initialPath?: string
  /** Allow selecting multiple files */
  multiple?: boolean
  /** Title shown in the dialog header */
  title?: string
}

// ─── File extension icon mapping ────────────────────────────────

const EXT_ICONS: Record<string, typeof FileIcon> = {
  '.geojson': Globe,
  '.json': FileCode,
  '.shp': Globe,
  '.gpkg': Database,
  '.kml': Globe,
  '.tif': Image,
  '.tiff': Image,
  '.png': Image,
  '.jpg': Image,
  '.jpeg': Image,
  '.csv': Table,
  '.xlsx': Table,
  '.xls': Table,
  '.py': FileCode,
  '.js': FileCode,
  '.ts': FileCode,
  '.md': FileText,
  '.txt': FileText,
}

function getFileIcon(entry: FileEntry) {
  if (entry.isDirectory) return FolderClosed
  // Workflow files get a special icon
  if (entry.name.endsWith('.flow.json')) return GitBranch
  const ext = entry.extension.toLowerCase()
  return EXT_ICONS[ext] || FileIcon
}

function getIconColor(entry: FileEntry): string {
  if (entry.isDirectory) return 'text-amber-400'
  if (entry.name.endsWith('.flow.json')) return 'text-accent-geo'
  const ext = entry.extension.toLowerCase()
  if (['.geojson', '.shp', '.kml', '.gpkg', '.gml'].includes(ext)) return 'text-emerald-400'
  if (['.tif', '.tiff', '.png', '.jpg', '.jpeg'].includes(ext)) return 'text-purple-400'
  if (['.csv', '.xlsx', '.xls'].includes(ext)) return 'text-blue-400'
  if (['.py', '.js', '.ts', '.json'].includes(ext)) return 'text-orange-400'
  return 'text-text-muted'
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ─── Component ──────────────────────────────────────────────────

export function FileBrowserDialog({
  open,
  onClose,
  onConfirm,
  initialPath,
  multiple = true,
  title = 'Browse Files',
}: FileBrowserDialogProps) {
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const startPath = initialPath || workspacePath || ''

  const [currentPath, setCurrentPath] = useState(startPath)
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [pathHistory, setPathHistory] = useState<string[]>([])

  const searchInputRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  // ─── Load directory ─────────────────────────────────────────

  const loadDir = useCallback(async (dirPath: string) => {
    if (!window.electronAPI?.readDirectory) {
      setError('File system API not available')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const result = await window.electronAPI.readDirectory(dirPath)
      if (result.success && result.entries) {
        const fileEntries: FileEntry[] = result.entries.map((e: any) => ({
          path: e.path,
          name: e.name,
          isDirectory: e.isDirectory,
          extension: e.extension || '',
          size: e.size || 0,
          modifiedTime: e.modifiedTime || '',
        }))
        // Sort: directories first, then alphabetical
        fileEntries.sort((a, b) => {
          if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1
          return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
        })
        setEntries(fileEntries)
      } else {
        setError(result.error || 'Failed to read directory')
        setEntries([])
      }
    } catch (err) {
      setError(String(err))
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [])

  // Load on open or path change
  useEffect(() => {
    if (open && currentPath) {
      loadDir(currentPath)
      setSearchQuery('')
    }
  }, [open, currentPath, loadDir])

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setCurrentPath(startPath)
      setSelected(new Set())
      setPathHistory([])
      setSearchQuery('')
    }
  }, [open, startPath])

  // ─── Navigation ─────────────────────────────────────────────

  const navigateTo = useCallback((dirPath: string) => {
    setPathHistory((prev) => [...prev, currentPath])
    setCurrentPath(dirPath)
    setSelected(new Set())
  }, [currentPath])

  const navigateBack = useCallback(() => {
    if (pathHistory.length > 0) {
      const prev = pathHistory[pathHistory.length - 1]
      setPathHistory((h) => h.slice(0, -1))
      setCurrentPath(prev)
      setSelected(new Set())
    }
  }, [pathHistory])

  const navigateUp = useCallback(() => {
    const parent = currentPath.replace(/[\\/][^\\/]+$/, '')
    if (parent && parent !== currentPath) {
      navigateTo(parent)
    }
  }, [currentPath, navigateTo])

  // ─── Selection ──────────────────────────────────────────────

  const toggleSelect = useCallback((path: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        if (!multiple) next.clear()
        next.add(path)
      }
      return next
    })
  }, [multiple])

  const handleConfirm = useCallback(() => {
    const results: FileBrowserResult[] = []
    for (const path of selected) {
      const entry = entries.find((e) => e.path === path)
      if (entry) {
        results.push({ name: entry.name, path: entry.path })
      }
    }
    if (results.length > 0) {
      onConfirm(results)
    }
  }, [selected, entries, onConfirm])

  // ─── Keyboard ───────────────────────────────────────────────

  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'Enter' && selected.size > 0) {
        handleConfirm()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose, handleConfirm, selected])

  // ─── Filtered entries ───────────────────────────────────────

  const filteredEntries = useMemo(() => {
    if (!searchQuery.trim()) return entries
    const q = searchQuery.toLowerCase()
    return entries.filter((e) => e.name.toLowerCase().includes(q))
  }, [entries, searchQuery])

  // ─── Breadcrumb ─────────────────────────────────────────────

  const breadcrumbs = useMemo(() => {
    if (!currentPath) return []
    // Detect path style: Windows drive (C:\…) or POSIX (/Users/…)
    const isWindowsPath = /^[A-Za-z]:[\\/]/.test(currentPath)
    const sep = isWindowsPath ? '\\' : '/'
    const parts = currentPath.split(/[\\/]/).filter(Boolean)
    const crumbs: { label: string; path: string }[] = []
    let accumulated = isWindowsPath ? '' : '/'
    for (const part of parts) {
      if (crumbs.length === 0) {
        accumulated += part
        // On Windows, first part is "C:" — append separator so paths become "C:\…"
        if (isWindowsPath && part.endsWith(':')) {
          accumulated += sep
        }
      } else {
        accumulated += sep + part
      }
      crumbs.push({ label: part, path: accumulated })
    }
    return crumbs
  }, [currentPath])

  // ─── Render ─────────────────────────────────────────────────

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />

      {/* Dialog */}
      <div
        ref={dialogRef}
        className="relative w-[560px] max-w-[90vw] h-[480px] max-h-[80vh] bg-bg-primary border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-scale-in"
      >
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border bg-bg-secondary/50">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-blue-500/10 flex items-center justify-center ring-1 ring-blue-500/20">
              <FolderOpen className="w-4 h-4 text-blue-400" />
            </div>
            <div>
              <h3 className="text-[13px] font-semibold text-text-primary leading-tight">
                {title}
              </h3>
              <p className="text-[10px] text-text-muted mt-0.5">
                {multiple ? 'Select one or more files' : 'Select a file'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Toolbar: breadcrumb + search */}
        <div className="shrink-0 px-3 py-2 border-b border-border/50 flex items-center gap-2">
          {/* Back button */}
          <button
            onClick={navigateBack}
            disabled={pathHistory.length === 0}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Go back"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>

          {/* Up button */}
          <button
            onClick={navigateUp}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
            title="Go to parent directory"
          >
            <Home className="w-3.5 h-3.5" />
          </button>

          {/* Breadcrumb */}
          <div className="flex-1 min-w-0 flex items-center gap-0.5 overflow-x-auto text-[11px]" style={{ scrollbarWidth: 'none' }}>
            <HardDrive className="w-3 h-3 text-text-muted shrink-0" />
            {breadcrumbs.map((crumb, i) => (
              <span key={crumb.path} className="flex items-center gap-0.5 shrink-0">
                {i > 0 && <ChevronRight className="w-2.5 h-2.5 text-text-muted/40" />}
                <button
                  onClick={() => {
                    if (crumb.path !== currentPath) {
                      navigateTo(crumb.path)
                    }
                  }}
                  className={`px-1 py-0.5 rounded hover:bg-bg-hover transition-colors truncate max-w-[100px] ${
                    crumb.path === currentPath
                      ? 'text-text-primary font-medium'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                  title={crumb.path}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </div>

          {/* Search */}
          <div className="flex items-center gap-1 bg-bg-tertiary rounded-md px-2 py-1 w-36">
            <Search className="w-3 h-3 text-text-muted shrink-0" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter..."
              className="flex-1 bg-transparent text-[11px] text-text-primary placeholder:text-text-muted/50 outline-none w-full"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="text-text-muted hover:text-text-secondary">
                <X className="w-2.5 h-2.5" />
              </button>
            )}
          </div>
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-5 h-5 text-accent-primary animate-spin" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-full px-6">
              <div className="text-center">
                <p className="text-xs text-accent-danger">{error}</p>
                <button
                  onClick={() => loadDir(currentPath)}
                  className="mt-2 text-[11px] text-accent-primary hover:underline"
                >
                  Retry
                </button>
              </div>
            </div>
          ) : filteredEntries.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-xs text-text-muted">
                {searchQuery ? 'No matching files' : 'Empty directory'}
              </p>
            </div>
          ) : (
            <div className="py-1">
              {filteredEntries.map((entry) => {
                const Icon = getFileIcon(entry)
                const iconColor = getIconColor(entry)
                const isSelected = selected.has(entry.path)

                return (
                  <div
                    key={entry.path}
                    className={`
                      flex items-center gap-2.5 px-3 py-1.5 cursor-pointer transition-colors duration-75
                      ${isSelected ? 'bg-accent-primary/10' : 'hover:bg-bg-hover'}
                    `}
                    onClick={() => {
                      if (entry.isDirectory) {
                        navigateTo(entry.path)
                      } else {
                        toggleSelect(entry.path)
                      }
                    }}
                    onDoubleClick={() => {
                      if (entry.isDirectory) {
                        navigateTo(entry.path)
                      } else {
                        // Double-click on file: select and confirm
                        setSelected(new Set([entry.path]))
                        setTimeout(() => {
                          onConfirm([{ name: entry.name, path: entry.path }])
                        }, 50)
                      }
                    }}
                  >
                    {/* Checkbox (files only) */}
                    {!entry.isDirectory ? (
                      <div
                        className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                          isSelected
                            ? 'bg-accent-primary border-accent-primary'
                            : 'border-border hover:border-accent-primary/50'
                        }`}
                      >
                        {isSelected && <Check className="w-2.5 h-2.5 text-white" />}
                      </div>
                    ) : (
                      <div className="w-4 h-4 shrink-0" />
                    )}

                    {/* Icon */}
                    <Icon className={`w-4 h-4 shrink-0 ${iconColor}`} />

                    {/* Name */}
                    <span className={`flex-1 text-[12px] truncate ${
                      entry.isDirectory ? 'text-text-primary font-medium' : 'text-text-secondary'
                    }`}>
                      {entry.name}
                    </span>

                    {/* Size (files only) */}
                    {!entry.isDirectory && entry.size > 0 && (
                      <span className="text-[10px] text-text-muted/60 shrink-0">
                        {formatFileSize(entry.size)}
                      </span>
                    )}

                    {/* Directory arrow */}
                    {entry.isDirectory && (
                      <ChevronRight className="w-3.5 h-3.5 text-text-muted/40 shrink-0" />
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-t border-border bg-bg-secondary/30">
          <div className="text-[11px] text-text-muted">
            {selected.size > 0 ? (
              <span className="text-accent-primary font-medium">
                {selected.size} file{selected.size > 1 ? 's' : ''} selected
              </span>
            ) : (
              <span>{filteredEntries.filter((e) => !e.isDirectory).length} files in this directory</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-[12px] text-text-secondary hover:text-text-primary rounded-lg hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={selected.size === 0}
              className="px-4 py-1.5 text-[12px] font-medium bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 disabled:bg-bg-tertiary disabled:text-text-muted/40 disabled:cursor-not-allowed transition-colors shadow-sm disabled:shadow-none"
            >
              Attach {selected.size > 0 ? `(${selected.size})` : ''}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
