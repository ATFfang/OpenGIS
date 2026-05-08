/**
 * View Store — 管理打开的编辑器/查看器选项卡和面板的状态。
 *
 * 管理：
 * - 打开的文件选项卡（代码查看器、文本文件等）
 * - 活动选项卡跟踪
 * - 面板布局（分割方向、哪些面板可见）
 * - 与选项卡关联的代执行结果
 */
import { create } from 'zustand'

// ─── 类型定义 ──────────────────────────────────────────────

export type ViewTabType = 'map' | 'code' | 'text' | 'image'

export interface ViewTab {
  /** 唯一选项卡 ID */
  id: string
  /** 选项卡显示标题 */
  title: string
  /** 内容类型 */
  type: ViewTabType
  /** 文件路径（如果从文件打开） */
  filePath?: string
  /** 文件内容（文本/代码） */
  content?: string
  /** 语法高亮语言（例如 'python'、'typescript'） */
  language?: string
  /** 是否固定此选项卡（不会自动关闭） */
  pinned?: boolean
  /** 代执行结果（用于代码选项卡） */
  executionResult?: CodeExecutionResult
  /** 代是否正在执行 */
  isExecuting?: boolean
}

export interface CodeExecutionResult {
  success: boolean
  stdout: string
  stderr: string
  return_value: any
  error?: string
  error_type?: string
  figures?: string[]
  execution_time_ms: number
}

export type SplitDirection = 'horizontal' | 'vertical'

export interface ViewState {
  // 主内容区域中打开的选项卡
  tabs: ViewTab[]
  // 当前活动选项卡 ID
  activeTabId: string
  // 主内容区域的分割方向
  splitDirection: SplitDirection
  // 是否可见代码面板
  showCodePanel: boolean
  // 是否并排显示地图 + 代码
  showSplitView: boolean

  // 操作方法
  openTab: (tab: Omit<ViewTab, 'id'>) => string
  closeTab: (tabId: string) => void
  setActiveTab: (tabId: string) => void
  updateTabContent: (tabId: string, content: string) => void
  updateTabExecutionResult: (tabId: string, result: CodeExecutionResult) => void
  setTabExecuting: (tabId: string, executing: boolean) => void
  closeAllTabs: () => void
  toggleSplitView: () => void
  setSplitDirection: (direction: SplitDirection) => void
  setShowCodePanel: (show: boolean) => void
  openFileAsTab: (filePath: string, fileName: string, content: string, language?: string) => string
}

// ─── 唯一 ID 计数器 ─────────────────────────────────

let tabIdCounter = 0
function nextTabId(): string {
  return `tab-${++tabIdCounter}`
}

// ─── Store 实现 ──────────────────────────────────────

