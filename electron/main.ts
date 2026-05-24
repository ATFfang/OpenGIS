import { app, BrowserWindow, shell, ipcMain, nativeImage, nativeTheme } from 'electron'
import { join } from 'path'
import { PythonManager } from './ipc/pythonManager'
import { registerFileHandlers } from './ipc/fileHandlers'
import { registerSettingsHandlers } from './ipc/settingsHandlers'
import { createMenu } from './menu'
import { initLogger, getLogDir } from './logger'

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

  loadingWindow = new BrowserWindow({
    width: 480,
    height: 580,
    resizable: false,
    frame: false,
    backgroundColor: '#ffffff',
    show: true,
    center: true,
    alwaysOnTop: true,
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

async function initializePythonBackend(): Promise<void> {
  pythonManager = new PythonManager()
  // Wire the renderer window so PythonManager can forward status / token
  // events. Without this, any stdout match in PythonManager would hit a
  // ReferenceError on a non-existent `mainWindow` global.
  pythonManager.setMainWindow(mainWindow)

  // Listen for Python status requests from renderer
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

  // WebSocket auth token handler
  ipcMain.handle('python:get-ws-token', () => {
    return pythonManager?.getWsToken() ?? null
  })

  try {
    await pythonManager.start()
    // Notify loading window that Python is ready (step 3 done)
    updateLoadingProgress(3, 'Python backend connected!')
    // Notify renderer that Python backend is ready
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
    if (loadingWindow && !loadingWindow.isDestroyed()) {
      console.log('[loading] Closing loading window')
      loadingWindow.close()
    }
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
      console.log('[loading] Showing main window')
      mainWindow.show()
    }
  })

  ipcMain.handle('system:get-log-dir', () => getLogDir())
  ipcMain.handle('system:open-log-dir', async () => {
    const dir = getLogDir()
    if (!dir) return { success: false, error: 'Log directory not initialised' }
    const err = await shell.openPath(dir)
    if (err) return { success: false, error: err }
    return { success: true, path: dir }
  })

  console.log('[loading] Step 2: createWindow');
  // ── Step 2: create main window (hidden) ───────────────────
  updateLoadingProgress(1, 'Preparing workspace…')
  createWindow()

  // Create application menu (attached to main window)
  createMenu(mainWindow!)

  console.log('[loading] Step 3: start Python');
  // ── Step 3: start Python backend ────────────────────────
  updateLoadingProgress(2, 'Starting Python backend…')
  await initializePythonBackend()

  console.log('[loading] Step 4: done');
  // ── Step 4: done ────────────────────────────────────────
  updateLoadingProgress(3, 'Ready!')

  // All backend steps done. Now wait for renderer to be ready.
  console.log('[loading] Step 4: backend ready, waiting for renderer...')
  updateLoadingProgress(3, 'Loading UI…')

  // The renderer will send 'renderer:ready' when React has painted.
  // See: src/main.tsx (or App.tsx) useEffect.
  if ((global as any).__rendererIsReady && loadingWindow && !loadingWindow.isDestroyed()) {
    loadingWindow.close()
    mainWindow?.show()
  }
  // Otherwise, ipcMain.on('renderer:ready') will close loading + show main.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
}).catch((err) => {
  console.error('[main] Fatal startup error:', err)
  process.exit(1)
})

app.on('window-all-closed', () => {
  // Shutdown Python backend
  pythonManager?.stop()

  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  pythonManager?.stop()
})
