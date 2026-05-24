import { contextBridge, ipcRenderer } from 'electron'

/**
 * Preload script — exposes safe APIs to the renderer process via contextBridge.
 * The renderer accesses these through `window.electronAPI`.
 */

const electronAPI = {
  // ---- File System ----
  openFileDialog: (filters?: Electron.FileFilter[]) =>
    ipcRenderer.invoke('file:open-dialog', filters),

  saveFileDialog: (defaultPath?: string) =>
    ipcRenderer.invoke('file:save-dialog', defaultPath),

  readFile: (path: string) =>
    ipcRenderer.invoke('file:read', path),

  readFileAsBuffer: (path: string) =>
    ipcRenderer.invoke('file:read-buffer', path),

  writeFile: (path: string, content: string) =>
    ipcRenderer.invoke('file:write', path, content),

  getFileInfo: (path: string) =>
    ipcRenderer.invoke('file:info', path),

  readDirectory: (path: string) =>
    ipcRenderer.invoke('file:read-dir', path),

  openFolderDialog: () =>
    ipcRenderer.invoke('file:open-folder-dialog'),

  deleteFile: (path: string) =>
    ipcRenderer.invoke('file:delete', path),

  renameFile: (oldPath: string, newPath: string) =>
    ipcRenderer.invoke('file:rename', oldPath, newPath),

  /**
   * Ensure a directory exists (creates intermediate parents as needed).
   * Used by features like Workflows that need to lazily create a
   * `workspace/workflows/` folder before saving the first file.
   */
  ensureDirectory: (path: string) =>
    ipcRenderer.invoke('file:mkdir', path),

  // ---- Python Backend ----
  getPythonStatus: () =>
    ipcRenderer.invoke('python:status'),

  restartPython: () =>
    ipcRenderer.invoke('python:restart'),

  getPythonPort: () =>
    ipcRenderer.invoke('python:get-port'),

  getPythonWsToken: () =>
    ipcRenderer.invoke('python:get-ws-token'),

  onPythonStatusChanged: (callback: (status: any) => void) => {
    const handler = (_event: any, status: any) => callback(status)
    ipcRenderer.on('python:status-changed', handler)
    return () => ipcRenderer.removeListener('python:status-changed', handler)
  },

  onPythonWsToken: (callback: (token: string) => void) => {
    const handler = (_event: any, token: string) => callback(token)
    ipcRenderer.on('python:ws-token', handler)
    return () => ipcRenderer.removeListener('python:ws-token', handler)
  },

  // ---- Settings ----
  getSettings: () =>
    ipcRenderer.invoke('settings:get'),

  setSetting: (key: string, value: any) =>
    ipcRenderer.invoke('settings:set', key, value),

  // ---- System ----
  getLogDir: () =>
    ipcRenderer.invoke('system:get-log-dir') as Promise<string | null>,

  openLogDir: () =>
    ipcRenderer.invoke('system:open-log-dir') as Promise<{ success: boolean; path?: string; error?: string }>,

  // ---- App Info ----
  getAppVersion: () =>
    ipcRenderer.invoke('app:version'),

  getPlatform: () => process.platform,

  // ---- Lifecycle ----
  signalRendererReady: () => ipcRenderer.send('renderer:ready'),

  // ---- Window ----
  setTitleBarTheme: (isDark: boolean) => ipcRenderer.send('window:set-titlebar-theme', isDark),
}

// Expose to renderer
contextBridge.exposeInMainWorld('electronAPI', electronAPI)

// Type declaration for renderer
export type ElectronAPI = typeof electronAPI
