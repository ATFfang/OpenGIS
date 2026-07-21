import { Fragment, useState, useCallback, useEffect, useRef, useMemo } from 'react'
import {
  Search,
  X,
  Bot,
  Palette,
  Terminal,
  Cpu,
  Gauge,
  ChevronDown,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Plus,
  Download,
  Upload,
} from 'lucide-react'
import { useT } from '@/i18n'
import { useSettingsStore } from '@/stores/settingsStore'
import { useRunsStore } from '@/stores/runsStore'
import type { ProtocolType, ModelPreset } from '@/stores/settingsStore'
import type { RunDetail, RunLLMUsageRecord, RunSummary } from '@/stores/runsStore'
import { BUILTIN_BASEMAPS } from '@/services/geo'
import { useMapStore } from '@/stores/mapStore'
import { mapEngine } from '@/features/map/engine/MapEngine'
import { pythonClient } from '@/services/pythonClient'
import { iconMap, PROVIDERS, type ProviderConfig } from './providerMap'
import {
  SettingItem,
  SettingInput,
  SettingNumber,
  SettingSelect,
  SettingCheckbox,
  SettingSlider,
  SettingTextArea,
  SettingSection,
} from './components/SettingItem'

// ─── Protocol options ──────────────────────────────────────────

// Protocol options are now generated inside the component to use translations

// ─── Navigation sections ───────────────────────────────────────

interface NavSection {
  id: string
  label: string
  icon: React.ElementType
  keywords: string[]
}

// ─── SettingsView ──────────────────────────────────────────────

