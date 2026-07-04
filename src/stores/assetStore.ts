/**
 * Asset Store — centralized state for the Asset Explorer file tree.
 *
 * Manages:
 * - Workspace directory path
 * - File tree structure (lazy-loaded)
 * - Search / filter state
 * - Expanded folder tracking
 * - Selected file tracking
 * - Layer association (which files are loaded as map layers)
 */
import { create } from 'zustand'

// ─── Types ────────────────────────────────────────────────────────

export type FileNodeType = 'file' | 'directory'

export interface FileNode {
  /** Unique path (relative to workspace root) */
  path: string
  /** Display name */
  name: string
  /** File or directory */
  type: FileNodeType
  /** File extension (lowercase, with dot). Empty for directories */
  extension: string
  /** File size in bytes (0 for directories) */
  size: number
  /** Last modified ISO string */
  modifiedTime: string
  /** Children (only for directories, lazy-loaded) */
  children?: FileNode[]
  /** Whether children have been loaded */
  childrenLoaded?: boolean
  /** Depth level in the tree (0 = root) */
  depth: number
}

export type SortMode = 'name' | 'type' | 'modified' | 'size'
export type SortOrder = 'asc' | 'desc'

// ─── Store Interface ──────────────────────────────────────────────

interface AssetStore {
  // State
  workspacePath: string | null
  rootNodes: FileNode[]
  expandedPaths: Set<string>
  selectedPath: string | null
  searchQuery: string
  isLoading: boolean
  error: string | null
  sortMode: SortMode
  sortOrder: SortOrder

  // Actions
  setWorkspacePath: (path: string | null) => void
  setRootNodes: (nodes: FileNode[]) => void
  toggleExpanded: (path: string) => void
  setExpanded: (path: string, expanded: boolean) => void
  collapseAll: () => void
  expandAll: (paths: string[]) => void
  setSelectedPath: (path: string | null) => void
  setSearchQuery: (query: string) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setSortMode: (mode: SortMode) => void
  setSortOrder: (order: SortOrder) => void
  updateDirectoryChildren: (dirPath: string, children: FileNode[]) => void
  removeNode: (path: string) => void
  refreshNode: (path: string, updatedNode: FileNode) => void

  // Computed
  getFilteredNodes: () => FileNode[]
  isExpanded: (path: string) => boolean
}

// ─── Helpers ──────────────────────────────────────────────────────

/** Recursively update children of a directory node */
function updateChildrenInTree(
  nodes: FileNode[],
  dirPath: string,
  children: FileNode[]
): FileNode[] {
  return nodes.map((node) => {
    if (node.path === dirPath && node.type === 'directory') {
      return { ...node, children, childrenLoaded: true }
    }
    if (node.children) {
      return {
        ...node,
        children: updateChildrenInTree(node.children, dirPath, children),
      }
    }
    return node
  })
}

/** Recursively remove a node from the tree */
function removeNodeFromTree(nodes: FileNode[], targetPath: string): FileNode[] {
  return nodes
    .filter((node) => node.path !== targetPath)
    .map((node) => {
      if (node.children) {
        return { ...node, children: removeNodeFromTree(node.children, targetPath) }
      }
      return node
    })
}

/** Recursively replace a node in the tree */
function refreshNodeInTree(nodes: FileNode[], targetPath: string, updatedNode: FileNode): FileNode[] {
  return nodes.map((node) => {
    if (node.path === targetPath) {
      return updatedNode
    }
    if (node.children) {
      return { ...node, children: refreshNodeInTree(node.children, targetPath, updatedNode) }
    }
    return node
  })
}

