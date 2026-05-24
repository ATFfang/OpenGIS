import { useEffect } from 'react'
import { MainLayout } from './layouts/MainLayout'
import { DialogHost } from '@/components/Dialog'
import { useSettingsStore } from '@/stores/settingsStore'
import { pythonClient } from '@/services/pythonClient'
import {
  globalDispatcher,
  globalRegistry,
  registerAllHandlers,
} from '@/services/rpc'
import { installExtensions } from '@/features/map/extensions'

function App() {
  const loadFromElectron = useSettingsStore((s) => s.loadFromElectron)
  const theme = useSettingsStore((s) => s.appearance.theme)

  // Wire up the v3.0 JSON-RPC 2.0 three-channel bridge once, at app start.
  //
  // Python sidecar sends inbound requests/notifications under the
  // `rpc.* / chat.* / event.*` canonical method names; pythonClient
  // dispatches them through globalDispatcher into the handlers
  // registered on globalRegistry. This is the single wire since
  // Stage 3.6 (the legacy CommandBus was removed along with the
  // `map.*` dual-notification path on the Python side).
  useEffect(() => {
    // `override: true` so hot-reload during dev does not hit
    // "method already registered" on re-mount.
    registerAllHandlers(globalRegistry, { override: true })
    pythonClient.setDispatcher(globalDispatcher)
    installExtensions()

    // Signal the main process that React has painted.
    // The main process waits for this before closing the loading window.
    try { window.electronAPI?.signalRendererReady?.() } catch {}

    return () => {
      pythonClient.setDispatcher(null)
    }
  }, [])

  // Connect to the Python backend WebSocket once the sidecar is ready.
  // The command bus (installed above) will then receive server-push
  // notifications (map.addLayer, map.flyTo, ...) over this socket.
  useEffect(() => {
    const api = window.electronAPI
    if (!api) {
      // Browser-only dev mode: nothing to connect to.
      return
    }

    let cancelled = false
    let wsToken: string | null = null
    let currentPort: number | null = null
    let unsubscribe: (() => void) | null = null
    let unsubscribeToken: (() => void) | null = null

    // Fetch WebSocket token from main process
    const fetchToken = async () => {
      try {
        wsToken = await api.getPythonWsToken()
        if (wsToken) {
          console.log('[App] WebSocket token fetched successfully')
        }
      } catch (e) {
        console.warn('[App] Failed to fetch WebSocket token:', e)
      }
    }

    const tryConnect = (port: number | null | undefined) => {
      if (cancelled || !port) return
      console.log('[App] Connecting to Python backend on port', port, wsToken ? '(with auth token)' : '(no token)')
      // 先断开旧连接（防止 token 错误时缓存了无效连接）
      pythonClient.disconnect()
      pythonClient.connect(port, wsToken ?? undefined)
    }

    // Initialize async
    const initialize = async () => {
      // 0. Fetch token first
      await fetchToken()

      // 1. Poll the current status — handles the case where the sidecar
      //    became 'ready' before this effect ran (we'd have missed the event).
      const s = await api.getPythonStatus()
      if (s?.status === 'ready') {
        // If still no token, try again
        if (!wsToken) await fetchToken()
        tryConnect(s.port)
      }

      // 2. Subscribe to future status changes.
      unsubscribe = api.onPythonStatusChanged((status) => {
        if (status.status === 'ready') {
          currentPort = status.port ?? null
          // Use token from status, or fetch if not available
          if (!wsToken) {
            fetchToken().then(() => {
              if (currentPort) tryConnect(currentPort)
            })
          } else {
            tryConnect(status.port)
          }
        } else if (status.status === 'stopped' || status.status === 'error') {
          pythonClient.disconnect()
        }
      })

      // 3. Listen for token events from main — when token arrives, reconnect if we have a port
      unsubscribeToken = api.onPythonWsToken?.((token: string) => {
        wsToken = token
        console.log('[App] Received WebSocket token, reconnecting...')
        if (currentPort) {
          // Reconnect with token
          tryConnect(currentPort)
        }
      }) || (() => {})
    }

    initialize()

    return () => {
      cancelled = true
      if (unsubscribe) unsubscribe()
      if (unsubscribeToken) unsubscribeToken()
    }
  }, [])

  // Load persisted settings from Electron on app startup
  useEffect(() => {
    loadFromElectron()
  }, [loadFromElectron])

  // Sync theme class to <html> element so CSS variables apply globally
  useEffect(() => {
    const root = document.documentElement

    const applyTheme = () => {
      let isDark: boolean
      if (theme === 'system') {
        isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      } else {
        isDark = theme === 'dark'
      }
      root.classList.toggle('light', !isDark)
      // Notify Electron main process to update Windows title bar overlay
      ;(window as any).electronAPI?.setTitleBarTheme?.(isDark)
    }

    applyTheme()

    // Listen for system theme changes when using 'system' mode
    if (theme === 'system') {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
      mediaQuery.addEventListener('change', applyTheme)
      return () => mediaQuery.removeEventListener('change', applyTheme)
    }
  }, [theme])

  return (
    <>
      <MainLayout />
      <DialogHost />
    </>
  )
}

export default App