export function SettingsView() {
  const t = useT()

  const PROTOCOL_OPTIONS: { value: ProtocolType; label: string; description: string }[] = [
    { value: 'openai', label: t.settings.openaiProtocol, description: t.settings.openaiProtocolDesc },
    { value: 'anthropic', label: t.settings.anthropicProtocol, description: t.settings.anthropicProtocolDesc },
  ]

  const NAV_SECTIONS: NavSection[] = [
    { id: 'model', label: t.settings.model, icon: Bot, keywords: ['llm', 'api', 'key', 'protocol', 'model', 'temperature', 'token'] },
    { id: 'agent', label: t.settings.agent, icon: Cpu, keywords: ['agent', 'iteration', 'confirmation', 'timeout', 'instructions'] },
    { id: 'promptCache', label: t.settings.promptCacheTest, icon: Gauge, keywords: ['cache', 'prompt cache', 'deepseek', 'usage', 'token', 'section', 'llm'] },
    { id: 'appearance', label: t.settings.appearance, icon: Palette, keywords: ['theme', 'font', 'language', 'map', 'dark', 'light'] },
    { id: 'python', label: t.settings.python, icon: Terminal, keywords: ['python', 'path', 'environment', 'interpreter'] },
  ]
  const [searchQuery, setSearchQuery] = useState('')
  const [activeSection, setActiveSection] = useState('model')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
  const [pythonBackendStatus, setPythonBackendStatus] = useState<'stopped' | 'starting' | 'ready' | 'error'>('stopped')
  const [pythonBackendError, setPythonBackendError] = useState<string>('')
  const [pythonRestarting, setPythonRestarting] = useState(false)
  const [showSaveAsNew, setShowSaveAsNew] = useState(false)
  const [newPresetName, setNewPresetName] = useState('')
  const [showProviderDropdown, setShowProviderDropdown] = useState(false)
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [loadedPresetId, setLoadedPresetId] = useState<string | null>(null)
  const [promptCacheTestEnabled, setPromptCacheTestEnabled] = useState(() => {
    try {
      return window.localStorage.getItem('opengis.settings.promptCacheTest.enabled') === '1'
    } catch {
      return false
    }
  })
  const [promptCacheTestExpanded, setPromptCacheTestExpanded] = useState(() => {
    try {
      return window.localStorage.getItem('opengis.settings.promptCacheTest.expanded') === '1'
    } catch {
      return false
    }
  })
  const [promptCacheClearedAt, setPromptCacheClearedAt] = useState(() => {
    try {
      return Number(window.localStorage.getItem('opengis.settings.promptCacheTest.clearedAt') || 0)
    } catch {
      return 0
    }
  })

  const contentRef = useRef<HTMLDivElement>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const providerDropdownRef = useRef<HTMLDivElement>(null)

  // Close provider dropdown on outside click
  useEffect(() => {
    if (!showProviderDropdown) return
    const handler = (e: MouseEvent) => {
      if (providerDropdownRef.current && !providerDropdownRef.current.contains(e.target as Node)) {
        setShowProviderDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showProviderDropdown])

  const {
    model, appearance, agent,
    updateModel, updateAppearance, updateAgent,
    loadFromElectron, saveToElectron,
  } = useSettingsStore()
  const runs = useRunsStore((s) => s.runs)
  const runsLoaded = useRunsStore((s) => s.loaded)
  const refreshRuns = useRunsStore((s) => s.refresh)
  const getRunDetail = useRunsStore((s) => s.getDetail)
  const runDetails = useRunsStore((s) => s.details)

  // Load settings on mount
  useEffect(() => {
    loadFromElectron()
  }, [loadFromElectron])

  useEffect(() => {
    if (!promptCacheTestEnabled) return
    if (!runsLoaded) {
      refreshRuns(12).catch(() => {})
    }
  }, [promptCacheTestEnabled, runsLoaded, refreshRuns])

  useEffect(() => {
    try {
      window.localStorage.setItem('opengis.settings.promptCacheTest.enabled', promptCacheTestEnabled ? '1' : '0')
    } catch {}
  }, [promptCacheTestEnabled])

  useEffect(() => {
    try {
      window.localStorage.setItem('opengis.settings.promptCacheTest.expanded', promptCacheTestExpanded ? '1' : '0')
    } catch {}
  }, [promptCacheTestExpanded])

  useEffect(() => {
    try {
      window.localStorage.setItem('opengis.settings.promptCacheTest.clearedAt', String(promptCacheClearedAt || 0))
    } catch {}
  }, [promptCacheClearedAt])

  useEffect(() => {
    if (!promptCacheTestEnabled) return
    const timer = window.setInterval(() => {
      refreshRuns(12).catch(() => {})
    }, 10000)
    return () => window.clearInterval(timer)
  }, [promptCacheTestEnabled, refreshRuns])

  // Initialize selectedProviderId from current baseURL
  useEffect(() => {
    if (model.baseURL && !selectedProviderId) {
      const match = PROVIDERS.find(p => p.baseURL === model.baseURL)
      if (match) setSelectedProviderId(match.id)
    }
  }, [model.baseURL]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load user instructions from backend (source of truth)
  useEffect(() => {
    let cancelled = false
    pythonClient.send('user_instructions.get', {})
      .then((res: any) => {
        if (!cancelled && res?.content) {
          useSettingsStore.getState().updateAgent({ customInstructions: res.content })
        }
      })
      .catch(() => { /* backend may not be ready yet */ })
    return () => { cancelled = true }
  }, [])

  // Monitor Python backend status
  useEffect(() => {
    // Fetch initial status
    window.electronAPI?.getPythonStatus().then((status) => {
      if (status) {
        setPythonBackendStatus(status.status)
        setPythonBackendError(status.error || '')
      }
    }).catch(() => {})

    // Listen for status changes
    const unsubscribe = window.electronAPI?.onPythonStatusChanged((status) => {
      setPythonBackendStatus(status.status)
      setPythonBackendError(status.error || '')
      if (status.status === 'ready' || status.status === 'error') {
        setPythonRestarting(false)
      }
    })

    return unsubscribe ?? (() => {})
  }, [])

  // Restart Python backend
  const handleRestartPython = useCallback(async () => {
    if (!window.electronAPI) return
    setPythonRestarting(true)
    setPythonBackendError('')
    try {
      // Flush any pending debounced save so the restart reads the latest settings
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current)
        saveTimeoutRef.current = undefined
      }
      await saveToElectron()

      const status = await window.electronAPI.restartPython()
      if (status) {
        setPythonBackendStatus(status.status)
        setPythonBackendError(status.error || '')
        if (status.status === 'ready' || status.status === 'error') {
          setPythonRestarting(false)
        }
      }
    } catch (err: any) {
      setPythonBackendStatus('error')
      setPythonBackendError(err.message || String(err))
      setPythonRestarting(false)
    }
  }, [saveToElectron])

  // Auto-save with debounce
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout>>()
  const handleSave = useCallback(() => {
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    setSaveStatus('saving')
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        await saveToElectron()
        setSaveStatus('saved')
        setTimeout(() => setSaveStatus('idle'), 2000)
      } catch {
        setSaveStatus('error')
        setTimeout(() => setSaveStatus('idle'), 3000)
      }
    }, 500)
  }, [saveToElectron])

  // Wrap update functions to auto-save
  const setModel = useCallback(
    (updates: Partial<typeof model>) => {
      updateModel(updates)
      handleSave()
    },
    [updateModel, handleSave]
  )

  const setAgent = useCallback(
    (updates: Partial<typeof agent>) => {
      updateAgent(updates)
      handleSave()
    },
    [updateAgent, handleSave]
  )

  const setAppearance = useCallback(
    (updates: Partial<typeof appearance>) => {
      updateAppearance(updates)
      handleSave()
    },
    [updateAppearance, handleSave]
  )

  // Preset helpers
  const presets = model.presets || []

  // Resolve icon for a provider id
  const loadProviderIcon = useCallback((providerId?: string) => {
    if (!providerId) return null
    return iconMap[providerId] || null
  }, [])

  const handleProviderSelect = useCallback(
    (provider: ProviderConfig) => {
      // Record user's explicit selection so that manually editing baseURL later
      // won't reset the displayed provider label to "Custom".
      setSelectedProviderId(provider.id)
      const updates: Partial<typeof model> = {
        protocol: provider.protocol,
      }
      if (!model.baseURL) {
        updates.baseURL = provider.baseURL
      }
      if (!model.modelName) {
        updates.modelName = provider.defaultModel || ''
      }
      setModel(updates)
      setShowProviderDropdown(false)
    },
    [setModel, model.baseURL, model.modelName],
  )

  const loadPreset = useCallback(
    (preset: ModelPreset) => {
      setModel({
        protocol: preset.protocol,
        modelName: preset.modelName,
        apiKey: preset.apiKey,
        baseURL: preset.baseURL,
      })
      setLoadedPresetId(preset.id)
      // Restore provider selection from preset
      setSelectedProviderId(preset.provider || null)
    },
    [setModel],
  )

  const updateActivePreset = useCallback(() => {
    if (!loadedPresetId) return
    setModel({
      presets: presets.map((p) =>
        p.id === loadedPresetId
          ? { ...p, protocol: model.protocol, modelName: model.modelName, apiKey: model.apiKey, baseURL: model.baseURL }
          : p,
      ),
    })
  }, [loadedPresetId, presets, model.protocol, model.modelName, model.apiKey, model.baseURL, setModel])

  const createNewPreset = useCallback(() => {
    const name = newPresetName.trim()
    if (!name) return
    const matchedProvider = PROVIDERS.find(
      (p) => p.baseURL && model.baseURL && p.baseURL === model.baseURL,
    )
    const newPreset: ModelPreset = {
      id: crypto.randomUUID(),
      name,
      provider: matchedProvider?.id || '',
      protocol: model.protocol,
      modelName: model.modelName,
      apiKey: model.apiKey,
      baseURL: model.baseURL,
    }
    setModel({ presets: [...presets, newPreset] })
    setLoadedPresetId(newPreset.id)
    setNewPresetName('')
    setShowSaveAsNew(false)
  }, [newPresetName, model.protocol, model.modelName, model.apiKey, model.baseURL, presets, setModel])

  const deletePreset = useCallback(
    (id: string) => {
      setModel({ presets: presets.filter((p) => p.id !== id) })
      if (loadedPresetId === id) setLoadedPresetId(null)
    },
    [presets, loadedPresetId, setModel],
  )

  // ─── Import / Export model config as JSON ───────────────────────
  const exportModelConfig = useCallback(() => {
    const config = {
      protocol: model.protocol,
      modelName: model.modelName,
      apiKey: model.apiKey,
      baseURL: model.baseURL,
      temperature: model.temperature,
      maxTokens: model.maxTokens,
      reasoningEffort: model.reasoningEffort,
    }
    const json = JSON.stringify(config, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `model-config-${model.modelName || 'default'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [model])

  const exportPreset = useCallback((preset: ModelPreset) => {
    const config = {
      name: preset.name,
      protocol: preset.protocol,
      modelName: preset.modelName,
      apiKey: preset.apiKey,
      baseURL: preset.baseURL,
    }
    const json = JSON.stringify(config, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `preset-${preset.name || 'config'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  const importModelConfig = useCallback(() => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json'
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const config = JSON.parse(text)
        const updates: Partial<typeof model> = {}
        if (config.protocol && (config.protocol === 'openai' || config.protocol === 'anthropic')) {
          updates.protocol = config.protocol
        }
        if (typeof config.modelName === 'string') updates.modelName = config.modelName
        if (typeof config.apiKey === 'string') updates.apiKey = config.apiKey
        if (typeof config.baseURL === 'string') updates.baseURL = config.baseURL
        if (typeof config.temperature === 'number') updates.temperature = config.temperature
        if (typeof config.maxTokens === 'number') updates.maxTokens = config.maxTokens
        if (config.reasoningEffort && ['low', 'medium', 'high'].includes(config.reasoningEffort)) {
          updates.reasoningEffort = config.reasoningEffort
        }
        setModel(updates)
      } catch (err) {
        console.error('[Settings] Failed to import model config:', err)
      }
    }
    input.click()
  }, [setModel])

  // Scroll to section
  const scrollToSection = useCallback((sectionId: string) => {
    setActiveSection(sectionId)
    const el = sectionRefs.current[sectionId]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [])

  // Intersection observer for active section tracking
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id.replace('section-', ''))
          }
        }
      },
      { root: contentRef.current, rootMargin: '-20% 0px -70% 0px', threshold: 0 }
    )

    for (const section of NAV_SECTIONS) {
      const el = sectionRefs.current[section.id]
      if (el) observer.observe(el)
    }

    return () => observer.disconnect()
  }, [])

  // Sync showMapLabels setting to map on load and when changed
  useEffect(() => {
    const checked = appearance.showMapLabels
    const store = useMapStore.getState()
    store.setLabelsVisible(checked)

    const currentBasemap = store.basemap
    // Raster basemaps: switch to the appropriate variant
    if (currentBasemap.type === 'raster-tiles') {
      const targetId = checked ? 'carto-voyager' : 'carto-voyager-nolabels'
      const target = BUILTIN_BASEMAPS.find((b) => b.id === targetId)
      if (target && target.id !== currentBasemap.id) {
        store.setBasemap(target)
      }
      return
    }
    // Vector basemaps: try -nolabels variant
    const currentId = currentBasemap.id
    if (!checked) {
      const noLabelsId = currentId + '-nolabels'
      const noLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === noLabelsId)
      if (noLabelsBasemap) {
        store.setBasemap(noLabelsBasemap)
        return
      }
    } else if (currentId.endsWith('-nolabels')) {
      const withLabelsId = currentId.replace('-nolabels', '')
      const withLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === withLabelsId)
      if (withLabelsBasemap) {
        store.setBasemap(withLabelsBasemap)
        return
      }
    }
    // Fallback: toggle symbol layers directly via MapEngine
    const applyToMap = () => {
      const map = mapEngine.getMap()
      if (map && map.isStyleLoaded()) {
        mapEngine.setLabelsVisible(checked)
      }
    }
    applyToMap()
    const map = mapEngine.getMap()
    if (map && !map.isStyleLoaded()) {
      map.once('style.load', () => {
        mapEngine.setLabelsVisible(checked)
      })
    }
  }, [appearance.showMapLabels])

  // Filter sections by search
  const filteredSections = useMemo(() => {
    if (!searchQuery.trim()) return NAV_SECTIONS
    const q = searchQuery.toLowerCase()
    return NAV_SECTIONS.filter(
      (s) =>
        s.label.toLowerCase().includes(q) ||
        s.keywords.some((k) => k.includes(q))
    )
  }, [searchQuery])

  const visiblePromptCacheRuns = useMemo(
    () => runs.filter((run) => runCreatedAtMs(run.created_at) > promptCacheClearedAt),
    [runs, promptCacheClearedAt],
  )
  const latestRun = visiblePromptCacheRuns[0] || null
  const latestRunDetail = latestRun ? runDetails[latestRun.run_id] || null : null
  const latestRunUsage = latestRunDetail?.llm_usage?.[latestRunDetail.llm_usage.length - 1] || null
  const latestRunUsages = latestRunDetail?.llm_usage || []
  const promptCacheLoopPoints = useMemo(
    () => summarizePromptCacheRuns(visiblePromptCacheRuns.slice(0, 12), runDetails),
    [visiblePromptCacheRuns, runDetails],
  )
  const currentRouteIsDeepSeek = useMemo(
    () => isDeepSeekRoute(model.modelName, model.baseURL),
    [model.modelName, model.baseURL],
  )
  const promptCacheStats = useMemo(
    () => summarizePromptCacheUsage(latestRunUsages),
    [latestRunUsages],
  )

  useEffect(() => {
    if (!promptCacheTestEnabled) return
    if (!latestRun) return
    const shouldForce = !latestRunDetail
      || latestRunDetail.status !== latestRun.status
      || !Array.isArray(latestRunDetail.llm_usage)
    getRunDetail(latestRun.run_id, shouldForce).catch(() => {})
  }, [promptCacheTestEnabled, latestRun?.run_id, latestRun?.status, latestRunDetail?.status, getRunDetail])

  useEffect(() => {
    if (!promptCacheTestEnabled || !promptCacheTestExpanded) return
    for (const run of visiblePromptCacheRuns.slice(0, 12)) {
      const detail = runDetails[run.run_id]
      getRunDetail(run.run_id, !detail || !Array.isArray(detail.llm_usage)).catch(() => {})
    }
  }, [promptCacheTestEnabled, promptCacheTestExpanded, visiblePromptCacheRuns, runDetails, getRunDetail])

  // Test API connection via Python backend (delegates to litellm)
  const handleTestConnection = useCallback(async () => {
    setTestStatus('testing')
    try {
      if (!model.apiKey) {
        setTestStatus('error')
        setTimeout(() => setTestStatus('idle'), 3000)
        return
      }

      const result = await pythonClient.send('rpc.agent.test_connection', {
        protocol: model.protocol,
        model: model.modelName || 'gpt-4o',
        api_key: model.apiKey,
        base_url: model.baseURL || undefined,
      })

      if (result.ok) {
        setTestStatus('success')
      } else {
        console.error('[Settings] API test failed:', result.error)
        setTestStatus('error')
      }
      setTimeout(() => setTestStatus('idle'), 3000)
    } catch (err) {
      console.error('[Settings] API test error:', err)
      setTestStatus('error')
      setTimeout(() => setTestStatus('idle'), 3000)
    }
  }, [model.apiKey, model.baseURL, model.protocol, model.modelName])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden">
      {/* === Header: Title + Search === */}
      <div className="shrink-0 border-b border-border">
        {/* Title bar */}
        <div className="h-9 flex items-center px-5 gap-3">
          <span className="text-sm font-medium text-text-primary">{t.settings.title}</span>
          <div className="flex-1" />
          {/* Save status indicator */}
          <div className="flex items-center gap-1.5 text-xs">
            {saveStatus === 'saving' && (
              <>
                <Loader2 className="w-3 h-3 text-text-muted animate-spin" />
                <span className="text-text-muted">{t.settings.saving}</span>
              </>
            )}
            {saveStatus === 'saved' && (
              <>
                <CheckCircle2 className="w-3 h-3 text-accent-success" />
                <span className="text-accent-success">{t.settings.saved}</span>
              </>
            )}
            {saveStatus === 'error' && (
              <>
                <AlertCircle className="w-3 h-3 text-accent-danger" />
                <span className="text-accent-danger">{t.settings.saveFailed}</span>
              </>
            )}
          </div>
        </div>

        {/* Search bar — mirrors VSCode's settings search */}
        <div className="px-5 pb-3">
          <div className="relative max-w-[600px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t.settings.searchPlaceholder}
              className="
                w-full h-[30px] pl-8 pr-8 text-sm
                bg-bg-tertiary text-text-primary
                border border-border rounded-md
                outline-none
                focus:border-accent-primary
                placeholder:text-text-muted
                transition-colors
              "
              spellCheck={false}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-text-muted hover:text-text-secondary transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* === Body: Nav + Content === */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left navigation — VSCode's Table of Contents */}
        <nav className="w-[180px] shrink-0 border-r border-border overflow-y-auto py-3 px-2">
          {filteredSections.map((section) => {
            const isActive = activeSection === section.id
            const Icon = section.icon
            return (
              <button
                key={section.id}
                onClick={() => scrollToSection(section.id)}
                className={`
                  w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-left
                  transition-all duration-100 group relative
                  ${isActive
                    ? 'bg-accent-primary/10 text-accent-primary'
                    : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
                  }
                `}
              >
                {isActive && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-4 bg-accent-primary rounded-r-full" />
                )}
                <Icon className="w-4 h-4 shrink-0" strokeWidth={isActive ? 2 : 1.5} />
                <span className="text-[13px] font-medium">{(t.settings as any)[section.id] ?? section.label}</span>
              </button>
            )
          })}
        </nav>

        {/* Right content — scrollable settings list */}
        <div
          ref={contentRef}
          className="flex-1 overflow-y-auto px-6 py-4"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--text-muted) transparent' }}
        >
          <div className="max-w-[700px]">
            {/* 模型配置 */}
            {filteredSections.some((s) => s.id === 'model') && (
              <div
                id="section-model"
                ref={(el) => { sectionRefs.current['model'] = el }}
              >
                <SettingSection title={t.settings.modelConfig}>
                  {/* ── Preset cards grid ── */}
                  {presets.length > 0 && (
                    <div className="py-3 px-1 border-b border-border">
                      <div className="flex flex-wrap gap-2">
                        {presets.map((p) => {
                          const icon = loadProviderIcon(p.provider)
                          const isActive = loadedPresetId === p.id
                          return (
                            <button
                              key={p.id}
                              onClick={() => loadPreset(p)}
                              className={`
                                group relative w-[90px] h-[68px] rounded-lg
                                flex flex-col items-center justify-center gap-1
                                transition-all duration-150
                                ${isActive
                                  ? 'bg-accent-primary/15 ring-1.5 ring-accent-primary/40 text-accent-primary'
                                  : 'bg-bg-secondary hover:bg-bg-hover text-text-secondary hover:text-text-primary'
                                }
                              `}
                            >
                              {icon ? (
                                <div
                                  className="w-6 h-6 flex items-center justify-center [&>svg]:w-full [&>svg]:h-full"
                                  dangerouslySetInnerHTML={{ __html: icon }}
                                />
                              ) : (
                                <div className="w-6 h-6 rounded-full bg-bg-hover flex items-center justify-center text-[10px] font-bold uppercase">
                                  {p.name.charAt(0)}
                                </div>
                              )}
                              <span className="text-[10px] font-medium truncate max-w-[80px] leading-none">
                                {p.name}
                              </span>
                              {/* Export on hover */}
                              <span
                                role="button"
                                tabIndex={0}
                                onClick={(e) => { e.stopPropagation(); exportPreset(p) }}
                                onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); exportPreset(p) } }}
                                className="hidden group-hover:flex absolute -top-1.5 -left-1.5 items-center justify-center w-4 h-4 rounded-full bg-bg-tertiary border border-border text-text-muted hover:text-accent-primary hover:border-accent-primary/50"
                                title={t.settings.exportConfig}
                              >
                                <Download className="w-2.5 h-2.5" />
                              </span>
                              {/* Delete on hover */}
                              <span
                                role="button"
                                tabIndex={0}
                                onClick={(e) => { e.stopPropagation(); deletePreset(p.id) }}
                                onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); deletePreset(p.id) } }}
                                className="hidden group-hover:flex absolute -top-1.5 -right-1.5 items-center justify-center w-4 h-4 rounded-full bg-bg-tertiary border border-border text-text-muted hover:text-accent-danger hover:border-accent-danger/50"
                              >
                                <X className="w-2.5 h-2.5" />
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* ── Provider selector ── */}
                  <div className="py-3 px-1 border-b border-border">
                    <label className="text-xs font-medium text-text-muted mb-1.5 block">{t.settings.provider}</label>
                    <div ref={providerDropdownRef} className="relative">
                      <button
                        onClick={() => setShowProviderDropdown(!showProviderDropdown)}
                        className="h-9 w-full max-w-[400px] px-3 text-sm rounded border border-border bg-bg-tertiary text-text-primary hover:border-accent-primary/50 transition-colors flex items-center justify-between"
                      >
                        <div className="flex items-center gap-2">
                          {(() => {
                            const match = PROVIDERS.find(p => p.id === selectedProviderId) || PROVIDERS.find(p => p.baseURL && model.baseURL && p.baseURL === model.baseURL)
                            if (match && iconMap[match.id]) {
                              return (
                                <div
                                  className="w-5 h-5 flex items-center justify-center shrink-0 [&>svg]:w-full [&>svg]:h-full"
                                  dangerouslySetInnerHTML={{ __html: iconMap[match.id] }}
                                />
                              )
                            }
                            return null
                          })()}
                          <span>
                            {(() => {
                              const match = PROVIDERS.find(p => p.id === selectedProviderId) || PROVIDERS.find(p => p.baseURL && model.baseURL && p.baseURL === model.baseURL)
                              return match ? match.label : (model.baseURL ? t.settings.custom : t.settings.selectProvider)
                            })()}
                          </span>
                        </div>
                        <ChevronDown className="w-4 h-4 text-text-muted shrink-0" />
                      </button>

                      {showProviderDropdown && (
                        <div className="absolute z-50 top-full left-0 mt-1 w-[360px] max-h-[320px] overflow-y-auto rounded-lg border border-border bg-bg-secondary shadow-xl p-2">
                          <div className="grid grid-cols-4 gap-1">
                            {PROVIDERS.map((provider) => {
                              const icon = iconMap[provider.id]
                              const isActive = provider.id === selectedProviderId || (!selectedProviderId && model.baseURL === provider.baseURL)
                              return (
                                <button
                                  key={provider.id}
                                  onClick={() => handleProviderSelect(provider)}
                                  className={`
                                    flex flex-col items-center gap-1.5 p-2 rounded-md text-center transition-colors
                                    ${isActive
                                      ? 'bg-accent-primary/15 text-accent-primary'
                                      : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                                    }
                                  `}
                                  title={`${provider.label} · ${provider.protocol} · ${provider.defaultModel}`}
                                >
                                  {icon ? (
                                    <div
                                      className="w-7 h-7 flex items-center justify-center [&>svg]:w-full [&>svg]:h-full"
                                      dangerouslySetInnerHTML={{ __html: icon }}
                                    />
                                  ) : (
                                    <div className="w-7 h-7 rounded-full bg-bg-hover flex items-center justify-center text-xs font-bold uppercase">
                                      {provider.label.charAt(0)}
                                    </div>
                                  )}
                                  <span className="text-[10px] leading-tight truncate w-full">
                                    {provider.label}
                                  </span>
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Protocol Type */}
                  <SettingItem
                    id="model-protocol"
                    label={t.settings.protocol}
                    description={t.settings.protocolDesc}
                  >
                    <SettingSelect
                      id="model-protocol"
                      value={model.protocol}
                      onChange={(v) => {
                        const newProtocol = v as ProtocolType
                        if (newProtocol === model.protocol) return
                        // Only change protocol — leave all other fields intact
                        setModel({ protocol: newProtocol })
                      }}
                      options={PROTOCOL_OPTIONS.map((p) => ({
                        value: p.value,
                        label: p.label,
                      }))}
                      className="min-w-[260px]"
                    />
                  </SettingItem>

                  {/* API Key */}
                  <SettingItem
                    id="model-apikey"
                    label={t.settings.apiKey}
                    description={t.settings.apiKeyDesc}
                  >
                    <SettingInput
                      id="model-apikey"
                      type="password"
                      value={model.apiKey}
                      onChange={(v) => setModel({ apiKey: v })}
                      placeholder="sk-..."
                    />
                  </SettingItem>

                  {/* Base URL */}
                  <SettingItem
                    id="model-baseurl"
                    label={t.settings.baseURL}
                    description={t.settings.baseURLDesc}
                  >
                    <SettingInput
                      id="model-baseurl"
                      value={model.baseURL}
                      onChange={(v) => setModel({ baseURL: v })}
                      placeholder={
                        model.protocol === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com/v1'
                      }
                    />
                  </SettingItem>

                  {/* Model Name */}
                  <SettingItem
                    id="model-name"
                    label={t.settings.modelName}
                    description={t.settings.modelNameDesc}
                  >
                    <SettingInput
                      id="model-name"
                      value={model.modelName}
                      onChange={(v) => setModel({ modelName: v })}
                      placeholder={model.protocol === 'openai' ? 'e.g., gpt-4o, deepseek-v4-flash' : 'e.g., claude-3-5-sonnet, MiniMax-M2.7'}
                    />
                  </SettingItem>

                  {/* Test Connection */}
                  <SettingItem
                    id="model-test"
                    label={t.settings.testConnection}
                    description={t.settings.testConnectionDesc}
                  >
                    <button
                      onClick={handleTestConnection}
                      disabled={testStatus === 'testing'}
                      className="
                        h-[30px] px-4 text-sm font-medium rounded
                        bg-accent-primary/15 text-accent-primary
                        hover:bg-accent-primary/25
                        disabled:opacity-50 disabled:cursor-not-allowed
                        transition-colors
                        flex items-center gap-2
                      "
                    >
                      {testStatus === 'testing' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      {testStatus === 'success' && <CheckCircle2 className="w-3.5 h-3.5 text-accent-success" />}
                      {testStatus === 'error' && <AlertCircle className="w-3.5 h-3.5 text-accent-danger" />}
                      {testStatus === 'idle' && t.settings.testConnection}
                      {testStatus === 'testing' && t.settings.testing}
                      {testStatus === 'success' && t.settings.connected}
                      {testStatus === 'error' && t.common.failed}
                    </button>
                  </SettingItem>

                  {/* ── Save actions ── */}
                  <div className="py-3 px-1 flex items-center gap-2">
                    {loadedPresetId && (
                      <button
                        onClick={updateActivePreset}
                        className="h-8 px-3.5 text-xs font-medium rounded-md bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors flex items-center gap-1.5"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        {t.settings.updatePreset}
                      </button>
                    )}
                    {showSaveAsNew ? (
                      <div className="flex items-center gap-1.5">
                        <input
                          autoFocus
                          value={newPresetName}
                          onChange={(e) => setNewPresetName(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter') createNewPreset(); if (e.key === 'Escape') { setShowSaveAsNew(false); setNewPresetName('') } }}
                          placeholder={t.settings.presetName}
                          className="h-8 px-2.5 text-xs rounded-md border border-border bg-bg-tertiary text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary w-36"
                        />
                        <button
                          onClick={createNewPreset}
                          className="h-8 w-8 flex items-center justify-center rounded-md bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => { setShowSaveAsNew(false); setNewPresetName('') }}
                          className="h-8 w-8 flex items-center justify-center rounded-md hover:bg-bg-hover text-text-muted"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowSaveAsNew(true)}
                        className="h-8 px-3.5 text-xs font-medium rounded-md border border-border text-text-secondary hover:text-text-primary hover:border-accent-primary/50 hover:bg-accent-primary/5 transition-colors flex items-center gap-1.5"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        {t.settings.saveAsNew}
                      </button>
                    )}
                    {/* Import / Export */}
                    <div className="ml-auto flex items-center gap-1.5">
                      <button
                        onClick={importModelConfig}
                        className="h-8 px-3 text-xs font-medium rounded-md border border-border text-text-secondary hover:text-text-primary hover:border-accent-primary/50 hover:bg-accent-primary/5 transition-colors flex items-center gap-1.5"
                        title={t.settings.importConfig}
                      >
                        <Upload className="w-3.5 h-3.5" />
                        {t.settings.importConfig}
                      </button>
                      <button
                        onClick={exportModelConfig}
                        className="h-8 px-3 text-xs font-medium rounded-md border border-border text-text-secondary hover:text-text-primary hover:border-accent-primary/50 hover:bg-accent-primary/5 transition-colors flex items-center gap-1.5"
                        title={t.settings.exportConfig}
                      >
                        <Download className="w-3.5 h-3.5" />
                        {t.settings.exportConfig}
                      </button>
                    </div>
                  </div>
                </SettingSection>

                <SettingSection title={t.settings.modelParams}>
                  {/* Temperature */}
                  <SettingItem
                    id="model-temperature"
                    label={t.settings.temperature}
                    description={t.settings.temperatureDesc}
                  >
                    <SettingSlider
                      id="model-temperature"
                      value={model.temperature}
                      onChange={(v) => setModel({ temperature: v })}
                      min={0}
                      max={1}
                      step={0.05}
                    />
                  </SettingItem>

                  {/* Max Tokens */}
                  <SettingItem
                    id="model-maxtokens"
                    label={t.settings.maxTokens}
                    description={t.settings.maxTokensDesc}
                  >
                    <SettingNumber
                      id="model-maxtokens"
                      value={model.maxTokens}
                      onChange={(v) => setModel({ maxTokens: v })}
                      min={256}
                      max={200000}
                      step={256}
                    />
                  </SettingItem>

                  {/* Reasoning Effort */}
                  <SettingItem
                    id="model-reasoning"
                    label={t.settings.reasoningEffort}
                    description={t.settings.reasoningEffortDesc}
                  >
                    <SettingSelect
                      id="model-reasoning"
                      value={model.reasoningEffort}
                      onChange={(v) => setModel({ reasoningEffort: v as 'low' | 'medium' | 'high' })}
                      options={[
                        { value: 'low', label: t.settings.reasoningLow },
                        { value: 'medium', label: t.settings.reasoningMedium },
                        { value: 'high', label: t.settings.reasoningHigh },
                      ]}
                    />
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* Agent 配置 */}
            {filteredSections.some((s) => s.id === 'agent') && (
              <div
                id="section-agent"
                ref={(el) => { sectionRefs.current['agent'] = el }}
              >
                <SettingSection title={t.settings.agentBehavior}>
                  {/* Max Consecutive Mistakes */}
                  <SettingItem
                    id="agent-maxmistakes"
                    label={t.settings.maxConsecutiveMistakes}
                    description={t.settings.maxConsecutiveMistakesDesc}
                  >
                    <SettingSlider
                      id="agent-maxmistakes"
                      value={agent.maxConsecutiveMistakes}
                      onChange={(v) => setAgent({ maxConsecutiveMistakes: v })}
                      min={1}
                      max={10}
                      step={1}
                    />
                  </SettingItem>

                  {/* Code Execution Timeout */}
                  <SettingItem
                    id="agent-timeout"
                    label={t.settings.timeout}
                    description={t.settings.timeoutDesc}
                  >
                    <SettingNumber
                      id="agent-timeout"
                      value={agent.codeExecutionTimeout}
                      onChange={(v) => setAgent({ codeExecutionTimeout: v })}
                      min={10}
                      max={600}
                      step={10}
                    />
                  </SettingItem>

                  {/* Require Confirmation */}
                  <SettingItem
                    id="agent-confirm"
                    label={t.settings.requireConfirmation}
                    description={t.settings.requireConfirmationDesc}
                  >
                    <SettingCheckbox
                      id="agent-confirm"
                      checked={agent.requireConfirmation}
                      onChange={(v) => setAgent({ requireConfirmation: v })}
                    />
                  </SettingItem>

                  {/* Auto Render Results */}
                  <SettingItem
                    id="agent-autorender"
                    label={t.settings.autoRenderResults}
                    description={t.settings.autoRenderResultsDesc}
                  >
                    <SettingCheckbox
                      id="agent-autorender"
                      checked={agent.autoRenderResults}
                      onChange={(v) => setAgent({ autoRenderResults: v })}
                    />
                  </SettingItem>

                  {/* Auto Condense */}
                  <SettingItem
                    id="agent-condense"
                    label={t.settings.autoCondenseContext}
                    description={t.settings.autoCondenseContextDesc}
                  >
                    <SettingCheckbox
                      id="agent-condense"
                      checked={agent.useAutoCondense}
                      onChange={(v) => setAgent({ useAutoCondense: v })}
                    />
                  </SettingItem>

                  {/* Debug Mode */}
                  <SettingItem
                    id="agent-debug"
                    label={t.settings.debugMode}
                    description={t.settings.debugModeDesc}
                  >
                    <SettingCheckbox
                      id="agent-debug"
                      checked={agent.debugMode}
                      onChange={(v) => {
                        setAgent({ debugMode: v })
                        pythonClient.send('rpc.debug.set_log_level', {
                          level: v ? 'DEBUG' : 'INFO',
                        }).catch(() => {/* ignore if backend not ready */})
                      }}
                    />
                  </SettingItem>
                </SettingSection>

                <SettingSection title={t.settings.customInstructions}>
                  {/* Custom Instructions */}
                  <SettingItem
                    id="agent-instructions"
                    label={t.settings.customInstructions}
                    description={t.settings.customInstructionsDesc}
                  >
                    <SettingTextArea
                      id="agent-instructions"
                      value={agent.customInstructions}
                      onChange={async (v) => {
                        const trimmed = v.slice(0, 2000)
                        setAgent({ customInstructions: trimmed })
                        try {
                          await pythonClient.send('user_instructions.set', { content: trimmed })
                        } catch (e) {
                          console.warn('[Settings] user_instructions.set failed:', e)
                        }
                      }}
                      placeholder="[user] Default to Chinese.&#10;[user] Use CGCS2000 (EPSG:4490).&#10;[agent] User prefers seaborn for charts."
                      rows={8}
                    />
                    <div className="text-[10px] text-text-muted mt-1 text-right">
                      {agent.customInstructions.length} / 2000
                    </div>
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* DeepSeek Prompt Cache 测试 */}
            {filteredSections.some((s) => s.id === 'promptCache') && (
              <div
                id="section-promptCache"
                ref={(el) => { sectionRefs.current['promptCache'] = el }}
              >
                <SettingSection title={t.settings.promptCacheTestPanelTitle}>
                  <SettingItem
                    id="prompt-cache-summary"
                    label={t.settings.promptCacheTestPanelTitle}
                    description={t.settings.promptCacheTestPanelDesc}
                  >
                    <div className="space-y-2 text-sm text-text-secondary">
                      <div className="flex flex-wrap items-center gap-3">
                        <SettingCheckbox
                          id="prompt-cache-test-enabled"
                          checked={promptCacheTestEnabled}
                          onChange={setPromptCacheTestEnabled}
                          label={t.settings.promptCacheTestEnable}
                        />
                        <button
                          type="button"
                          onClick={() => setPromptCacheTestExpanded((v) => !v)}
                          disabled={!promptCacheTestEnabled}
                          className="
                            inline-flex h-7 items-center gap-1.5 rounded-md border border-border
                            bg-bg-tertiary px-2 text-xs text-text-secondary
                            hover:bg-bg-hover disabled:opacity-45 disabled:hover:bg-bg-tertiary
                          "
                        >
                          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${promptCacheTestExpanded ? '' : '-rotate-90'}`} />
                          {promptCacheTestExpanded ? t.settings.promptCacheTestCollapse : t.settings.promptCacheTestExpand}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPromptCacheClearedAt(Date.now())}
                          disabled={!promptCacheTestEnabled || visiblePromptCacheRuns.length === 0}
                          className="
                            inline-flex h-7 items-center rounded-md border border-border
                            bg-bg-tertiary px-2 text-xs text-text-muted
                            hover:bg-bg-hover hover:text-text-secondary disabled:opacity-45 disabled:hover:bg-bg-tertiary
                          "
                        >
                          {t.settings.promptCacheClearHistory}
                        </button>
                        <span className={`rounded px-1.5 py-0.5 text-[11px] ${currentRouteIsDeepSeek ? 'bg-green-500/12 text-green-500' : 'bg-amber-500/12 text-amber-500'}`}>
                          {currentRouteIsDeepSeek ? t.settings.promptCacheDeepSeekReady : t.settings.promptCacheDeepSeekOnly}
                        </span>
                      </div>

                      {promptCacheTestEnabled && promptCacheTestExpanded && (
                        <div className="space-y-2">
                          <div className="grid grid-cols-2 gap-2 max-w-[760px] md:grid-cols-3">
                            <MiniStat label={t.settings.promptCacheProvider} value={`${model.protocol} / ${model.modelName || '—'}`} />
                            <MiniStat label={t.settings.promptCacheMode} value={promptCacheStats.modeLabel} />
                            <MiniStat label={t.settings.promptCacheTurns} value={String(promptCacheStats.turns)} />
                            <MiniStat label={t.settings.promptCacheInputTokens} value={promptCacheStats.inputTokensLabel} />
                            <MiniStat label={t.settings.promptCacheHitTokens} value={promptCacheStats.hitTokensLabel} />
                            <MiniStat label={t.settings.promptCacheHitRatio} value={promptCacheStats.hitRatioLabel} />
                          </div>

                          <div className="rounded-md border border-border bg-bg-secondary/60 px-3 py-2 space-y-1.5">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium text-text-primary">{t.settings.promptCacheLatestRun}</span>
                              {latestRun && (
                                <span className="text-[11px] text-text-muted font-mono">
                                  {latestRun.run_id.slice(0, 8)}
                                </span>
                              )}
                            </div>
                            {latestRunUsage ? (
                              <div className="space-y-1 text-xs text-text-muted/85">
                                <div className="flex flex-wrap gap-x-3 gap-y-1">
                                  <span>{t.settings.promptCacheTotalTokens}: <span className="font-mono">{promptCacheStats.totalTokensLabel}</span></span>
                                  <span>{t.settings.promptCacheMissTokens}: <span className="font-mono">{promptCacheStats.missTokensLabel}</span></span>
                                  <span>{t.settings.promptCacheKey}: <span className="font-mono">{latestRunUsage.prompt_cache?.cache_key || '—'}</span></span>
                                  <span>{t.settings.promptCacheStrategy}: <span className="font-mono">{latestRunUsage.prompt_cache?.strategy || '—'}</span></span>
                                  <span>{t.settings.promptCachePrefix}: <span className="font-mono">{latestRunUsage.prompt_cache?.prefix_hash?.slice(0, 12) || '—'}</span></span>
                                  <span>{t.settings.promptCacheSections}: <span className="font-mono">{latestRunUsage.prompt_cache?.sections?.length ?? 0}</span></span>
                                  <span>{t.settings.promptCacheReason}: <span className="font-mono">{promptCacheStats.reasonLabel}</span></span>
                                </div>
                                <div className="text-[11px] text-text-muted/70">
                                  {(latestRunUsage.prompt_cache?.sections || []).slice(0, 3).map((section: any) => (
                                    <span key={String(section.id)} className="inline-block mr-2 font-mono">
                                      {String(section.id)}#{String(section.cache_policy || 'none')}
                                    </span>
                                  ))}
                                  {(latestRunUsage.prompt_cache?.sections?.length || 0) > 3 && (
                                    <span className="italic">+{(latestRunUsage.prompt_cache?.sections?.length || 0) - 3} {t.runs.more}</span>
                                  )}
                                </div>
                                <p className="text-[11px] leading-relaxed text-text-muted/75">
                                  {t.settings.promptCacheDeepSeekHint}
                                </p>
                              </div>
                            ) : (
                              <div className="text-xs text-text-muted/70">
                                {latestRun ? t.settings.promptCacheLoading : t.settings.promptCacheNoRun}
                              </div>
                            )}
                          </div>

                          <div className="rounded-md border border-border bg-bg-secondary/60 px-3 py-2 space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium text-text-primary">{t.settings.promptCacheAggregate}</span>
                              <span className="text-[11px] text-text-muted">{promptCacheLoopPoints.length} {t.settings.promptCacheLoops}</span>
                            </div>
                            {promptCacheLoopPoints.length > 0 ? (
                              <>
                                <PromptCacheWave
                                  points={promptCacheLoopPoints}
                                  totalLabel={t.settings.promptCacheTotalTokens}
                                  hitLabel={t.settings.promptCacheHitTokens}
                                />
                                <div className="max-h-[168px] overflow-auto pr-1">
                                  <div className="grid grid-cols-[minmax(72px,1fr)_80px_80px_70px] gap-x-2 gap-y-1 text-[11px]">
                                    <span className="text-text-muted">{t.settings.promptCacheLoop}</span>
                                    <span className="text-right text-text-muted">{t.settings.promptCacheTotalTokens}</span>
                                    <span className="text-right text-text-muted">{t.settings.promptCacheHitTokens}</span>
                                    <span className="text-right text-text-muted">{t.settings.promptCacheHitRatio}</span>
                                    {promptCacheLoopPoints.map((point) => (
                                      <Fragment key={point.runId}>
                                        <span className="truncate font-mono text-text-secondary" title={point.runId}>{point.label}</span>
                                        <span className="text-right font-mono text-text-secondary">{formatNumberLabel(point.totalTokens)}</span>
                                        <span className="text-right font-mono text-text-secondary">{formatNumberLabel(point.hitTokens)}</span>
                                        <span className="text-right font-mono text-text-secondary">{point.hitRatio == null ? '—' : `${Math.round(point.hitRatio * 1000) / 10}%`}</span>
                                      </Fragment>
                                    ))}
                                  </div>
                                </div>
                              </>
                            ) : (
                              <div className="text-xs text-text-muted/70">{t.settings.promptCacheNoDisplayHistory}</div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* 外观设置 */}
            {filteredSections.some((s) => s.id === 'appearance') && (
              <div
                id="section-appearance"
                ref={(el) => { sectionRefs.current['appearance'] = el }}
              >
                <SettingSection title={t.settings.appearance}>
                  {/* Theme */}
                  <SettingItem
                    id="appearance-theme"
                    label={t.settings.theme}
                    description={t.settings.themeDesc}
                  >
                    <SettingSelect
                      id="appearance-theme"
                      value={appearance.theme}
                      onChange={(v) => setAppearance({ theme: v as 'dark' | 'light' | 'system' })}
                      options={[
                        { value: 'dark', label: t.settings.themeDark },
                        { value: 'light', label: t.settings.themeLight },
                        { value: 'system', label: t.settings.themeSystem },
                      ]}
                    />
                  </SettingItem>

                  {/* Language */}
                  <SettingItem
                    id="appearance-language"
                    label={t.settings.language}
                    description={t.settings.languageDesc}
                  >
                    <SettingSelect
                      id="appearance-language"
                      value={appearance.language}
                      onChange={(v) => setAppearance({ language: v as 'en' | 'zh' })}
                      options={[
                        { value: 'en', label: 'English' },
                        { value: 'zh', label: '中文' },
                      ]}
                    />
                  </SettingItem>

                  {/* Basemap Source */}
                  <SettingItem
                    id="appearance-basemap"
                    label={t.settings.basemap}
                    description={t.settings.basemapDesc}
                  >
                    <SettingSelect
                      id="appearance-basemap"
                      value={appearance.basemapId}
                      onChange={(v) => {
                        setAppearance({ basemapId: v })
                        // Also update the live map basemap
                        const basemap = BUILTIN_BASEMAPS.find((b) => b.id === v)
                        if (basemap) {
                          useMapStore.getState().setBasemap(basemap)
                        }
                      }}
                      options={BUILTIN_BASEMAPS.map((b) => ({
                        value: b.id,
                        label: b.name,
                      }))}
                      className="min-w-[260px]"
                    />
                  </SettingItem>

                  {/* Map Labels */}
                  <SettingItem
                    id="appearance-map-labels"
                    label={t.settings.showMapLabels}
                    description={t.settings.showMapLabelsDesc}
                  >
                    <SettingCheckbox
                      id="appearance-map-labels"
                      checked={appearance.showMapLabels}
                      onChange={(checked) => {
                        // 1. Persist to settingsStore
                        setAppearance({ showMapLabels: checked })

                        // 2. Update map immediately
                        const store = useMapStore.getState()
                        store.setLabelsVisible(checked)
                        const currentBasemap = store.basemap
                        if (currentBasemap.type === 'raster-tiles') {
                          const targetId = checked ? 'carto-voyager' : 'carto-voyager-nolabels'
                          const target = BUILTIN_BASEMAPS.find((b) => b.id === targetId)
                          if (target && target.id !== currentBasemap.id) {
                            store.setBasemap(target)
                          }
                          return
                        }
                        const currentId = currentBasemap.id
                        if (!checked) {
                          const noLabelsId = currentId + '-nolabels'
                          const noLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === noLabelsId)
                          if (noLabelsBasemap) {
                            store.setBasemap(noLabelsBasemap)
                            return
                          }
                        } else if (currentId.endsWith('-nolabels')) {
                          const withLabelsId = currentId.replace('-nolabels', '')
                          const withLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === withLabelsId)
                          if (withLabelsBasemap) {
                            store.setBasemap(withLabelsBasemap)
                            return
                          }
                        }
                        // Fallback: toggle symbol layers directly via MapEngine
                        const map = mapEngine.getMap()
                        if (map) {
                          if (map.isStyleLoaded()) {
                            mapEngine.setLabelsVisible(checked)
                          } else {
                            map.once('style.load', () => {
                              mapEngine.setLabelsVisible(checked)
                            })
                          }
                        }
                      }}
                      label={t.settings.showMapLabels}
                    />
                  </SettingItem>

                  {/* Custom Tile URL */}
                  <SettingItem
                    id="appearance-custom-tile"
                    label={t.settings.customTileUrl}
                    description={t.settings.customTileUrlDesc}
                  >
                    <SettingInput
                      id="appearance-custom-tile"
                      value={appearance.customTileUrl}
                      onChange={(v) => {
                        setAppearance({ customTileUrl: v })
                        if (v.trim()) {
                          useMapStore.getState().setBasemap({
                            id: 'custom',
                            name: 'Custom Tiles',
                            type: 'raster-tiles',
                            url: v.trim(),
                          })
                        }
                      }}
                      placeholder="https://tile.example.com/{z}/{x}/{y}.png"
                    />
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* Python 环境 */}
            {filteredSections.some((s) => s.id === 'python') && (
              <div
                id="section-python"
                ref={(el) => { sectionRefs.current['python'] = el }}
              >
                <SettingSection title={t.settings.pythonEnv}>
                  {/* Backend Status */}
                  <SettingItem
                    id="python-backend-status"
                    label={t.settings.pythonStatus}
                    description={t.settings.pythonStatusDesc}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-1.5">
                        {pythonBackendStatus === 'ready' && (
                          <>
                            <CheckCircle2 className="w-3.5 h-3.5 text-accent-success" />
                            <span className="text-sm text-accent-success">{t.settings.statusReady}</span>
                          </>
                        )}
                        {pythonBackendStatus === 'starting' && (
                          <>
                            <Loader2 className="w-3.5 h-3.5 text-accent-warning animate-spin" />
                            <span className="text-sm text-accent-warning">{t.settings.statusStarting}</span>
                          </>
                        )}
                        {pythonBackendStatus === 'stopped' && (
                          <>
                            <div className="w-2 h-2 rounded-full bg-text-muted" />
                            <span className="text-sm text-text-muted">{t.settings.statusStopped}</span>
                          </>
                        )}
                        {pythonBackendStatus === 'error' && (
                          <>
                            <AlertCircle className="w-3.5 h-3.5 text-accent-danger" />
                            <span className="text-sm text-accent-danger">{t.settings.statusError}</span>
                          </>
                        )}
                      </div>
                      <button
                        onClick={handleRestartPython}
                        disabled={pythonRestarting || pythonBackendStatus === 'starting'}
                        className="
                          h-[30px] px-3 text-sm font-medium rounded
                          bg-accent-primary/15 text-accent-primary
                          hover:bg-accent-primary/25
                          disabled:opacity-50 disabled:cursor-not-allowed
                          transition-colors
                          flex items-center gap-1.5
                        "
                      >
                        {pythonRestarting ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RotateCcw className="w-3.5 h-3.5" />
                        )}
                        {pythonRestarting ? t.settings.restarting : t.settings.restart}
                      </button>
                    </div>
                  </SettingItem>

                  {/* Error message */}
                  {pythonBackendStatus === 'error' && pythonBackendError && (
                    <div className="ml-0 mb-2 px-3 py-2 rounded bg-accent-danger/10 text-xs text-accent-danger break-all">
                      {pythonBackendError}
                    </div>
                  )}
                </SettingSection>
              </div>
            )}

            {/* Bottom spacer */}
            <div className="h-20" />
          </div>
        </div>
      </div>
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-bg-secondary/60 px-2.5 py-2">
      <div className="text-[11px] text-text-muted uppercase tracking-wider">{label}</div>
      <div className="mt-0.5 text-sm text-text-primary truncate">{value}</div>
    </div>
  )
}

interface PromptCacheLoopPoint {
  runId: string
  label: string
  totalTokens: number | null
  inputTokens: number | null
  hitTokens: number | null
  missTokens: number | null
  hitRatio: number | null
}

function PromptCacheWave({
  points,
  totalLabel,
  hitLabel,
}: {
  points: PromptCacheLoopPoint[]
  totalLabel: string
  hitLabel: string
}) {
  const width = 520
  const height = 96
  const pad = 10
  const values = points.map((point) => ({
    total: point.totalTokens || 0,
    hit: point.hitTokens || 0,
  }))
  const maxValue = Math.max(1, ...values.flatMap((v) => [v.total, v.hit]))
  const xFor = (index: number) => {
    if (values.length <= 1) return width / 2
    return pad + (index / (values.length - 1)) * (width - pad * 2)
  }
  const yFor = (value: number) => height - pad - (value / maxValue) * (height - pad * 2)
  const totalPath = values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(v.total).toFixed(1)}`).join(' ')
  const hitPath = values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(v.hit).toFixed(1)}`).join(' ')
  const totalFill = `${totalPath} L ${xFor(values.length - 1).toFixed(1)} ${height - pad} L ${xFor(0).toFixed(1)} ${height - pad} Z`
  const hitFill = `${hitPath} L ${xFor(values.length - 1).toFixed(1)} ${height - pad} L ${xFor(0).toFixed(1)} ${height - pad} Z`

  return (
    <div className="rounded-md bg-bg-tertiary/70 px-2 py-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[96px] w-full overflow-visible">
        <defs>
          <linearGradient id="pcw-total-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.04" />
          </linearGradient>
          <linearGradient id="pcw-hit-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22c55e" stopOpacity="0.30" />
            <stop offset="100%" stopColor="#22c55e" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        <path d={totalFill} fill="url(#pcw-total-grad)" />
        <path d={hitFill} fill="url(#pcw-hit-grad)" />
        <path d={totalPath} fill="none" stroke="#3b82f6" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        <path d={hitPath} fill="none" stroke="#22c55e" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {values.map((v, i) => (
          <g key={points[i]?.runId || i}>
            <circle cx={xFor(i)} cy={yFor(v.total)} r="2.8" fill="#3b82f6" stroke="#1e3a5f" strokeWidth="1" />
            <circle cx={xFor(i)} cy={yFor(v.hit)} r="2.8" fill="#22c55e" stroke="#14532d" strokeWidth="1" />
          </g>
        ))}
      </svg>
      <div className="mt-1.5 flex items-center gap-4 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: '#3b82f6' }} /> {totalLabel}</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: '#22c55e' }} /> {hitLabel}</span>
      </div>
    </div>
  )
}

function summarizePromptCacheRuns(
  runs: RunSummary[],
  details: Record<string, RunDetail | undefined>,
): PromptCacheLoopPoint[] {
  return runs
    .map((run) => {
      const records = details[run.run_id]?.llm_usage || []
      const metrics = collectPromptCacheMetrics(records)
      return {
        runId: run.run_id,
        label: run.run_id.slice(0, 8),
        totalTokens: metrics.hasTotal ? metrics.totalTokens : null,
        inputTokens: metrics.hasInput ? metrics.inputTokens : null,
        hitTokens: metrics.hasHitMiss || metrics.hasCached ? (metrics.hasHitMiss ? metrics.hitTokens : metrics.cachedTokens) : null,
        missTokens: metrics.hasHitMiss ? metrics.missTokens : null,
        hitRatio: metrics.hitRatio,
      }
    })
    .filter((point) => point.totalTokens != null || point.hitTokens != null)
    .reverse()
}

function summarizePromptCacheUsage(records: RunLLMUsageRecord[]) {
  const metrics = collectPromptCacheMetrics(records)

  return {
    turns: records?.length || 0,
    enabled: metrics.enabled,
    sent: metrics.sent,
    statusLabel: metrics.sent ? 'sent' : (metrics.blockReason || metrics.status || 'not_sent'),
    modeLabel: metrics.mode || (metrics.sent ? 'openai_prompt_cache_key' : 'observe_only'),
    reasonLabel: metrics.blockReason || metrics.note || metrics.status || (metrics.hasHitMiss || metrics.hasCached ? 'provider_reported' : 'provider_usage_not_reported'),
    totalTokensLabel: metrics.hasTotal ? String(Math.round(metrics.totalTokens)) : '—',
    inputTokensLabel: metrics.hasInput ? String(Math.round(metrics.inputTokens)) : '—',
    cachedTokensLabel: metrics.hasCached ? String(Math.round(metrics.cachedTokens)) : '—',
    hitTokensLabel: metrics.hasHitMiss || metrics.hasCached ? String(Math.round(metrics.hasHitMiss ? metrics.hitTokens : metrics.cachedTokens)) : '—',
    missTokensLabel: metrics.hasHitMiss ? String(Math.round(metrics.missTokens)) : '—',
    hitRatioLabel: metrics.hitRatio == null ? '—' : `${Math.round(metrics.hitRatio * 1000) / 10}%`,
  }
}

function collectPromptCacheMetrics(records: RunLLMUsageRecord[]) {
  const metrics = {
    totalTokens: 0,
    inputTokens: 0,
    cachedTokens: 0,
    hitTokens: 0,
    missTokens: 0,
    hasTotal: false,
    hasInput: false,
    hasCached: false,
    hasHitMiss: false,
    enabled: false,
    sent: false,
    blockReason: '',
    status: '',
    mode: '',
    note: '',
    hitRatio: null as number | null,
  }

  for (const record of records || []) {
    const usage = record.usage || {}
    const telemetry = record.telemetry || {}
    const promptCache = record.prompt_cache || {}
    metrics.enabled = metrics.enabled || Boolean(promptCache.enabled)
    metrics.sent = metrics.sent || Boolean(promptCache.prompt_cache_key_sent)
    metrics.status = String(promptCache.prompt_cache_key_status || metrics.status || '')
    metrics.blockReason = String(promptCache.prompt_cache_key_block_reason || metrics.blockReason || '')
    metrics.mode = String(promptCache.provider_cache_mode || metrics.mode || '')
    metrics.note = String(promptCache.provider_cache_note || metrics.note || '')

    const prompt = firstNumber(
      usage.prompt_tokens,
      usage.input_tokens,
      telemetry.context_tokens,
    )
    const completion = firstNumber(usage.completion_tokens, usage.output_tokens)
    const total = firstNumber(
      usage.total_tokens,
      prompt != null || completion != null ? Number(prompt || 0) + Number(completion || 0) : undefined,
    )
    if (total != null) {
      metrics.totalTokens += total
      metrics.hasTotal = true
    }
    if (prompt != null) {
      metrics.inputTokens += prompt
      metrics.hasInput = true
    }

    const cached = firstNumber(
      usage.prompt_cache_hit_tokens,
      nestedNumber(usage, ['prompt_tokens_details', 'cached_tokens']),
      nestedNumber(usage, ['input_tokens_details', 'cached_tokens']),
      nestedNumber(usage, ['input_token_details', 'cache_read']),
      usage.cache_read_input_tokens,
      usage.cached_tokens,
      promptCache.cached_tokens,
    )
    if (cached != null) {
      metrics.cachedTokens += cached
      metrics.hasCached = true
    }

    const hit = firstNumber(usage.prompt_cache_hit_tokens, promptCache.cached_tokens)
    const miss = firstNumber(usage.prompt_cache_miss_tokens)
    if (hit != null || miss != null) {
      metrics.hitTokens += hit || 0
      metrics.missTokens += miss || 0
      metrics.hasHitMiss = true
    }
  }

  const hitMissTotal = metrics.hitTokens + metrics.missTokens
  metrics.hitRatio = metrics.hasHitMiss && hitMissTotal > 0
    ? Math.max(0, Math.min(1, metrics.hitTokens / hitMissTotal))
    : metrics.hasCached && metrics.hasInput && metrics.inputTokens > 0
      ? Math.max(0, Math.min(1, metrics.cachedTokens / metrics.inputTokens))
      : null
  return metrics
}

function isDeepSeekRoute(modelName: string, baseURL: string) {
  const route = `${modelName || ''} ${baseURL || ''}`.toLowerCase()
  return route.includes('deepseek') || route.includes('api.deepseek.com')
}

function runCreatedAtMs(createdAt: string) {
  const ms = Date.parse(createdAt || '')
  return Number.isFinite(ms) ? ms : 0
}

function formatNumberLabel(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value >= 1_000_000) return `${Math.round(value / 100_000) / 10}m`
  if (value >= 1_000) return `${Math.round(value / 100) / 10}k`
  return String(Math.round(value))
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    if (value == null || value === '') continue
    const n = Number(value)
    if (Number.isFinite(n) && n >= 0) return n
  }
  return undefined
}

function nestedNumber(source: Record<string, unknown>, path: string[]) {
  let current: unknown = source
  for (const key of path) {
    if (!current || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return firstNumber(current)
}
