/**
 * AssetExplorer — file tree sidebar panel for managing project data assets.
 *
 * Features:
 * - Workspace folder selection
 * - Lazy-loaded file tree with expand/collapse
 * - File type icons with GIS format awareness
 * - Search / filter by filename
 * - Right-click context menu (add to map, rename, delete, properties)
 * - Layer association indicators
 * - Drag & drop files to add as layers
 * - Sort by name / type / modified / size
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import {
  FolderOpen,
  FolderClosed,
  File as LucideFile,
  FileText,
  Search,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  MapPin,
  Trash2,
  Pencil,
  X,
  ArrowUpDown,
  Globe,
  Database,
  Image,
  Table,
  FileCode,
  Layers,
  FolderPlus,
  ScrollText,
  Loader2,
  BarChart3,
} from 'lucide-react'
import { useT } from '@/i18n'
import { useAssetStore, type FileNode, type SortMode } from '@/stores/assetStore'
import { useMapStore } from '@/stores/mapStore'
import { useViewStore } from '@/stores/viewStore'
import { loadGeoFiles, isSupportedExtension } from '@/services/geo'
import { mapEngine } from '@/features/map/engine/MapEngine'
import { useDialog } from '@/components/Dialog'
import { usePivotStore } from '@/stores/pivotStore'
import { canPivotFile } from '@/features/pivot/pivotData'
import { targetFromFile } from '@/features/pivot/types'

// ─── GIS file extension sets ──────────────────────────────────────

const GIS_VECTOR_EXTS = new Set(['.geojson', '.json', '.shp', '.gpkg', '.kml', '.kmz', '.gml'])
const GIS_RASTER_EXTS = new Set(['.tif', '.tiff', '.nc', '.hdf5', '.h5'])
const GIS_TABLE_EXTS = new Set(['.csv', '.tsv', '.xlsx', '.xls', '.dbf'])
const CODE_EXTS = new Set(['.py', '.js', '.ts', '.tsx', '.jsx', '.r', '.ipynb', '.md'])
const TEXT_EXTS = new Set(['.json', '.yaml', '.yml', '.toml', '.xml', '.html', '.css', '.scss', '.sh', '.bash', '.sql', '.md', '.rst', '.txt', '.log', '.ini', '.cfg', '.conf', '.env', '.gitignore', '.editorconfig'])
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.svg', '.gif', '.bmp', '.webp'])
const CSV_PREVIEW_MAX_BYTES = 5 * 1024 * 1024

/**
 * Extensions that are openable in the tab viewer in addition to code/text:
 * - GeoJSON: treated as JSON for highlighting purposes
 * - CSV / TSV: rendered as a grid by CsvTableView
 */
const VIEWABLE_EXTRA_EXTS = new Set(['.geojson', '.csv', '.tsv'])

function isViewableInTab(ext: string): boolean {
  return CODE_EXTS.has(ext) || TEXT_EXTS.has(ext) || VIEWABLE_EXTRA_EXTS.has(ext)
}

/**
 * Returns true for "code script" extensions that should open on single-click.
 * Data / config files like .json, .yaml, .xml, .csv etc. are excluded —
 * they can still be opened via the right-click context menu "View Code".
 */
function isCodeScript(ext: string): boolean {
  return CODE_EXTS.has(ext)
}

// ─── Main Component ───────────────────────────────────────────────

