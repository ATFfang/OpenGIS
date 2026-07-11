/**
 * Workflow Store — 管理当前工作区中的工作流列表
 * 以及当前正在编辑的工作流的内存状态。
 *
 * 持久化模型：
 * - 一个工作流 = `<workspace>/workflows/` 中的一个 `<name>.flow.json`
 * - Store 仅缓存平面索引（名称、路径、更新时间）用于
 *   侧边栏列表；完整文档在用户打开工作流选项卡时
 *   按需加载。
 * - 所有变更操作都通过此 store 进行，以便画布编辑器、
 *   侧边栏列表和任何未来的 Python 调用者保持一致。
 */
import { create } from 'zustand'
import {
  createEmptyWorkflow,
  isWorkflowFilename,
  parseWorkflow,
  serialiseWorkflow,
  WORKFLOW_DIR_NAME,
  WORKFLOW_FILE_EXT,
  type Workflow,
  type WorkflowNode,
  type WorkflowEdge,
} from '@/features/workflows/workflow-schema'
import { useAssetStore } from '@/stores/assetStore'

// ─── 类型定义 ──────────────────────────────────────────────

export interface WorkflowIndexEntry {
  /** .flow.json 文件的绝对路径 */
  path: string
  /** 不带扩展名的文件名，用作显示名称 */
  name: string
  /** 最后修改的 ISO 字符串（来自 fs stat） */
  modifiedTime: string
  /** 文件大小（字节） */
  size: number
}

interface LoadedWorkflow {
  /** 磁盘上的绝对路径 */
  path: string
  /** 当前正在编辑的工作流文档 */
  doc: Workflow
  /** 文档是否有未保存的更改 */
  dirty: boolean
}

export interface WorkflowState {
  // ── 索引（侧边栏列表）────────────────────────────────────
  entries: WorkflowIndexEntry[]
  isLoading: boolean
  error: string | null

  // ── 当前加载的工作流文档，按绝对路径键控 ─────────────────
  // 工作流可以在多个选项卡或面板中打开，共享同一份内存文档。
  loaded: Record<string, LoadedWorkflow>

  // ── 操作方法：索引管理 ─────────────────────────────────
  refresh: () => Promise<void>
  createWorkflow: (name: string) => Promise<string | null>
  renameWorkflow: (oldPath: string, newName: string) => Promise<string | null>
  deleteWorkflow: (path: string) => Promise<boolean>

  // ── 操作方法：打开/编辑 ─────────────────────────────────
  loadWorkflow: (path: string) => Promise<Workflow | null>
  saveWorkflow: (path: string) => Promise<boolean>
  updateLoaded: (path: string, patch: Partial<Workflow>) => void
  markDirty: (path: string, dirty: boolean) => void

  // ── 节点/边辅助函数（包装 updateLoaded 以方便调用） ─────────────────
  addNode: (path: string, node: WorkflowNode) => void
  updateNode: (path: string, nodeId: string, patch: Partial<WorkflowNode>) => void
  removeNode: (path: string, nodeId: string) => void
  addEdge: (path: string, edge: WorkflowEdge) => void
  removeEdge: (path: string, edgeId: string) => void
}

// ─── 辅助函数 ──────────────────────────────────────────────

/**
 * 平台感知的路径连接。渲染器默认没有 `path.join`（浏览器包中没有 Node 的 `path` 模块），但我们所有的文件
 * 路径只需要是传递给 Electron 主进程的
 * 有效字符串，后者使用 Node 进行真正的连接。到处使用 ' '/' 在 Windows 上也有效（Node 会规范化）。
 */
function joinPath(...parts: string[]): string {
  return parts
    .filter((p) => p != null && p !== '')
    .map((p, i) => (i === 0 ? p.replace(/[\\/]+$/, '') : p.replace(/^[\\/]+|[\\/]+$/g, '')))
    .join('/')
}

function basename(p: string): string {
  return p.split(/[\\/]/).pop() || p
}

function workflowDirFor(workspacePath: string): string {
  return joinPath(workspacePath, WORKFLOW_DIR_NAME)
}

/**
 * 将用户提供的工作流名称清理为对文件名安全的内容。
 * 去除路径分隔符和控制字符，折叠空白字符，并修剪。
 */
