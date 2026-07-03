import { app, BrowserWindow, shell, ipcMain, nativeImage, nativeTheme } from 'electron'
import { join } from 'path'
import { readFileSync, existsSync } from 'fs'
import { PythonManager } from './ipc/pythonManager'
import { ensurePythonEnv } from './ipc/pythonSetup'
import { registerFileHandlers } from './ipc/fileHandlers'
import { registerSettingsHandlers, loadProjects } from './ipc/settingsHandlers'
import { createMenu } from './menu'
import { initLogger, getLogDir } from './logger'

/**
 * Read the saved theme from settings.json synchronously (before any window opens).
 * Returns 'dark' | 'light' based on saved preference or system default.
 */
function getStartupTheme(): 'dark' | 'light' {
  try {
    const settingsPath = join(app.getPath('userData'), 'settings.json')
    if (existsSync(settingsPath)) {
      const raw = readFileSync(settingsPath, 'utf-8')
      const settings = JSON.parse(raw)
      const theme = settings?.appearance?.theme
      if (theme === 'light') return 'light'
      if (theme === 'dark') return 'dark'
      // 'system' — follow OS
    }
  } catch { /* ignore */ }
  return nativeTheme.shouldUseDarkColors ? 'dark' : 'light'
}

// Prevent multiple instances
const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  app.quit()
}

let mainWindow: BrowserWindow | null = null
let loadingWindow: BrowserWindow | null = null
let pythonManager: PythonManager | null = null

// Resolve the app icon path for both dev and packaged mode
function getAppIcon(): Electron.NativeImage {
  const iconPath = app.isPackaged
    ? join(process.resourcesPath, 'icons', 'app-icon.png')
    : join(__dirname, '../../resources/icons/app-icon.png')
  return nativeImage.createFromPath(iconPath)
}

function createLoadingWindow(): Promise<void> {
  const appIcon = getAppIcon()
  const startupTheme = getStartupTheme()
  const isDarkLoading = startupTheme === 'dark'

  loadingWindow = new BrowserWindow({
    width: 580,
    height: 680,
    resizable: false,
    frame: false,
    backgroundColor: isDarkLoading ? '#0a0c10' : '#ffffff',
    show: true,
    center: true,
    alwaysOnTop: false,
    icon: appIcon,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  })

  const isDev = !!process.env.ELECTRON_RENDERER_URL
  const loadingPath = isDev
    ? join(__dirname, '../../loading.html')
    : join(__dirname, '../renderer/loading.html')

  console.log('[loading] Loading file:', loadingPath)

  loadingWindow.webContents.on('did-fail-load', (event, code, desc) => {
    console.error('[loading] did-fail-load:', code, desc)
  })

  loadingWindow.on('closed', () => {
    console.log('[loading] Window CLOSED')
    loadingWindow = null
  })

  // Wait for the page to finish loading before resolving,
  // so that the caller knows the renderer is ready to receive IPC messages.
  return new Promise((resolve) => {
    loadingWindow!.webContents.once('did-finish-load', () => {
      console.log('[loading] did-finish-load — sending step 0')
      loadingWindow?.webContents.send('loading:theme', startupTheme)
      updateLoadingProgress(0, 'Initializing application…')
      resolve()
    })
    loadingWindow!.loadFile(loadingPath).catch(err => {
      console.error('[loading] loadFile error:', err)
      resolve() // still resolve so app startup continues
    })
  })
}

function updateLoadingProgress(step: number, status: string): void {
  loadingWindow?.webContents.send('loading:progress', { step, status })
}

// ─── Main Window ────────────────────────────────────────────────
function createWindow(): void {
  const appIcon = getAppIcon()

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: false,
    frame: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    ...(process.platform === 'win32' ? {
      titleBarOverlay: {
        color: '#0a0c10',
        symbolColor: '#e2e8f0',
        height: 32,
      },
    } : {}),
    backgroundColor: '#0a0c10',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false, // Need false for preload to work with some APIs
      preload: join(__dirname, '../preload/preload.js'),
      webgl: true,
      webSecurity: true,
    },
    icon: appIcon,
  })



  // Do NOT show here. Wait for renderer to signal "renderer:ready"
  // so we only show after React has painted (no black flash).

  // Open external links in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // Load the renderer
  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  // Open DevTools in development
  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

  mainWindow.on('closed', () => {
    mainWindow = null
    // Detach so PythonManager doesn't keep a destroyed BrowserWindow alive.
    pythonManager?.setMainWindow(null)
  })
}