export function AssetExplorer() {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const rootNodes = useAssetStore((s) => s.rootNodes)
  const isLoading = useAssetStore((s) => s.isLoading)
  const error = useAssetStore((s) => s.error)
  const searchQuery = useAssetStore((s) => s.searchQuery)
  const setSearchQuery = useAssetStore((s) => s.setSearchQuery)
  const setWorkspacePath = useAssetStore((s) => s.setWorkspacePath)
  const setRootNodes = useAssetStore((s) => s.setRootNodes)
  const setLoading = useAssetStore((s) => s.setLoading)
  const setError = useAssetStore((s) => s.setError)
  const getFilteredNodes = useAssetStore((s) => s.getFilteredNodes)
  const collapseAll = useAssetStore((s) => s.collapseAll)
  const sortMode = useAssetStore((s) => s.sortMode)
  const setSortMode = useAssetStore((s) => s.setSortMode)

  const [showSearch, setShowSearch] = useState(false)
  const [showSortMenu, setShowSortMenu] = useState(false)
  const searchInputRef = useRef<HTMLInputElement>(null)

  // ─── Load workspace directory ─────────────────────────────────

  const loadDirectory = useCallback(
    async (dirPath: string) => {
      if (!window.electronAPI?.readDirectory) return

      setLoading(true)
      setError(null)

      try {
        const result = await window.electronAPI.readDirectory(dirPath)
        if (result.success && result.entries) {
          const nodes: FileNode[] = result.entries.map((entry: any) => ({
            path: entry.path,
            name: entry.name,
            type: entry.isDirectory ? 'directory' : 'file',
            extension: entry.extension || '',
            size: entry.size,
            modifiedTime: entry.modifiedTime,
            children: entry.isDirectory ? [] : undefined,
            childrenLoaded: false,
            depth: 0,
          }))
          setRootNodes(nodes)
        } else {
          setError(result.error || 'Failed to read directory')
        }
      } catch (err) {
        setError(String(err))
      } finally {
        setLoading(false)
      }
    },
    [setRootNodes, setLoading, setError]
  )

  // Auto-load when workspace path changes
  useEffect(() => {
    if (workspacePath) {
      loadDirectory(workspacePath)
    }
  }, [workspacePath, loadDirectory])

  // ─── Open folder ──────────────────────────────────────────────

  const [openingFolder, setOpeningFolder] = useState(false)

  const handleOpenFolder = useCallback(async () => {
    if (!window.electronAPI?.openFolderDialog) {
      console.warn('openFolderDialog not available — running outside Electron?')
      return
    }

    setOpeningFolder(true)
    try {
      const folderPath = await window.electronAPI.openFolderDialog()
      if (folderPath) {
        setWorkspacePath(folderPath)
      }
    } finally {
      setOpeningFolder(false)
    }
  }, [setWorkspacePath])

  // ─── Refresh ──────────────────────────────────────────────────

  const handleRefresh = useCallback(() => {
    if (workspacePath) {
      loadDirectory(workspacePath)
    }
  }, [workspacePath, loadDirectory])

  useEffect(() => {
    const onAssetsRefresh = () => {
      if (workspacePath) {
        loadDirectory(workspacePath)
      }
    }

    window.addEventListener('opengis:assets-refresh', onAssetsRefresh)
    return () => window.removeEventListener('opengis:assets-refresh', onAssetsRefresh)
  }, [workspacePath, loadDirectory])

  // ─── Toggle search ────────────────────────────────────────────

  const handleToggleSearch = useCallback(() => {
    setShowSearch((prev) => {
      if (!prev) {
        setTimeout(() => searchInputRef.current?.focus(), 50)
      } else {
        setSearchQuery('')
      }
      return !prev
    })
  }, [setSearchQuery])

  // ─── Sort menu ────────────────────────────────────────────────

  const handleSortChange = useCallback(
    (mode: SortMode) => {
      setSortMode(mode)
      setShowSortMenu(false)
    },
    [setSortMode]
  )

  // ─── Open log directory ───────────────────────────────────────

  const handleOpenLogs = useCallback(async () => {
    const api = (window as any).electronAPI
    if (!api?.openLogDir) {
      console.warn('openLogDir not available — running outside Electron?')
      return
    }
    const res = await api.openLogDir()
    if (!res?.success) {
      console.error('Failed to open log directory:', res?.error)
    }
  }, [])

  // ─── Render ───────────────────────────────────────────────────

  const filteredNodes = getFilteredNodes()
  const workspaceName = workspacePath
    ? workspacePath.split(/[\\/]/).pop() || workspacePath
    : null

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-1">
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          {workspaceName || t.assets.explorer}
        </span>

        {/* Search toggle */}
        <button
          onClick={handleToggleSearch}
          className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${
            showSearch
              ? 'text-accent-primary bg-accent-primary/10'
              : 'text-text-muted hover:text-accent-primary hover:bg-accent-primary/10'
          }`}
          title={t.assets.searchFiles}
        >
          <Search className="w-3.5 h-3.5" />
        </button>

        {/* Sort */}
        <div className="relative">
          <button
            onClick={() => setShowSortMenu(!showSortMenu)}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
            title={t.assets.sortFiles}
          >
            <ArrowUpDown className="w-3.5 h-3.5" />
          </button>
          {showSortMenu && (
            <SortMenu
              currentMode={sortMode}
              onSelect={handleSortChange}
              onClose={() => setShowSortMenu(false)}
            />
          )}
        </div>

        {/* Refresh */}
        {workspacePath && (
          <button
            onClick={handleRefresh}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
            title={t.assets.refresh}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        )}

        {/* Collapse all */}
        {workspacePath && (
          <button
            onClick={collapseAll}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
            title={t.assets.collapseAll}
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        )}

        {/* Open log directory */}
        <button
          onClick={handleOpenLogs}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title={t.assets.revealLogs}
        >
          <ScrollText className="w-3.5 h-3.5" />
        </button>

        {/* Open folder */}
        <button
          onClick={handleOpenFolder}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title={t.assets.openFolder}
        >
          <FolderPlus className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Search bar */}
      {showSearch && (
        <div className="px-2 py-1.5 border-b border-border shrink-0 animate-slide-up">
          <div className="flex items-center gap-1.5 bg-bg-tertiary rounded-md px-2 py-1">
            <Search className="w-3 h-3 text-text-muted shrink-0" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t.assets.filterPlaceholder}
              className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-muted/50 outline-none"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="text-text-muted hover:text-text-secondary"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* File tree */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {openingFolder ? (
          <LoadingState />
        ) : !workspacePath ? (
          <EmptyState onOpenFolder={handleOpenFolder} />
        ) : isLoading && rootNodes.length === 0 ? (
          <LoadingState />
        ) : error ? (
          <ErrorState error={error} onRetry={handleRefresh} />
        ) : filteredNodes.length === 0 ? (
          searchQuery ? (
            <NoResultsState query={searchQuery} />
          ) : (
            <EmptyFolderState />
          )
        ) : (
          <div className="py-1">
            {filteredNodes.map((node) => (
              <FileTreeNode key={node.path} node={node} depth={0} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── File Tree Node ─────────────────────────────────────────────

interface FileTreeNodeProps {
  node: FileNode
  depth: number
}

function FileTreeNode({ node, depth }: FileTreeNodeProps) {
  const t = useT()
  const isExpanded = useAssetStore((s) => s.isExpanded(node.path))
  const toggleExpanded = useAssetStore((s) => s.toggleExpanded)
  const selectedPath = useAssetStore((s) => s.selectedPath)
  const setSelectedPath = useAssetStore((s) => s.setSelectedPath)
  const updateDirectoryChildren = useAssetStore((s) => s.updateDirectoryChildren)
  const addLayers = useMapStore((s) => s.addLayers)
  const layers = useMapStore((s) => s.layers)
  const openPivot = usePivotStore((s) => s.open)
  const { alert } = useDialog()

  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(node.name)
  const [isLoadingToMap, setIsLoadingToMap] = useState(false)
  const renameInputRef = useRef<HTMLInputElement>(null)

  const isSelected = selectedPath === node.path
  const isDirectory = node.type === 'directory'
  const isGisFile = isSupportedExtension(node.name)
  const canPivot = canPivotFile(node)
  // Defensive: agent-created layers or legacy entries may lack `meta`.
  // Never assume it's present — otherwise a single bad layer crashes the
  // whole sidebar tree.
  //
  // Match by full filePath first (unique), fall back to fileName for legacy
  // layers loaded before filePath was tracked. Matching by fileName alone
  // falsely marks unrelated same-named files as loaded and hides the
  // "Add to Map" menu item.
  const isLayerLoaded = layers.some((l) => {
    const meta = l?.meta
    if (!meta) return false
    if (meta.filePath && meta.filePath === node.path) return true
    // Only fall back to fileName match when layer has NO filePath recorded.
    if (!meta.filePath && meta.fileName === node.name) return true
    return false
  })

  // ─── Expand directory (lazy load children) ────────────────────

  const handleToggle = useCallback(async () => {
    if (!isDirectory) return

    if (!isExpanded && !node.childrenLoaded && window.electronAPI) {
      try {
        const result = await window.electronAPI.readDirectory(node.path)
        if (result.success && result.entries) {
          const children: FileNode[] = result.entries.map((entry: any) => ({
            path: entry.path,
            name: entry.name,
            type: entry.isDirectory ? 'directory' : 'file',
            extension: entry.extension || '',
            size: entry.size,
            modifiedTime: entry.modifiedTime,
            children: entry.isDirectory ? [] : undefined,
            childrenLoaded: false,
            depth: depth + 1,
          }))
          updateDirectoryChildren(node.path, children)
        }
      } catch (err) {
        console.error('Failed to load directory:', err)
      }
    }

    toggleExpanded(node.path)
  }, [isDirectory, isExpanded, node, depth, toggleExpanded, updateDirectoryChildren])

  // ─── Click handler ────────────────────────────────────────────

  const handleClick = useCallback(() => {
    setSelectedPath(node.path)
    if (isDirectory) {
      handleToggle()
    } else {
      // Single-click opens code scripts and image files directly
      const ext = node.extension.toLowerCase()
      if (isCodeScript(ext) || IMAGE_EXTS.has(ext)) {
        openFileInViewer(node, alert)
      }
    }
  }, [node, isDirectory, setSelectedPath, handleToggle, alert])

  // ─── Double-click: add to map ─────────────────────────────────

  const handleDoubleClick = useCallback(async () => {
    if (isDirectory || !isGisFile || isLoadingToMap) return
    setIsLoadingToMap(true)
    try {
      await addFileToMap(node, addLayers)
    } catch (err) {
      console.error('[AssetExplorer] Add to Map failed:', err)
      alert({
        title: t.assets.failedToAddLayer,
        message: `Could not add "${node.name}" to the map:\n\n${(err as Error)?.message || String(err)}`,
        severity: 'error',
      })
    } finally {
      setIsLoadingToMap(false)
    }
  }, [isDirectory, isGisFile, isLoadingToMap, node, addLayers, alert])

  // ─── Context menu ─────────────────────────────────────────────

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setSelectedPath(node.path)
      setContextMenu({ x: e.clientX, y: e.clientY })
    },
    [node.path, setSelectedPath]
  )

  // ─── Rename ───────────────────────────────────────────────────

  const handleStartRename = useCallback(() => {
    setRenameValue(node.name)
    setIsRenaming(true)
    setContextMenu(null)
    setTimeout(() => {
      renameInputRef.current?.focus()
      // Select filename without extension
      const dotIdx = node.name.lastIndexOf('.')
      if (dotIdx > 0) {
        renameInputRef.current?.setSelectionRange(0, dotIdx)
      } else {
        renameInputRef.current?.select()
      }
    }, 50)
  }, [node.name])

  const handleRenameSubmit = useCallback(async () => {
    setIsRenaming(false)
    if (!renameValue.trim() || renameValue === node.name) return

    if (window.electronAPI) {
      const parentPath = node.path.substring(0, node.path.lastIndexOf(node.name.length > 0 ? node.name : ''))
      const newPath = parentPath + renameValue
      const result = await window.electronAPI.renameFile(node.path, newPath)
      if (!result.success) {
        console.error('Rename failed:', result.error)
      }
      // Refresh parent directory
      const workspacePath = useAssetStore.getState().workspacePath
      if (workspacePath) {
        // Trigger a refresh of the parent
        const parentDir = node.path.substring(0, node.path.length - node.name.length - 1)
        if (parentDir) {
          const dirResult = await window.electronAPI.readDirectory(parentDir)
          if (dirResult.success && dirResult.entries) {
            const children: FileNode[] = dirResult.entries.map((entry: any) => ({
              path: entry.path,
              name: entry.name,
              type: entry.isDirectory ? 'directory' : 'file',
              extension: entry.extension || '',
              size: entry.size,
              modifiedTime: entry.modifiedTime,
              children: entry.isDirectory ? [] : undefined,
              childrenLoaded: false,
              depth: depth,
            }))
            updateDirectoryChildren(parentDir, children)
          }
        }
      }
    }
  }, [renameValue, node, depth, updateDirectoryChildren])

  // ─── Render ───────────────────────────────────────────────────

  const paddingLeft = 8 + depth * 16

  return (
    <>
      <div
        className={`
          group relative flex items-center gap-1 pr-2 py-[3px] cursor-pointer
          transition-colors duration-75
          ${isSelected ? 'bg-accent-primary/12 text-text-primary' : 'hover:bg-bg-hover text-text-secondary'}
        `}
        style={{ paddingLeft }}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
        title={node.path}
      >
        {/* Expand/collapse arrow (directories only) */}
        {isDirectory ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              handleToggle()
            }}
            className="w-4 h-4 flex items-center justify-center shrink-0 text-text-muted"
          >
            {isExpanded ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
          </button>
        ) : (
          <div className="w-4 h-4 shrink-0" />
        )}

        {/* File/folder icon */}
        <FileIcon node={node} isExpanded={isExpanded} />

        {/* Name (or rename input) */}
        {isRenaming ? (
          <input
            ref={renameInputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onBlur={handleRenameSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameSubmit()
              if (e.key === 'Escape') setIsRenaming(false)
            }}
            className="flex-1 bg-bg-tertiary text-xs text-text-primary px-1 py-0.5 rounded outline-none border border-accent-primary min-w-0"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span
            className={`text-xs truncate flex-1 ${
              isSelected ? 'text-text-primary' : ''
            }`}
          >
            {node.name}
          </span>
        )}

        {/* Loading spinner */}
        {isLoadingToMap && (
          <Loader2 className="w-3.5 h-3.5 shrink-0 text-accent-primary animate-spin" />
        )}

        {/* Layer loaded indicator */}
        {!isLoadingToMap && isLayerLoaded && (
          <div
            className="w-1.5 h-1.5 rounded-full bg-accent-geo shrink-0"
            title={t.assets.loadedAsLayer}
          />
        )}

        {/* GIS file indicator */}
        {!isLoadingToMap && isGisFile && !isLayerLoaded && (
          <div
            className="w-1.5 h-1.5 rounded-full bg-accent-primary/40 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
            title={t.assets.gisFileHint}
          />
        )}
      </div>

      {/* Children (expanded directories) */}
      {isDirectory && isExpanded && node.children && (
        <div className="animate-slide-up">
          {node.children.map((child) => (
            <FileTreeNode key={child.path} node={child} depth={depth + 1} />
          ))}
          {node.children.length === 0 && node.childrenLoaded && (
            <div
              className="text-2xs text-text-muted/50 italic py-1"
              style={{ paddingLeft: paddingLeft + 20 }}
            >
              {t.assets.emptyFolder}
            </div>
          )}
        </div>
      )}

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          node={node}
          position={contextMenu}
          isGisFile={isGisFile}
          isLayerLoaded={isLayerLoaded}
          canPivot={canPivot}
          onClose={() => setContextMenu(null)}
          onRename={handleStartRename}
          onOpenPivot={() => {
            setContextMenu(null)
            openPivot(targetFromFile(node))
          }}
          onAddToMap={async () => {
            setContextMenu(null)
            if (isLoadingToMap) return
            setIsLoadingToMap(true)
            try {
              await addFileToMap(node, addLayers)
            } catch (err) {
              console.error('[AssetExplorer] Add to Map failed:', err)
              alert({
                title: t.assets.failedToAddLayer,
                message: `Could not add "${node.name}" to the map:\n\n${(err as Error)?.message || String(err)}`,
                severity: 'error',
              })
            } finally {
              setIsLoadingToMap(false)
            }
          }}
        />
      )}
    </>
  )
}

// ─── File Icon ──────────────────────────────────────────────────

function FileIcon({ node, isExpanded }: { node: FileNode; isExpanded: boolean }) {
  const ext = node.extension.toLowerCase()
  const iconClass = 'w-4 h-4 shrink-0'

  if (node.type === 'directory') {
    return isExpanded ? (
      <FolderOpen className={`${iconClass} text-accent-warning`} />
    ) : (
      <FolderClosed className={`${iconClass} text-accent-warning/70`} />
    )
  }

  // GIS vector files
  if (GIS_VECTOR_EXTS.has(ext)) {
    return <Globe className={`${iconClass} text-accent-geo`} />
  }

  // GIS raster files
  if (GIS_RASTER_EXTS.has(ext)) {
    return <Image className={`${iconClass} text-green-400`} />
  }

  // Table / CSV files
  if (GIS_TABLE_EXTS.has(ext)) {
    return <Table className={`${iconClass} text-accent-primary`} />
  }

  // Code files
  if (CODE_EXTS.has(ext)) {
    return <FileCode className={`${iconClass} text-yellow-400`} />
  }

  // Image files
  if (IMAGE_EXTS.has(ext)) {
    return <Image className={`${iconClass} text-pink-400`} />
  }

  // Database files
  if (ext === '.gpkg' || ext === '.sqlite' || ext === '.db') {
    return <Database className={`${iconClass} text-purple-400`} />
  }

  // Default file icon
  return <LucideFile className={`${iconClass} text-text-muted`} />
}

// ─── Context Menu ───────────────────────────────────────────────

interface ContextMenuProps {
  node: FileNode
  position: { x: number; y: number }
  isGisFile: boolean
  isLayerLoaded: boolean
  canPivot: boolean
  onClose: () => void
  onRename: () => void
  onAddToMap: () => void
  onOpenPivot: () => void
}

function ContextMenu({
  node,
  position,
  isGisFile,
  isLayerLoaded,
  canPivot,
  onClose,
  onRename,
  onAddToMap,
  onOpenPivot,
}: ContextMenuProps) {
  const t = useT()
  const removeNode = useAssetStore((s) => s.removeNode)
  const menuRef = useRef<HTMLDivElement>(null)
  const { confirm, alert } = useDialog()

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const handleDelete = useCallback(async () => {
    onClose()
    if (!window.electronAPI) return

    const confirmed = await confirm({
      title: t.assets.deleteFile,
      message: t.assets.deleteConfirm.replace('{name}', node.name),
      okLabel: t.assets.delete,
      danger: true,
    })
    if (!confirmed) return

    const result = await window.electronAPI.deleteFile(node.path)
    if (result.success) {
      removeNode(node.path)
    } else {
      console.error('Delete failed:', result.error)
    }
  }, [node, onClose, removeNode, confirm])

  const handleCopyPath = useCallback(() => {
    navigator.clipboard.writeText(node.path)
    onClose()
  }, [node.path, onClose])

  // Adjust position to stay within viewport
  const style: React.CSSProperties = {
    position: 'fixed',
    left: position.x,
    top: position.y,
    zIndex: 9999,
  }

  return (
    <div
      ref={menuRef}
      className="bg-bg-secondary border border-border rounded-lg shadow-xl py-1 min-w-[180px] animate-fade-in"
      style={style}
    >
      {/* Add to map (GIS files only) */}
      {isGisFile && !isLayerLoaded && node.type === 'file' && (
        <ContextMenuItem
          icon={<MapPin className="w-3.5 h-3.5" />}
          label={t.assets.addToMap}
          onClick={onAddToMap}
          accent
        />
      )}

      {isLayerLoaded && node.type === 'file' && (
        <ContextMenuItem
          icon={<Layers className="w-3.5 h-3.5" />}
          label={t.assets.alreadyOnMap}
          onClick={onClose}
          disabled
        />
      )}

      {canPivot && node.type === 'file' && (
        <ContextMenuItem
          icon={<BarChart3 className="w-3.5 h-3.5" />}
          label="数据透视"
          onClick={onOpenPivot}
          pivot
        />
      )}

      {/* View code (code/text/csv/geojson files) */}
      {node.type === 'file' && isViewableInTab(node.extension.toLowerCase()) && (
        <ContextMenuItem
          icon={<FileCode className="w-3.5 h-3.5" />}
          label={t.assets.viewCode}
          onClick={() => {
            onClose()
            openFileInViewer(node, alert)
          }}
        />
      )}

      {(isGisFile || isLayerLoaded) && node.type === 'file' && (
        <div className="h-px bg-border mx-2 my-1" />
      )}

      {/* Rename */}
      <ContextMenuItem
        icon={<Pencil className="w-3.5 h-3.5" />}
        label={t.assets.rename}
        onClick={onRename}
      />

      {/* Copy path */}
      <ContextMenuItem
        icon={<FileText className="w-3.5 h-3.5" />}
        label={t.assets.copyPath}
        onClick={handleCopyPath}
      />

      {/* Show in Finder / Explorer */}
      <ContextMenuItem
        icon={<FolderOpen className="w-3.5 h-3.5" />}
        label={t.assets.showInFolder}
        onClick={() => {
          onClose()
          window.electronAPI?.showItemInFolder(node.path)
        }}
      />

      <div className="h-px bg-border mx-2 my-1" />

      {/* Delete */}
      <ContextMenuItem
        icon={<Trash2 className="w-3.5 h-3.5" />}
        label={t.assets.delete}
        onClick={handleDelete}
        danger
      />
    </div>
  )
}

function ContextMenuItem({
  icon,
  label,
  onClick,
  accent,
  pivot,
  danger,
  disabled,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  accent?: boolean
  pivot?: boolean
  danger?: boolean
  disabled?: boolean
}) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={`
        w-full flex items-center gap-2.5 px-3 py-1.5 text-xs transition-colors
        ${disabled
          ? 'text-text-muted/40 cursor-default'
          : danger
            ? 'text-text-secondary hover:text-accent-danger hover:bg-accent-danger/10'
            : pivot
              ? 'text-accent-geo hover:bg-accent-geo/10'
            : accent
              ? 'text-accent-primary hover:bg-accent-primary/10'
              : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
        }
      `}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

// ─── Sort Menu ──────────────────────────────────────────────────

function SortMenu({
  currentMode,
  onSelect,
  onClose,
}: {
  currentMode: SortMode
  onSelect: (mode: SortMode) => void
  onClose: () => void
}) {
  const t = useT()
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  const options: { mode: SortMode; label: string }[] = [
    { mode: 'name', label: t.assets.sortName },
    { mode: 'type', label: t.assets.sortType },
    { mode: 'modified', label: t.assets.sortDate },
    { mode: 'size', label: t.assets.sortSize },
  ]

  return (
    <div
      ref={menuRef}
      className="absolute right-0 top-full mt-1 bg-bg-secondary border border-border rounded-lg shadow-xl py-1 min-w-[140px] z-50 animate-fade-in"
    >
      <div className="px-3 py-1 text-2xs text-text-muted font-medium uppercase tracking-wider">
        {t.assets.sortBy}
      </div>
      {options.map(({ mode, label }) => (
        <button
          key={mode}
          onClick={() => onSelect(mode)}
          className={`
            w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors
            ${currentMode === mode
              ? 'text-accent-primary bg-accent-primary/10'
              : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
            }
          `}
        >
          <span className="w-3 text-center">
            {currentMode === mode ? '✓' : ''}
          </span>
          <span>{label}</span>
        </button>
      ))}
    </div>
  )
}

// ─── State Components ───────────────────────────────────────────

function EmptyState({ onOpenFolder }: { onOpenFolder: () => void }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-primary/10 flex items-center justify-center mx-auto mb-3">
          <FolderOpen className="w-5 h-5 text-accent-primary/50" />
        </div>
        <p className="text-xs text-text-muted mb-2">{t.assets.noWorkspace}</p>
        <button
          onClick={onOpenFolder}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors flex items-center gap-1 mx-auto"
        >
          <FolderPlus className="w-3 h-3" />
          {t.assets.openWorkspace}
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
        <p className="text-xs text-text-muted">{t.assets.loadingFiles}</p>
      </div>
    </div>
  )
}

function ErrorState({ error, onRetry }: { error: string; onRetry: () => void }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-danger/10 flex items-center justify-center mx-auto mb-3">
          <X className="w-5 h-5 text-accent-danger/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">{t.assets.failedToLoad}</p>
        <p className="text-2xs text-text-muted/60 mb-2 max-w-[180px] truncate">{error}</p>
        <button
          onClick={onRetry}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors"
        >
          {t.common.retry}
        </button>
      </div>
    </div>
  )
}

function NoResultsState({ query }: { query: string }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <Search className="w-5 h-5 text-text-muted/30 mx-auto mb-2" />
        <p className="text-xs text-text-muted">{t.assets.noFilesMatching}</p>
        <p className="text-2xs text-accent-primary truncate max-w-[160px]">"{query}"</p>
      </div>
    </div>
  )
}

function EmptyFolderState() {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <FolderOpen className="w-5 h-5 text-text-muted/30 mx-auto mb-2" />
        <p className="text-xs text-text-muted">{t.assets.emptyFolder}</p>
      </div>
    </div>
  )
}

// ─── Helpers ────────────────────────────────────────────────────

/**
 * Add a file node to the map as a layer.
 * Reads the file via Electron IPC and passes it through the geo loader.
 *
 * Throws on any failure so the caller can surface a visible error to the user.
 * (Previously this swallowed errors silently — users would click "Add to Map"
 * and get zero feedback when the CSV had no coordinate columns, the file was
 * too large, or parsing failed.)
 */
async function addFileToMap(
  node: FileNode,
  addLayers: (layers: any[]) => void
) {
  if (node.type !== 'file') {
    throw new Error('Only files can be added to the map.')
  }
  if (!window.electronAPI) {
    throw new Error('Electron API not available — are you running in the desktop app?')
  }

  // Read file content based on type
  const ext = node.extension.toLowerCase()
  let file: File

  // Binary formats: shapefiles and raster (GeoTIFF) must be read as ArrayBuffer
  const BINARY_EXTS = new Set(['.shp', '.dbf', '.shx', '.prj', '.cpg', '.tif', '.tiff'])
  if (BINARY_EXTS.has(ext)) {
    const result = await window.electronAPI.readFileAsBuffer(node.path)
    if (!result.success || !result.buffer) {
      throw new Error(result.error || 'Failed to read file.')
    }
    file = new File([result.buffer], node.name)
  } else {
    // For text-based formats, read as text
    const result = await window.electronAPI.readFile(node.path)
    if (!result.success || result.content === undefined) {
      throw new Error(result.error || 'Failed to read file.')
    }
    file = new File([result.content], node.name)
  }

  const layers = await loadGeoFiles([file])
  if (layers.length === 0) {
    // loadGeoFiles swallows per-parser errors internally. Give the user a
    // hint about common causes so they're not left staring at an unchanged map.
    throw new Error(
      `No layers could be loaded from "${node.name}". ` +
      `Common causes: unsupported format, CSV without lat/lng columns, empty file, or malformed GeoJSON.`
    )
  }

  // Stamp the absolute filesystem path onto each layer so AssetExplorer can
  // reliably detect "already loaded" across directories with same-named files.
  for (const layer of layers) {
    if (layer.meta) {
      layer.meta.filePath = node.path
    }
  }

  addLayers(layers)

  // Fit to first layer
  const first = layers[0]
  if (first.data.kind === 'vector') {
    const { bbox } = first.data
    mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
  } else if (first.data.kind === 'raster') {
    const { bbox } = first.data
    mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
  }
}

/**
 * Open a file in the code viewer tab.
 * Reads the file content via Electron IPC and opens a new tab.
 */
type AlertFn = (options: {
  title: string
  message?: string
  severity?: 'info' | 'warning' | 'error'
}) => Promise<void> | void

async function openFileInViewer(node: FileNode, alert?: AlertFn) {
  if (!window.electronAPI?.readFile) {
    console.warn('readFile not available — running outside Electron?')
    return
  }

  // Workflow files (*.flow.json) route to the workflow canvas editor
  if (/\.flow\.json$/i.test(node.name)) {
    const displayName = node.name.replace(/\.flow\.json$/i, '')
    useViewStore.getState().openTab({
      title: displayName,
      type: 'code',
      filePath: node.path,
      language: 'workflow',
    })
    return
  }

  // Image files open in the image viewer (no content preload needed)
  const IMAGE_EXTS_LOCAL = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg']
  const ext = node.extension.toLowerCase()
  if (IMAGE_EXTS_LOCAL.includes(ext)) {
    useViewStore.getState().openTab({
      title: node.name,
      type: 'image',
      filePath: node.path,
      language: 'image',
    })
    return
  }

  if ((ext === '.csv' || ext === '.tsv') && node.size > CSV_PREVIEW_MAX_BYTES) {
    const maxMb = Math.round(CSV_PREVIEW_MAX_BYTES / 1024 / 1024)
    alert?.({
      title: 'CSV preview skipped',
      message: `"${node.name}" is larger than ${maxMb}MB. Open smaller CSV/TSV files in the built-in table preview, or load this file as a map/data layer instead.`,
      severity: 'warning',
    })
    return
  }

  try {
    const result = await window.electronAPI.readFile(node.path)
    if (result.success && result.content !== undefined) {
      useViewStore.getState().openFileAsTab(node.path, node.name, result.content)
    } else {
      console.error('Failed to read file:', result.error)
    }
  } catch (err) {
    console.error('Failed to open file in viewer:', err)
  }
}
