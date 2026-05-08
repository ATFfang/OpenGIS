import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import {
  Search,
  X,
  Bot,
  Palette,
  Terminal,
  Cpu,
  ChevronDown,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Plus,
} from 'lucide-react'
import { useSettingsStore } from '@/stores/settingsStore'
import type { ProtocolType, ModelPreset } from '@/stores/settingsStore'
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

/* ============================================
   Protocol options — only openai and anthropic
   ============================================ */

const PROTOCOL_OPTIONS: { value: ProtocolType; label: string; description: string }[] = [
  { value: 'openai', label: 'OpenAI Compatible', description: 'OpenAI-style API (chat/completions)' },
  { value: 'anthropic', label: 'Anthropic Compatible', description: 'Anthropic-style API (messages)' },
]

/* ============================================
   Navigation sections
   ============================================ */

interface NavSection {
  id: string
  label: string
  icon: React.ElementType
  keywords: string[]
}

const NAV_SECTIONS: NavSection[] = [
  { id: 'model', label: 'Model', icon: Bot, keywords: ['llm', 'api', 'key', 'protocol', 'model', 'temperature', 'token'] },
  { id: 'agent', label: 'Agent', icon: Cpu, keywords: ['agent', 'iteration', 'confirmation', 'timeout', 'instructions'] },
  { id: 'appearance', label: 'Appearance', icon: Palette, keywords: ['theme', 'font', 'language', 'map', 'dark', 'light'] },
  { id: 'python', label: 'Python', icon: Terminal, keywords: ['python', 'path', 'environment', 'interpreter'] },
]

/* ============================================
   SettingsView — VSCode-style settings page
   ============================================ */