function sanitiseName(raw: string): string {
  return raw
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x1f<>:"/\\|?*]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function ensureUniqueName(entries: WorkflowIndexEntry[], desired: string): string {
  const taken = new Set(entries.map((e) => e.name.toLowerCase()))
  if (!taken.has(desired.toLowerCase())) return desired
  let i = 2
  while (taken.has(`${desired.toLowerCase()} (${i})`)) i++
  return `${desired} (${i})`
}

// ─── Store 实现 ──────────────────────────────────────────────────────

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  entries: [],
  isLoading: false,
  error: null,
  loaded: {},

  refresh: async () => {
    const workspacePath = useAssetStore.getState().workspacePath
    if (!workspacePath || !window.electronAPI) {
      set({ entries: [], isLoading: false, error: null })
      return
    }

    set({ isLoading: true, error: null })

    const dir = workflowDirFor(workspacePath)
    const result = await window.electronAPI.readDirectory(dir)

    if (!result.success) {
      // 目录不存在是"正常的空状态"，
      // 不是要显示给用户的错误 — 它是在首次保存时惰性创建的。
      const msg = String(result.error ?? '')
      const isMissing = /ENOENT|no such file|not found/i.test(msg)
      set({
        entries: [],
        isLoading: false,
        error: isMissing ? null : msg,
      })
      return
    }

    const entries: WorkflowIndexEntry[] = (result.entries || [])
      .filter((e: any) => !e.isDirectory && isWorkflowFilename(e.name))
      .map((e: any) => ({
        path: e.path,
        // 为显示名称去除 .flow.json 扩展名
        name: e.name.slice(0, -WORKFLOW_FILE_EXT.length),
        modifiedTime: e.modifiedTime,
        size: e.size,
      }))
      // 按最近修改时间排序 — 符合用户对于
      // "我正在处理什么"列表的预期。次要按字母顺序以保持稳定性。
      .sort((a: WorkflowIndexEntry, b: WorkflowIndexEntry) => {
        const t = b.modifiedTime.localeCompare(a.modifiedTime)
        return t !== 0 ? t : a.name.localeCompare(b.name)
      })

    set({ entries, isLoading: false, error: null })
  },

  createWorkflow: async (rawName: string) => {
    const workspacePath = useAssetStore.getState().workspacePath
    if (!workspacePath || !window.electronAPI) {
      set({ error: 'No workspace folder open. Open a folder first.' })
      return null
    }

    const cleanName = sanitiseName(rawName) || 'Untitled Workflow'
    const uniqueName = ensureUniqueName(get().entries, cleanName)

    const dir = workflowDirFor(workspacePath)
    // 确保 workflows/ 目录存在 — 它在首次保存时惰性创建，
    // 以便工作区在用户实际使用该功能之前不会获得空文件夹
    // 弄乱他们的树。
    const mkdirRes = await window.electronAPI.ensureDirectory(dir)
    if (!mkdirRes.success) {
      set({ error: `Failed to create workflows folder: ${mkdirRes.error}` })
      return null
    }

    const fullPath = joinPath(dir, `${uniqueName}${WORKFLOW_FILE_EXT}`)
    const doc = createEmptyWorkflow(uniqueName)
    const writeRes = await window.electronAPI.writeFile(fullPath, serialiseWorkflow(doc))
    if (!writeRes.success) {
      set({ error: `Failed to save workflow: ${writeRes.error}` })
      return null
    }

    await get().refresh()
    return fullPath
  },

  renameWorkflow: async (oldPath: string, rawNewName: string) => {
    if (!window.electronAPI) return null
    const cleanName = sanitiseName(rawNewName)
    if (!cleanName) {
      set({ error: 'Workflow name cannot be empty.' })
      return null
    }

    const dir = oldPath.substring(0, oldPath.length - basename(oldPath).length - 1)
    const newPath = joinPath(dir, `${cleanName}${WORKFLOW_FILE_EXT}`)
    if (newPath === oldPath) return oldPath

    const res = await window.electronAPI.renameFile(oldPath, newPath)
    if (!res.success) {
      set({ error: `Rename failed: ${res.error}` })
      return null
    }

    // 在 new key 下移动缓存的加载文档，更新其名称字段
    const loaded = { ...get().loaded }
    if (loaded[oldPath]) {
      const old = loaded[oldPath]
      loaded[newPath] = {
        ...old,
        path: newPath,
        doc: { ...old.doc, name: cleanName },
      }
      delete loaded[oldPath]
      set({ loaded })
    }

    await get().refresh()
    return newPath
  },

  deleteWorkflow: async (path: string) => {
    if (!window.electronAPI) return false
    const res = await window.electronAPI.deleteFile(path)
    if (!res.success) {
      set({ error: `Delete failed: ${res.error}` })
      return false
    }

    const loaded = { ...get().loaded }
    delete loaded[path]
    set({ loaded })

    await get().refresh()
    return true
  },

  loadWorkflow: async (path: string) => {
    if (!window.electronAPI) return null

    // 已经加载？返回缓存的副本。这允许多个
    // 组件（侧边栏预览、画布选项卡等）共享单个
    // 内存中文档。
    const cached = get().loaded[path]
    if (cached) return cached.doc

    const res = await window.electronAPI.readFile(path)
    if (!res.success || res.content === undefined) {
      set({ error: `读取工作流失败: ${res.error ?? '未知错误'}` })
      return null
    }

    try {
      const fallbackName = basename(path).replace(WORKFLOW_FILE_EXT, '')
      const doc = parseWorkflow(res.content, fallbackName)
      set((state) => ({
        loaded: {
          ...state.loaded,
          [path]: { path, doc, dirty: false },
        },
      }))
      return doc
    } catch (err) {
      set({ error: `解析工作流失败: ${(err as Error).message}` })
      return null
    }
  },

  saveWorkflow: async (path: string) => {
    if (!window.electronAPI) return false
    const loaded = get().loaded[path]
    if (!loaded) return false

    const res = await window.electronAPI.writeFile(path, serialiseWorkflow(loaded.doc))
    if (!res.success) {
      set({ error: `保存失败: ${res.error}` })
      return false
    }

    set((state) => ({
      loaded: {
        ...state.loaded,
        [path]: { ...state.loaded[path], dirty: false },
      },
    }))

    // 刷新索引，以便侧边栏获取新的修改时间
    await get().refresh()
    return true
  },

  updateLoaded: (path, patch) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: { ...cur.doc, ...patch },
            dirty: true,
          },
        },
      }
    })
  },

  markDirty: (path, dirty) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: { ...cur, dirty },
        },
      }
    })
  },

  addNode: (path, node) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: { ...cur.doc, nodes: [...cur.doc.nodes, node] },
            dirty: true,
          },
        },
      }
    })
  },

  updateNode: (path, nodeId, patch) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: {
              ...cur.doc,
              nodes: cur.doc.nodes.map((n) =>
                n.id === nodeId ? { ...n, ...patch } : n
              ),
            },
            dirty: true,
          },
        },
      }
    })
  },

  removeNode: (path, nodeId) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: {
              ...cur.doc,
              nodes: cur.doc.nodes.filter((n) => n.id !== nodeId),
              // 级联：移除连接到此节点的所有边
              edges: cur.doc.edges.filter(
                (e) => e.source !== nodeId && e.target !== nodeId
              ),
            },
            dirty: true,
          },
        },
      }
    })
  },

  addEdge: (path, edge) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      // 去重：完全相同的连接不应该堆积
      const alreadyExists = cur.doc.edges.some(
        (e) =>
          e.source === edge.source &&
          e.sourceHandle === edge.sourceHandle &&
          e.target === edge.target &&
          e.targetHandle === edge.targetHandle
      )
      if (alreadyExists) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: { ...cur.doc, edges: [...cur.doc.edges, edge] },
            dirty: true,
          },
        },
      }
    })
  },

  removeEdge: (path, edgeId) => {
    set((state) => {
      const cur = state.loaded[path]
      if (!cur) return state
      return {
        loaded: {
          ...state.loaded,
          [path]: {
            ...cur,
            doc: {
              ...cur.doc,
              edges: cur.doc.edges.filter((e) => e.id !== edgeId),
            },
            dirty: true,
          },
        },
      }
    })
  },
}))

// ─── 工作区更改时自动刷新 ─────────────────────────
// 钩住 asset store：每当工作区目录更改时，工作流列表需要从新工作区的
// workflows/ 文件夹重新加载。使用 subscribe() 而不是 React hook，
// 以便无论哪些组件被挂载，这都会运行。
useAssetStore.subscribe((state, prev) => {
  if (state.workspacePath !== prev.workspacePath) {
    useWorkflowStore.getState().refresh().catch(() => {
      /* swallowed — the store already stashes the error for UI */
    })
  }
})
