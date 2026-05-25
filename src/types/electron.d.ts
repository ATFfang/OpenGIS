/**
 * Type declarations for packages without built-in TypeScript types.
 */

// shapefile — https://github.com/mbostock/shapefile
declare module 'shapefile' {
  export function read(
    shp: ArrayBuffer | string,
    dbf?: ArrayBuffer | string,
    options?: Record<string, any>
  ): Promise<GeoJSON.FeatureCollection>

  export function open(
    shp: ArrayBuffer | string,
    dbf?: ArrayBuffer | string,
    options?: Record<string, any>
  ): Promise<{
    read(): Promise<{ done: boolean; value: GeoJSON.Feature }>
    bbox: [number, number, number, number]
  }>
}

// @tmcw/togeojson — https://github.com/tmcw/togeojson
declare module '@tmcw/togeojson' {
  export function kml(doc: Document): GeoJSON.FeatureCollection
  export function gpx(doc: Document): GeoJSON.FeatureCollection
}

// Electron API bridge — must match electron/preload.ts exactly
interface Window {
  electronAPI?: {
    // File system
    openFileDialog: (filters?: any[]) => Promise<string[] | null>
    saveFileDialog: (defaultPath?: string) => Promise<string | null>
    readFile: (path: string) => Promise<{ success: boolean; content?: string; error?: string }>
    readFileAsBuffer: (path: string) => Promise<{ success: boolean; buffer?: ArrayBuffer; error?: string }>
    writeFile: (path: string, content: string) => Promise<{ success: boolean; error?: string }>
    getFileInfo: (path: string) => Promise<{ success: boolean; info?: any; error?: string }>
    readDirectory: (path: string) => Promise<{ success: boolean; entries?: any[]; error?: string }>
    openFolderDialog: () => Promise<string | null>
    deleteFile: (path: string) => Promise<{ success: boolean; error?: string }>
    renameFile: (oldPath: string, newPath: string) => Promise<{ success: boolean; error?: string }>
    ensureDirectory: (path: string) => Promise<{ success: boolean; error?: string }>
    // Python
    getPythonStatus: () => Promise<{ status: 'stopped' | 'starting' | 'ready' | 'error'; port?: number; error?: string; pythonPath?: string }>
    restartPython: () => Promise<{ status: 'stopped' | 'starting' | 'ready' | 'error'; port?: number; error?: string; pythonPath?: string }>
    getPythonPort: () => Promise<number | null>
    onPythonStatusChanged: (callback: (status: { status: 'stopped' | 'starting' | 'ready' | 'error'; port?: number; error?: string; pythonPath?: string }) => void) => () => void
    // Settings
    getSettings: () => Promise<any>
    setSetting: (key: string, value: any) => Promise<void>
    // Projects
    getProjects: () => Promise<{ projects: any[]; lastProjectId?: string }>
    createProject: (name: string, path: string) => Promise<any>
    openProject: (id: string) => Promise<any>
    renameProject: (id: string, newName: string) => Promise<any>
    deleteProject: (id: string) => Promise<{ success: boolean }>
    browseProjectFolder: () => Promise<{ canceled?: boolean; path?: string }>
    switchProject: () => Promise<{ success: boolean }>
    onProjectSelected: (callback: (project: any) => void) => () => void
    // App info
    getAppVersion: () => Promise<string>
    getPlatform: () => string
    // Python WebSocket auth
    getPythonWsToken: () => Promise<string>
    onPythonWsToken: (callback: (token: string) => void) => () => void
    // Logging
    getLogDir: () => Promise<string | null>
    openLogDir: () => void
    // Lifecycle
    signalRendererReady: () => void
  }
}