export function SettingsView() {
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
  const [loadedPresetId, setLoadedPresetId] = useState<string | null>(null)

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

  // Load settings on mount
  useEffect(() => {
    loadFromElectron()
  }, [loadFromElectron])

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
        saveTimeoutRef.current = null
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

  const activePresetId = useMemo(() => {
    return presets.find(
      (p) =>
        p.protocol === model.protocol &&
        p.modelName === model.modelName &&
        p.apiKey === model.apiKey &&
        p.baseURL === model.baseURL,
    )?.id
  }, [presets, model.protocol, model.modelName, model.apiKey, model.baseURL])

  // Resolve icon for a provider id
  const loadProviderIcon = useCallback((providerId?: string) => {
    if (!providerId) return null
    return iconMap[providerId] || null
  }, [])

  const handleProviderSelect = useCallback(
    (provider: ProviderConfig) => {
      setModel({
        protocol: provider.protocol,
        baseURL: provider.baseURL,
        modelName: model.modelName || provider.defaultModel,
      })
      setShowProviderDropdown(false)
    },
    [setModel, model.modelName],
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
          <span className="text-sm font-medium text-text-primary">Settings</span>
          <div className="flex-1" />
          {/* Save status indicator */}
          <div className="flex items-center gap-1.5 text-xs">
            {saveStatus === 'saving' && (
              <>
                <Loader2 className="w-3 h-3 text-text-muted animate-spin" />
                <span className="text-text-muted">Saving...</span>
              </>
            )}
            {saveStatus === 'saved' && (
              <>
                <CheckCircle2 className="w-3 h-3 text-accent-success" />
                <span className="text-accent-success">Saved</span>
              </>
            )}
            {saveStatus === 'error' && (
              <>
                <AlertCircle className="w-3 h-3 text-accent-danger" />
                <span className="text-accent-danger">Save failed</span>
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
              placeholder="Search settings..."
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
                <span className="text-[13px] font-medium">{section.label}</span>
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
            {/* ======== Model Section ======== */}
            {filteredSections.some((s) => s.id === 'model') && (
              <div
                id="section-model"
                ref={(el) => { sectionRefs.current['model'] = el }}
              >
                <SettingSection title="Model Configuration">
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
                    <label className="text-xs font-medium text-text-muted mb-1.5 block">Provider</label>
                    <div ref={providerDropdownRef} className="relative">
                      <button
                        onClick={() => setShowProviderDropdown(!showProviderDropdown)}
                        className="h-9 w-full max-w-[400px] px-3 text-sm rounded border border-border bg-bg-tertiary text-text-primary hover:border-accent-primary/50 transition-colors flex items-center justify-between"
                      >
                        <div className="flex items-center gap-2">
                          {(() => {
                            const match = PROVIDERS.find(p => p.baseURL && model.baseURL && p.baseURL === model.baseURL)
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
                              const match = PROVIDERS.find(p => p.baseURL && model.baseURL && p.baseURL === model.baseURL)
                              return match ? match.label : (model.baseURL ? 'Custom' : 'Select provider...')
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
                              const isCurrentBaseURL = model.baseURL === provider.baseURL
                              return (
                                <button
                                  key={provider.id}
                                  onClick={() => handleProviderSelect(provider)}
                                  className={`
                                    flex flex-col items-center gap-1.5 p-2 rounded-md text-center transition-colors
                                    ${isCurrentBaseURL
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
                    label="Protocol"
                    description="Select the API protocol. OpenAI-compatible endpoints use /chat/completions. Anthropic-compatible endpoints use /v1/messages."
                  >
                    <SettingSelect
                      id="model-protocol"
                      value={model.protocol}
                      onChange={(v) => {
                        setModel({
                          protocol: v as ProtocolType,
                          baseURL: '',
                        })
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
                    label="API Key"
                    description="Enter your API key. Keys are stored locally and never sent to third parties."
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
                    label="Base URL"
                    description="Custom API endpoint URL (optional). Leave empty to use the default endpoint for the protocol."
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
                    label="Model Name"
                    description="Enter the model name/ID as expected by your API endpoint."
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
                    label="Connection Test"
                    description="Verify that the API key and endpoint are configured correctly."
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
                      {testStatus === 'idle' && 'Test Connection'}
                      {testStatus === 'testing' && 'Testing...'}
                      {testStatus === 'success' && 'Connected'}
                      {testStatus === 'error' && 'Failed'}
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
                        Save
                      </button>
                    )}
                    {showSaveAsNew ? (
                      <div className="flex items-center gap-1.5">
                        <input
                          autoFocus
                          value={newPresetName}
                          onChange={(e) => setNewPresetName(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter') createNewPreset(); if (e.key === 'Escape') { setShowSaveAsNew(false); setNewPresetName('') } }}
                          placeholder="Preset name"
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
                        Save as new
                      </button>
                    )}
                  </div>
                </SettingSection>

                <SettingSection title="Model Parameters">
                  {/* Temperature */}
                  <SettingItem
                    id="model-temperature"
                    label="Temperature"
                    description="Controls randomness. Lower values make responses more deterministic, higher values more creative. (0 = deterministic, 1 = creative)"
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
                    label="Max Tokens"
                    description="Maximum number of tokens in the model's response."
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
                    label="Reasoning Effort"
                    description="Controls how much reasoning the model performs before responding. Only applies to models that support extended thinking."
                  >
                    <SettingSelect
                      id="model-reasoning"
                      value={model.reasoningEffort}
                      onChange={(v) => setModel({ reasoningEffort: v as 'low' | 'medium' | 'high' })}
                      options={[
                        { value: 'low', label: 'Low' },
                        { value: 'medium', label: 'Medium' },
                        { value: 'high', label: 'High' },
                      ]}
                    />
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* ======== Agent Section ======== */}
            {filteredSections.some((s) => s.id === 'agent') && (
              <div
                id="section-agent"
                ref={(el) => { sectionRefs.current['agent'] = el }}
              >
                <SettingSection title="Agent Behavior">
                  {/* Max Iterations */}
                  <SettingItem
                    id="agent-maxiter"
                    label="Max Iterations"
                    description="Maximum number of ReAct loop iterations the agent can perform per task."
                  >
                    <SettingSlider
                      id="agent-maxiter"
                      value={agent.maxIterations}
                      onChange={(v) => setAgent({ maxIterations: v })}
                      min={1}
                      max={50}
                      step={1}
                    />
                  </SettingItem>

                  {/* Max Consecutive Mistakes */}
                  <SettingItem
                    id="agent-maxmistakes"
                    label="Max Consecutive Mistakes"
                    description="Number of consecutive tool call failures before the agent stops and asks for help."
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
                    label="Code Execution Timeout"
                    description="Maximum time (in seconds) for a single code execution before it is terminated."
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
                    label="Require Confirmation"
                    description="Ask for user confirmation before executing potentially destructive operations (file writes, code execution)."
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
                    label="Auto Render Results"
                    description="Automatically render analysis results to the map and chart modules when available."
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
                    label="Auto Condense Context"
                    description="Automatically compress conversation history when approaching the context window limit."
                  >
                    <SettingCheckbox
                      id="agent-condense"
                      checked={agent.useAutoCondense}
                      onChange={(v) => setAgent({ useAutoCondense: v })}
                    />
                  </SettingItem>
                </SettingSection>

                <SettingSection title="Custom Instructions">
                  {/* Custom Instructions */}
                  <SettingItem
                    id="agent-instructions"
                    label="Custom Instructions"
                    description="Additional instructions appended to the system prompt. Use this to customize the agent's behavior, persona, or domain knowledge."
                  >
                    <SettingTextArea
                      id="agent-instructions"
                      value={agent.customInstructions}
                      onChange={(v) => setAgent({ customInstructions: v })}
                      placeholder="e.g., Always use EPSG:4326 for output coordinate systems. Prefer GeoPandas over raw GDAL for vector operations."
                      rows={5}
                    />
                  </SettingItem>
                </SettingSection>
              </div>
            )}

            {/* ======== Appearance Section ======== */}
            {filteredSections.some((s) => s.id === 'appearance') && (
              <div
                id="section-appearance"
                ref={(el) => { sectionRefs.current['appearance'] = el }}
              >
                <SettingSection title="Appearance">
                  {/* Theme */}
                  <SettingItem
                    id="appearance-theme"
                    label="Color Theme"
                    description="Controls the overall color theme of the application."
                  >
                    <SettingSelect
                      id="appearance-theme"
                      value={appearance.theme}
                      onChange={(v) => setAppearance({ theme: v as 'dark' | 'light' | 'system' })}
                      options={[
                        { value: 'dark', label: 'Dark' },
                        { value: 'light', label: 'Light' },
                        { value: 'system', label: 'System' },
                      ]}
                    />
                  </SettingItem>

                  {/* Language */}
                  <SettingItem
                    id="appearance-language"
                    label="Language"
                    description="Display language for the application interface."
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

                  {/* Font Size */}
                  <SettingItem
                    id="appearance-fontsize"
                    label="Font Size"
                    description="Controls the font size in pixels for the editor and UI elements."
                  >
                    <SettingSlider
                      id="appearance-fontsize"
                      value={appearance.fontSize}
                      onChange={(v) => setAppearance({ fontSize: v })}
                      min={10}
                      max={24}
                      step={1}
                      valueFormatter={(v) => `${v}px`}
                    />
                  </SettingItem>

                  {/* Basemap Source */}
                  <SettingItem
                    id="appearance-basemap"
                    label="Basemap Source"
                    description="Select the default basemap tile source for the map view. Changes apply immediately."
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
                    label="Map Labels"
                    description="Show or hide text labels on the basemap. For raster basemaps, toggling labels will switch to a vector basemap variant."
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
                      label="Show map labels"
                    />
                  </SettingItem>

                  {/* Custom Tile URL */}
                  <SettingItem
                    id="appearance-custom-tile"
                    label="Custom Tile URL"
                    description="Enter a custom XYZ tile URL template (e.g. https://tile.example.com/{z}/{x}/{y}.png). Leave empty to use the selected basemap above."
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

            {/* ======== Python Section ======== */}
            {filteredSections.some((s) => s.id === 'python') && (
              <div
                id="section-python"
                ref={(el) => { sectionRefs.current['python'] = el }}
              >
                <SettingSection title="Python Environment">
                  {/* Backend Status */}
                  <SettingItem
                    id="python-backend-status"
                    label="Backend Status"
                    description="Current status of the Python backend service."
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-1.5">
                        {pythonBackendStatus === 'ready' && (
                          <>
                            <CheckCircle2 className="w-3.5 h-3.5 text-accent-success" />
                            <span className="text-sm text-accent-success">Running</span>
                          </>
                        )}
                        {pythonBackendStatus === 'starting' && (
                          <>
                            <Loader2 className="w-3.5 h-3.5 text-accent-warning animate-spin" />
                            <span className="text-sm text-accent-warning">Starting...</span>
                          </>
                        )}
                        {pythonBackendStatus === 'stopped' && (
                          <>
                            <div className="w-2 h-2 rounded-full bg-text-muted" />
                            <span className="text-sm text-text-muted">Stopped</span>
                          </>
                        )}
                        {pythonBackendStatus === 'error' && (
                          <>
                            <AlertCircle className="w-3.5 h-3.5 text-accent-danger" />
                            <span className="text-sm text-accent-danger">Error</span>
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
                        {pythonRestarting ? 'Restarting...' : 'Restart'}
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