/** Recursively filter nodes by search query */
function filterNodes(nodes: FileNode[], query: string): FileNode[] {
  const lowerQuery = query.toLowerCase()
  const result: FileNode[] = []

  for (const node of nodes) {
    if (node.type === 'directory') {
      const filteredChildren = node.children
        ? filterNodes(node.children, query)
        : []
      // Include directory if it has matching children or its name matches
      if (filteredChildren.length > 0 || node.name.toLowerCase().includes(lowerQuery)) {
        result.push({ ...node, children: filteredChildren })
      }
    } else {
      if (node.name.toLowerCase().includes(lowerQuery)) {
        result.push(node)
      }
    }
  }

  return result
}

/** Sort nodes: directories first, then by sort mode */
function sortNodes(nodes: FileNode[], mode: SortMode, order: SortOrder): FileNode[] {
  const sorted = [...nodes].sort((a, b) => {
    // Directories always come first
    if (a.type !== b.type) {
      return a.type === 'directory' ? -1 : 1
    }

    let cmp = 0
    switch (mode) {
      case 'name':
        cmp = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
        break
      case 'type':
        cmp = a.extension.localeCompare(b.extension) || a.name.localeCompare(b.name)
        break
      case 'modified':
        cmp = a.modifiedTime.localeCompare(b.modifiedTime)
        break
      case 'size':
        cmp = a.size - b.size
        break
    }

    return order === 'asc' ? cmp : -cmp
  })

  // Recursively sort children
  return sorted.map((node) => {
    if (node.children) {
      return { ...node, children: sortNodes(node.children, mode, order) }
    }
    return node
  })
}

// ─── Store Implementation ─────────────────────────────────────────

export const useAssetStore = create<AssetStore>((set, get) => ({
  // Initial state
  workspacePath: null,
  rootNodes: [],
  expandedPaths: new Set<string>(),
  selectedPath: null,
  searchQuery: '',
  isLoading: false,
  error: null,
  sortMode: 'name',
  sortOrder: 'asc',

  // ─── Actions ────────────────────────────────────────────────

  setWorkspacePath: (path) => set({ workspacePath: path }),

  setRootNodes: (nodes) => set({ rootNodes: nodes }),

  toggleExpanded: (path) =>
    set((state) => {
      const next = new Set(state.expandedPaths)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return { expandedPaths: next }
    }),

  setExpanded: (path, expanded) =>
    set((state) => {
      const next = new Set(state.expandedPaths)
      if (expanded) {
        next.add(path)
      } else {
        next.delete(path)
      }
      return { expandedPaths: next }
    }),

  collapseAll: () => set({ expandedPaths: new Set() }),

  expandAll: (paths) =>
    set((state) => {
      const next = new Set(state.expandedPaths)
      paths.forEach((p) => next.add(p))
      return { expandedPaths: next }
    }),

  setSelectedPath: (path) => set({ selectedPath: path }),

  setSearchQuery: (query) => set({ searchQuery: query }),

  setLoading: (loading) => set({ isLoading: loading }),

  setError: (error) => set({ error }),

  setSortMode: (mode) => set({ sortMode: mode }),

  setSortOrder: (order) => set({ sortOrder: order }),

  updateDirectoryChildren: (dirPath, children) =>
    set((state) => ({
      rootNodes: updateChildrenInTree(state.rootNodes, dirPath, children),
    })),

  removeNode: (path) =>
    set((state) => ({
      rootNodes: removeNodeFromTree(state.rootNodes, path),
      selectedPath: state.selectedPath === path ? null : state.selectedPath,
    })),

  refreshNode: (path, updatedNode) =>
    set((state) => ({
      rootNodes: refreshNodeInTree(state.rootNodes, path, updatedNode),
    })),

  // ─── Computed ───────────────────────────────────────────────

  getFilteredNodes: () => {
    const { rootNodes, searchQuery, sortMode, sortOrder } = get()
    let nodes = rootNodes

    if (searchQuery.trim()) {
      nodes = filterNodes(nodes, searchQuery.trim())
    }

    return sortNodes(nodes, sortMode, sortOrder)
  },

  isExpanded: (path) => get().expandedPaths.has(path),
}))
