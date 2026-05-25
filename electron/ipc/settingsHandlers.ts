import { ipcMain, app, dialog } from 'electron'
import { readFile, writeFile, mkdir } from 'fs/promises'
import { join } from 'path'
import { existsSync } from 'fs'

const SETTINGS_FILE = 'settings.json'
const PROJECTS_FILE = 'projects.json'

// ─── Project types ─────────────────────────────────────────────
export interface ProjectEntry {
  id: string
  name: string
  path: string
  createdAt: number
  lastOpenedAt: number
}

interface ProjectsData {
  projects: ProjectEntry[]
  lastProjectId?: string
}

const DEFAULT_PROJECTS: ProjectsData = { projects: [] }

// ─── Settings types ────────────────────────────────────────────
interface AppSettings {
  model: {
    provider: string
    modelName: string
    apiKey?: string
    baseURL?: string
    temperature: number
    maxTokens: number
  }
  python: {
    mode: 'auto' | 'manual'
    path?: string
  }
  appearance: {
    theme: 'dark' | 'light' | 'system'
    language: 'en' | 'zh'
    fontSize: number
    mapStyle: 'streets' | 'satellite' | 'dark' | 'light'
  }
  agent: {
    maxIterations: number
    codeExecutionTimeout: number
    requireConfirmation: boolean
    autoRenderResults: boolean
  }
}

const DEFAULT_SETTINGS: AppSettings = {
  model: {
    provider: 'openai',
    modelName: 'gpt-4o',
    temperature: 0.7,
    maxTokens: 4096,
  },
  python: {
    mode: 'auto',
  },
  appearance: {
    theme: 'dark',
    language: 'en',
    fontSize: 14,
    mapStyle: 'dark',
  },
  agent: {
    maxIterations: 10,
    codeExecutionTimeout: 60,
    requireConfirmation: true,
    autoRenderResults: true,
  },
}

function getSettingsPath(): string {
  return join(app.getPath('userData'), SETTINGS_FILE)
}

function getProjectsPath(): string {
  return join(app.getPath('userData'), PROJECTS_FILE)
}

async function loadSettings(): Promise<AppSettings> {
  const settingsPath = getSettingsPath()

  if (!existsSync(settingsPath)) {
    return { ...DEFAULT_SETTINGS }
  }

  try {
    const content = await readFile(settingsPath, 'utf-8')
    const saved = JSON.parse(content)
    return deepMerge(DEFAULT_SETTINGS, saved)
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

async function saveSettings(settings: AppSettings): Promise<void> {
  const settingsPath = getSettingsPath()
  const dir = join(app.getPath('userData'))

  if (!existsSync(dir)) {
    await mkdir(dir, { recursive: true })
  }

  await writeFile(settingsPath, JSON.stringify(settings, null, 2), 'utf-8')
}

// ─── Projects persistence ──────────────────────────────────────
export async function loadProjects(): Promise<ProjectsData> {
  const projectsPath = getProjectsPath()
  if (!existsSync(projectsPath)) {
    return { ...DEFAULT_PROJECTS }
  }
  try {
    const content = await readFile(projectsPath, 'utf-8')
    return JSON.parse(content) as ProjectsData
  } catch {
    return { ...DEFAULT_PROJECTS }
  }
}

export async function saveProjects(data: ProjectsData): Promise<void> {
  const projectsPath = getProjectsPath()
  await writeFile(projectsPath, JSON.stringify(data, null, 2), 'utf-8')
}

function deepMerge(target: any, source: any): any {
  const result = { ...target }
  for (const key of Object.keys(source)) {
    if (
      source[key] &&
      typeof source[key] === 'object' &&
      !Array.isArray(source[key]) &&
      target[key] &&
      typeof target[key] === 'object'
    ) {
      result[key] = deepMerge(target[key], source[key])
    } else {
      result[key] = source[key]
    }
  }
  return result
}

/**
 * Register settings IPC handlers.
 */
export function registerSettingsHandlers(): void {
  ipcMain.handle('settings:get', async () => {
    return await loadSettings()
  })

  ipcMain.handle('settings:set', async (_event, key: string, value: any) => {
    const settings = await loadSettings()

    // Support dot-notation keys like "model.provider"
    const keys = key.split('.')
    let current: any = settings
    for (let i = 0; i < keys.length - 1; i++) {
      if (current[keys[i]] === undefined) {
        current[keys[i]] = {}
      }
      current = current[keys[i]]
    }
    current[keys[keys.length - 1]] = value

    await saveSettings(settings)
    return { success: true }
  })

  ipcMain.handle('app:version', () => {
    return app.getVersion()
  })

  // ─── Project management ────────────────────────────────────────
  ipcMain.handle('projects:list', async () => {
    return await loadProjects()
  })

  ipcMain.handle('projects:create', async (_event, name: string, path: string) => {
    const data = await loadProjects()
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
    const entry: ProjectEntry = {
      id,
      name,
      path,
      createdAt: Date.now(),
      lastOpenedAt: Date.now(),
    }
    data.projects.unshift(entry)
    data.lastProjectId = id
    await saveProjects(data)
    return entry
  })

  ipcMain.handle('projects:open', async (_event, id: string) => {
    const data = await loadProjects()
    const project = data.projects.find(p => p.id === id)
    if (!project) return { error: 'Project not found' }
    project.lastOpenedAt = Date.now()
    data.lastProjectId = id
    await saveProjects(data)
    return project
  })

  ipcMain.handle('projects:rename', async (_event, id: string, newName: string) => {
    const data = await loadProjects()
    const project = data.projects.find(p => p.id === id)
    if (!project) return { error: 'Project not found' }
    project.name = newName
    await saveProjects(data)
    return project
  })

  ipcMain.handle('projects:delete', async (_event, id: string) => {
    const data = await loadProjects()
    data.projects = data.projects.filter(p => p.id !== id)
    if (data.lastProjectId === id) data.lastProjectId = undefined
    await saveProjects(data)
    return { success: true }
  })

  ipcMain.handle('projects:browse-folder', async (event) => {
    // Find the BrowserWindow that sent this request and use it as parent
    // so the dialog appears on top of the always-on-top loading window.
    const { BrowserWindow: BW } = require('electron')
    const sender = event.sender
    const parentWin = BW.getAllWindows().find((w: any) => w.webContents === sender)
    const dialogOpts: Electron.OpenDialogOptions = {
      properties: ['openDirectory', 'createDirectory'],
      title: '选择项目文件夹',
    }
    const result = parentWin
      ? await dialog.showOpenDialog(parentWin, dialogOpts)
      : await dialog.showOpenDialog(dialogOpts)
    if (result.canceled || !result.filePaths.length) return { canceled: true }
    return { path: result.filePaths[0] }
  })
}
