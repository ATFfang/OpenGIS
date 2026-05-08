import { ipcMain, app } from 'electron'
import { readFile, writeFile, mkdir } from 'fs/promises'
import { join } from 'path'
import { existsSync } from 'fs'

const SETTINGS_FILE = 'settings.json'

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

async function loadSettings(): Promise<AppSettings> {
  const settingsPath = getSettingsPath()

  if (!existsSync(settingsPath)) {
    return { ...DEFAULT_SETTINGS }
  }

  try {
    const content = await readFile(settingsPath, 'utf-8')
    const saved = JSON.parse(content)
    // Deep merge with defaults to handle new settings fields
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
}
