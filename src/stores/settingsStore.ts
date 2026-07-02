import { create } from 'zustand'
import { BUILTIN_BASEMAPS } from '@/services/geo'

// Supported protocol types — only openai and anthropic
export type ProtocolType = 'openai' | 'anthropic'

export interface ModelPreset {
  id: string
  name: string
  provider: string
  protocol: ProtocolType
  modelName: string
  apiKey: string
  baseURL: string
}

interface SettingsState {
  model: {
    protocol: ProtocolType
    modelName: string
    apiKey: string
    baseURL: string
    temperature: number
    maxTokens: number
    reasoningEffort: 'low' | 'medium' | 'high'
    presets: ModelPreset[]
  }
  python: {
    mode: 'auto' | 'manual'
    path: string
  }
  appearance: {
    theme: 'dark' | 'light' | 'system'
    language: 'en' | 'zh'
    fontSize: number
    basemapId: string
    customTileUrl: string
    showMapLabels: boolean
  }
  agent: {
    maxIterations: number
    maxConsecutiveMistakes: number
    codeExecutionTimeout: number
    requireConfirmation: boolean
    autoRenderResults: boolean
    useAutoCondense: boolean
    customInstructions: string
    debugMode: boolean
  }

  // 操作方法
  updateModel: (updates: Partial<SettingsState['model']>) => void
  updatePython: (updates: Partial<SettingsState['python']>) => void
  updateAppearance: (updates: Partial<SettingsState['appearance']>) => void
  updateAgent: (updates: Partial<SettingsState['agent']>) => void
  loadFromElectron: () => Promise<void>
  saveToElectron: () => Promise<void>
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  // 默认值
  model: {
    protocol: 'openai' as ProtocolType,
    modelName: 'gpt-4o',
    apiKey: '',
    baseURL: '',
    temperature: 0,
    maxTokens: 4096,
    reasoningEffort: 'medium' as const,
    presets: [] as ModelPreset[],
  },
  python: {
    mode: 'auto',
    path: '',
  },
  appearance: {
    theme: 'system',
    language: 'en',
    fontSize: 14,
    basemapId: 'osm-streets',
    customTileUrl: '',
    showMapLabels: false,
  },
  agent: {
    maxIterations: 25,
    maxConsecutiveMistakes: 3,
    codeExecutionTimeout: 60,
    requireConfirmation: true,
    autoRenderResults: true,
    useAutoCondense: true,
    customInstructions: '',
    debugMode: false,
  },

  // 操作方法
  updateModel: (updates) =>
    set((state) => ({
      model: { ...state.model, ...updates },
    })),

  updatePython: (updates) =>
    set((state) => ({
      python: { ...state.python, ...updates },
    })),

  updateAppearance: (updates) =>
    set((state) => ({
      appearance: { ...state.appearance, ...updates },
    })),

  updateAgent: (updates) =>
    set((state) => ({
      agent: { ...state.agent, ...updates },
    })),

  loadFromElectron: async () => {
    if (typeof window !== 'undefined' && window.electronAPI) {
      try {
        const settings = await window.electronAPI.getSettings()
        set({
          model: { ...get().model, ...settings.model },
          python: { ...get().python, ...settings.python },
          appearance: { ...get().appearance, ...settings.appearance },
          agent: { ...get().agent, ...settings.agent },
        })
      } catch (error) {
        console.error('[settingsStore] 加载设置失败:', error)
      }
    }
  },

  saveToElectron: async () => {
    if (typeof window !== 'undefined' && window.electronAPI) {
      const state = get()
      try {
        await window.electronAPI.setSetting('model', state.model)
        await window.electronAPI.setSetting('python', state.python)
        await window.electronAPI.setSetting('appearance', state.appearance)
        await window.electronAPI.setSetting('agent', state.agent)
      } catch (error) {
        console.error('[settingsStore] 保存设置失败:', error)
      }
    }
  },
}))