export const useViewStore = create<ViewState>((set, get) => ({
  tabs: [],
  activeTabId: 'map',
  splitDirection: 'horizontal',
  showCodePanel: false,
  showSplitView: false,

  openTab: (tab) => {
    const id = nextTabId()
    const newTab: ViewTab = { ...tab, id }

    set((state) => ({
      tabs: [...state.tabs, newTab],
      activeTabId: id,
      showCodePanel: true,
    }))

    return id
  },

  closeTab: (tabId) => {
    set((state) => {
      const newTabs = state.tabs.filter((t) => t.id !== tabId)
      let newActiveId = state.activeTabId

      // 如果关闭活动选项卡，切换到上一个或下一个选项卡
      if (tabId === state.activeTabId) {
        if (newTabs.length > 0) {
          const closedIndex = state.tabs.findIndex((t) => t.id === tabId)
          const newActiveIndex = Math.min(closedIndex, newTabs.length - 1)
          newActiveId = newTabs[newActiveIndex].id
        } else {
          newActiveId = 'map'
        }
      }

      return {
        tabs: newTabs,
        activeTabId: newActiveId,
        showCodePanel: newTabs.length > 0,
      }
    })
  },

  setActiveTab: (tabId) => set({ activeTabId: tabId }),

  updateTabContent: (tabId, content) =>
    set((state) => ({
      tabs: state.tabs.map((t) => (t.id === tabId ? { ...t, content } : t)),
    })),

  updateTabExecutionResult: (tabId, result) =>
    set((state) => ({
      tabs: state.tabs.map((t) =>
        t.id === tabId ? { ...t, executionResult: result, isExecuting: false } : t
      ),
    })),

  setTabExecuting: (tabId, executing) =>
    set((state) => ({
      tabs: state.tabs.map((t) =>
        t.id === tabId ? { ...t, isExecuting: executing } : t
      ),
    })),

  closeAllTabs: () =>
    set({
      tabs: [],
      activeTabId: 'map',
      showCodePanel: false,
    }),

  toggleSplitView: () =>
    set((state) => ({ showSplitView: !state.showSplitView })),

  setSplitDirection: (direction) => set({ splitDirection: direction }),

  setShowCodePanel: (show) => set({ showCodePanel: show }),

  openFileAsTab: (filePath, fileName, content, language) => {
    const state = get()

    // 检查文件是否已经打开
    const existingTab = state.tabs.find((t) => t.filePath === filePath)
    if (existingTab) {
      set({ activeTabId: existingTab.id, showCodePanel: true })
      return existingTab.id
    }

    // ── .flow.json → 工作流编辑器（无需预加载内容） ──
    if (/\.flow\.json$/i.test(fileName) || /\.flow\.json$/i.test(filePath)) {
      const displayName = fileName.replace(/\.flow\.json$/i, '') || 'Workflow'
      return state.openTab({
        title: displayName,
        type: 'code',
        filePath,
        language: 'workflow',
      })
    }

    // 从文件扩展名检测语言
    const ext = fileName.includes('.') ? fileName.split('.').pop()!.toLowerCase() : ''
    const detectedLanguage = language || detectLanguage(ext)

    const tabType: ViewTabType = CODE_EXTENSIONS.has(ext) ? 'code' : 'text'

    return state.openTab({
      title: fileName,
      type: tabType,
      filePath,
      content,
      language: detectedLanguage,
    })
  },
}))

// ─── Helpers ──────────────────────────────────────────────────────

const CODE_EXTENSIONS = new Set([
  'py', 'js', 'ts', 'tsx', 'jsx', 'r', 'ipynb',
  'json', 'geojson', 'yaml', 'yml', 'toml', 'xml', 'html', 'css', 'scss',
  'sh', 'bash', 'zsh', 'bat', 'ps1',
  'sql', 'md', 'rst', 'txt', 'log', 'ini', 'cfg', 'conf',
  // CSV / TSV 文件也被标签化并使用专用网格查看器打开，
  // 但我们将其分类为 `code` 类型的选项卡，以便现有的选项卡栏
  // 和分割视图逻辑将其视为可打开的文件，而不是
  // 地图/图像面板。
  'csv', 'tsv',
])

function detectLanguage(ext: string): string {
  const langMap: Record<string, string> = {
    py: 'python',
    js: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    jsx: 'javascript',
    r: 'r',
    json: 'json',
    // .geojson 只是具有已知架构的 JSON — 重用 JSON 语法高亮
    // 但标记 language='geojson'，以便 CodeTabContent 可以特殊处理，如果
    // 我们曾经想要一个支持架构的查看器。
    geojson: 'geojson',
    yaml: 'yaml',
    yml: 'yaml',
    toml: 'toml',
    xml: 'xml',
    html: 'html',
    css: 'css',
    scss: 'scss',
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    bat: 'batch',
    ps1: 'powershell',
    sql: 'sql',
    md: 'markdown',
    rst: 'restructuredtext',
    txt: 'text',
    log: 'text',
    ini: 'ini',
    cfg: 'ini',
    conf: 'ini',
    csv: 'csv',
    tsv: 'tsv',
  }
  return langMap[ext] || 'text'
}