function registerPythonIpcHandlers(): void {
  // Register early so the renderer can call them during setup.
  ipcMain.handle('python:status', () => {
    return pythonManager?.getStatus() ?? { status: 'stopped' }
  })

  ipcMain.handle('python:restart', async () => {
    await pythonManager?.restart()
    return pythonManager?.getStatus()
  })

  ipcMain.handle('python:get-port', () => {
    return pythonManager?.getPort() ?? null
  })

  ipcMain.handle('python:get-ws-token', () => {
    return pythonManager?.getWsToken() ?? null
  })
}

async function initializePythonBackend(): Promise<void> {
  pythonManager = new PythonManager()
  pythonManager.setMainWindow(mainWindow)

  try {
    await pythonManager.start()
    updateLoadingProgress(3, 'Python backend connected!')
    mainWindow?.webContents.send('python:status-changed', pythonManager.getStatus())
  } catch (error) {
    console.error('Failed to start Python backend:', error)
    mainWindow?.webContents.send('python:status-changed', {
      status: 'error',
      error: String(error),
    })
  }
}

// App lifecycle
app.whenReady().then(async () => {
  console.log('[loading] app.whenReady start');
  // Initialise logging FIRST so every subsequent step writes to disk.
  const logDir = initLogger()
  console.log(`[main] App started. Log dir: ${logDir}`)

  console.log('[loading] Step 1: createLoadingWindow');
  // ── Step 1: create and show loading window ───────────────────────────
  await createLoadingWindow()

  // Register IPC handlers
  registerFileHandlers()
  registerSettingsHandlers()

  // ── Windows title bar overlay theme ───────────────────────────
  if (process.platform === 'win32') {
    // Renderer notifies main process when theme changes
    ipcMain.on('window:set-titlebar-theme', (_event, isDark: boolean) => {
      mainWindow?.setTitleBarOverlay({
        color: isDark ? '#0a0c10' : '#ffffff',
        symbolColor: isDark ? '#e2e8f0' : '#1e293b',
        height: 32,
      })
    })

    // Listen for OS system theme changes (for 'system' theme mode)
    nativeTheme.on('updated', () => {
      mainWindow?.setTitleBarOverlay({
        color: nativeTheme.shouldUseDarkColors ? '#0a0c10' : '#ffffff',
        symbolColor: nativeTheme.shouldUseDarkColors ? '#e2e8f0' : '#1e293b',
        height: 32,
      })
    })
  }

  // ── renderer:ready handler ────────────────────────────────
  // The renderer (React) sends this when it has painted the UI.
  // We close the loading window and show the main window.
  ipcMain.on('renderer:ready', () => {
    console.log('[loading] renderer:ready received')
    ;(global as any).__rendererIsReady = true
    // Re-send cached project selection in case it arrived before the listener was registered
    if ((global as any).__projectSelected && (global as any).__selectedProject) {
      mainWindow?.webContents.send('project:selected', (global as any).__selectedProject)
    }
    // Don't close loading yet — wait for project selection
    if ((global as any).__projectSelected) {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
        mainWindow.show()
      }
      if (loadingWindow && !loadingWindow.isDestroyed()) {
        loadingWindow.close()
      }
    }
  })

  // ── Close loading window on request ───────────────────
  ipcMain.on('loading:close-window', () => {
    if (loadingWindow && !loadingWindow.isDestroyed()) {
      loadingWindow.close()
    }
  })

  // ── Project selection from loading window ───────────────────
  ipcMain.on('loading:project-selected', (_event, project: any) => {
    // Ignore duplicate selections (user may click multiple times during loading)
    if ((global as any).__projectSelected) return
    console.log('[loading] Project selected:', project.name, project.path)
    ;(global as any).__projectSelected = true
    ;(global as any).__selectedProject = project
    // Send workspace path to the main window renderer
    mainWindow?.webContents.send('project:selected', project)
    // If renderer already ready, show main BEFORE closing loading
    // to avoid a brief "no windows" state that triggers window-all-closed
    if ((global as any).__rendererIsReady) {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
        mainWindow.show()
      }
      if (loadingWindow && !loadingWindow.isDestroyed()) {
        loadingWindow.close()
      }
    }
  })

  // ── Switch project (return to project selector from main UI) ──
  ipcMain.handle('app:switch-project', async () => {
    // Hide main window
    mainWindow?.hide()
    // Reset project selection state
    ;(global as any).__projectSelected = false
    ;(global as any).__selectedProject = null

    // Re-create loading window in project-selector mode (skip loading steps)
    const appIcon = getAppIcon()
    const startupTheme = getStartupTheme()
    const isDarkLoading = startupTheme === 'dark'

    loadingWindow = new BrowserWindow({
      width: 580,
      height: 680,
      resizable: false,
      frame: false,
      backgroundColor: isDarkLoading ? '#0a0c10' : '#ffffff',
      show: true,
      center: true,
      alwaysOnTop: false,
      icon: appIcon,
      webPreferences: {
        nodeIntegration: true,
        contextIsolation: false,
      },
    })

    const isDev = !!process.env.ELECTRON_RENDERER_URL
    const loadingPath = isDev
      ? join(__dirname, '../../loading.html')
      : join(__dirname, '../renderer/loading.html')

    loadingWindow.on('closed', () => {
      loadingWindow = null
      // If user closed the selector without picking a project, re-show main
      if (!(global as any).__projectSelected) {
        mainWindow?.show()
      }
    })

    await new Promise<void>((resolve) => {
      loadingWindow!.webContents.once('did-finish-load', () => {
        loadingWindow?.webContents.send('loading:theme', startupTheme)
        resolve()
      })
      loadingWindow!.loadFile(loadingPath)
    })

    // Immediately show project selector (skip loading steps)
    const projectsData = await loadProjects()
    loadingWindow?.webContents.send('loading:show-projects', projectsData)
    return { success: true }
  })

  ipcMain.handle('system:get-log-dir', () => getLogDir())
  ipcMain.handle('system:open-log-dir', async () => {
    const dir = getLogDir()
    if (!dir) return { success: false, error: 'Log directory not initialised' }
    const err = await shell.openPath(dir)
    if (err) return { success: false, error: err }
    return { success: true, path: dir }
  })

  // ── Write binary data (ArrayBuffer) to file ─────────────────────
  ipcMain.handle('file:write-binary', (_event, filePath: string, buffer: ArrayBuffer) => {
    try {
      const { writeFileSync, mkdirSync } = require('fs')
      const { dirname } = require('path')
      mkdirSync(dirname(filePath), { recursive: true })
      writeFileSync(filePath, Buffer.from(buffer))
      return { success: true, path: filePath }
    } catch (err: any) {
      return { success: false, error: err?.message ?? String(err) }
    }
  })

  // ── Show file/folder in OS file manager ──────────────────────────
  ipcMain.handle('file:show-in-folder', (_event, filePath: string) => {
    try {
      shell.showItemInFolder(filePath)
      return { success: true }
    } catch (err: any) {
      return { success: false, error: err?.message ?? String(err) }
    }
  })

  console.log('[loading] Step 2: createWindow');
  // ── Step 2: create main window (hidden) ───────────────────
  updateLoadingProgress(1, 'Preparing workspace…')
  createWindow()

  // Create application menu (attached to main window)
  createMenu(mainWindow!)

  console.log('[loading] Step 3: start Python');
  // ── Step 3: ensure Python env then start backend ───────────────
  updateLoadingProgress(2, 'Starting Python backend…')

  // Register IPC handlers early so the renderer can call them during setup
  registerPythonIpcHandlers()

  // Always run ensurePythonEnv — it checks version and upgrades deps if needed
  const tempManager = new PythonManager()
  const needsSetup = !tempManager.hasVenv()

  if (needsSetup) {
    console.log('[loading] No .venv found — running first-launch setup')
    loadingWindow?.webContents.send('loading:install-start', {})
  } else {
    console.log('[loading] .venv exists — checking version')
  }

  {
    const runSetup = async (): Promise<void> => {
      const backendPath = tempManager.getBackendPath()
      const venvPath = tempManager.getVenvPath()
      await ensurePythonEnv(backendPath, venvPath, (progress) => {
        if (needsSetup) {
          loadingWindow?.webContents.send('loading:install-progress', progress)
        }
      }, app.getVersion())
    }

    try {
      await runSetup()
      if (needsSetup) {
        loadingWindow?.webContents.send('loading:install-done', {})
      }
      console.log('[loading] Python env ready')
    } catch (err) {
      console.error('[loading] Python env setup failed:', err)
      // Show install UI on error even if it wasn't shown before (version upgrade case)
      if (!needsSetup) {
        loadingWindow?.webContents.send('loading:install-start', {})
      }
      loadingWindow?.webContents.send('loading:install-error', { error: String(err) })

      // Wait for user to retry — also resolves if loading window is closed
      await new Promise<void>((resolve) => {
        const onRetry = async () => {
          loadingWindow?.webContents.send('loading:install-start', {})
          try {
            await runSetup()
            loadingWindow?.webContents.send('loading:install-done', {})
            ipcMain.removeListener('loading:install-retry', onRetry)
            resolve()
          } catch (retryErr) {
            loadingWindow?.webContents.send('loading:install-error', { error: String(retryErr) })
          }
        }
        ipcMain.on('loading:install-retry', onRetry)
        // Resolve on window close so app startup continues (avoids zombie)
        const onClosed = () => {
          ipcMain.removeListener('loading:install-retry', onRetry)
          resolve()
        }
        loadingWindow?.on('closed', onClosed)
      })
    }
  }

  await initializePythonBackend()

  console.log('[loading] Step 4: done');
  // ── Step 4: done ────────────────────────────────────────
  updateLoadingProgress(3, 'Ready!')

  // All backend steps done. Send project list to loading window
  // so user can select a project before entering main UI.
  console.log('[loading] Step 4: backend ready, sending project list...')
  const projectsData = await loadProjects()
  loadingWindow?.webContents.send('loading:show-projects', projectsData)

  // The loading window will send 'loading:project-selected' when user picks a project.
  // Then renderer:ready + project-selected together trigger the transition.
  if ((global as any).__projectSelected && (global as any).__rendererIsReady && loadingWindow && !loadingWindow.isDestroyed()) {
    mainWindow?.show()
    loadingWindow.close()
  }
  // Otherwise, ipcMain.on('renderer:ready') will close loading + show main.
  app.on('activate', async () => {
    // Check for any existing window (visible or minimized)
    const existingWindow = BrowserWindow.getAllWindows().find(w => !w.isDestroyed())
    if (existingWindow) {
      // Restore minimized window or bring hidden window to front
      if (existingWindow.isMinimized()) existingWindow.restore()
      existingWindow.show()
      existingWindow.focus()
    } else {
      createWindow()
      // Re-inject the new main window into PythonManager
      if (pythonManager) {
        pythonManager.setMainWindow(mainWindow)
        // Restart Python if it was stopped (e.g. by window-all-closed on non-macOS,
        // or if it crashed while the app was in the background)
        const status = pythonManager.getStatus()
        if (status.status === 'stopped' || status.status === 'error') {
          try {
            await pythonManager.start()
            mainWindow?.webContents.send('python:status-changed', pythonManager.getStatus())
          } catch (err) {
            console.error('[main] Failed to restart Python on activate:', err)
          }
        }
      }
    }
  })
}).catch((err) => {
  console.error('[main] Fatal startup error:', err)
  process.exit(1)
})

app.on('window-all-closed', () => {
  // On macOS, closing all windows doesn't quit the app (stays in dock).
  // Keep Python backend alive so it's ready when the user re-opens.
  // The backend is stopped in 'before-quit' when the app truly exits.
  if (process.platform !== 'darwin') {
    pythonManager?.stop()
    app.quit()
  }
})

app.on('before-quit', () => {
  pythonManager?.stop()
})
