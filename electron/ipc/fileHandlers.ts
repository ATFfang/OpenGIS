import { ipcMain, dialog } from 'electron'
import { readFile, writeFile, stat, readdir, mkdir } from 'fs/promises'
import { extname, basename, join, resolve, normalize } from 'path'
import { homedir } from 'os'

const HOME_DIR = homedir()
const SYSTEM_DIRS = [
  'C:\\Windows',
  'C:\\Program Files',
  'C:\\Program Files (x86)',
  'C:\\ProgramData',
  '/bin',
  '/sbin',
  '/usr/bin',
  '/usr/sbin',
  '/etc',
  '/sys',
  '/proc',
]

function isPathSafe(requestedPath: string): boolean {
  // Reject traversal patterns
  if (requestedPath.includes('..')) {
    return false
  }
  const resolved = resolve(normalize(requestedPath))
  // Reject system directories
  for (const sysDir of SYSTEM_DIRS) {
    if (resolved.toLowerCase().startsWith(sysDir.toLowerCase())) {
      return false
    }
  }
  return true
}

/**
 * Register file system IPC handlers.
 * These provide safe file access from the renderer process.
 */
export function registerFileHandlers(): void {
  // Open file dialog
  ipcMain.handle('file:open-dialog', async (_event, filters?: Electron.FileFilter[]) => {
    const defaultFilters: Electron.FileFilter[] = filters ?? [
      {
        name: 'GIS Files',
        extensions: ['shp', 'geojson', 'json', 'gpkg', 'tif', 'tiff', 'kml', 'kmz', 'gml', 'csv'],
      },
      {
        name: 'Vector Files',
        extensions: ['shp', 'geojson', 'json', 'gpkg', 'kml', 'kmz', 'gml'],
      },
      {
        name: 'Raster Files',
        extensions: ['tif', 'tiff', 'nc', 'hdf5', 'h5'],
      },
      {
        name: 'All Files',
        extensions: ['*'],
      },
    ]

    const result = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections'],
      filters: defaultFilters,
    })

    if (result.canceled) {
      return null
    }

    return result.filePaths
  })

  // Save file dialog
  ipcMain.handle('file:save-dialog', async (_event, defaultPath?: string) => {
    const result = await dialog.showSaveDialog({
      defaultPath,
      filters: [
        { name: 'GeoJSON', extensions: ['geojson'] },
        { name: 'Shapefile', extensions: ['shp'] },
        { name: 'GeoPackage', extensions: ['gpkg'] },
        { name: 'GeoTIFF', extensions: ['tif'] },
        { name: 'CSV', extensions: ['csv'] },
        { name: 'PNG Image', extensions: ['png'] },
        { name: 'All Files', extensions: ['*'] },
      ],
    })

    if (result.canceled) {
      return null
    }

    return result.filePath
  })

  // Read file as text
  ipcMain.handle('file:read', async (_event, path: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const info = await stat(path)
      if (info.size > 50 * 1024 * 1024) {
        return { success: false, error: `File too large (${(info.size / 1024 / 1024).toFixed(1)} MB, max 50 MB)` }
      }
      const content = await readFile(path, 'utf-8')
      return { success: true, content }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Read file as buffer (for binary files)
  ipcMain.handle('file:read-buffer', async (_event, path: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const info = await stat(path)
      if (info.size > 100 * 1024 * 1024) {
        return { success: false, error: `File too large (${(info.size / 1024 / 1024).toFixed(1)} MB, max 100 MB)` }
      }
      const buffer = await readFile(path)
      const arrayBuffer = buffer.buffer.slice(
        buffer.byteOffset,
        buffer.byteOffset + buffer.byteLength,
      )
      return { success: true, buffer: arrayBuffer }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Write file
  ipcMain.handle('file:write', async (_event, path: string, content: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      await writeFile(path, content, 'utf-8')
      return { success: true }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Get file info
  ipcMain.handle('file:info', async (_event, path: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const stats = await stat(path)
      return {
        success: true,
        info: {
          path,
          name: basename(path),
          extension: extname(path).toLowerCase(),
          size: stats.size,
          isDirectory: stats.isDirectory(),
          modifiedTime: stats.mtime.toISOString(),
        },
      }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Read directory contents (for Asset Explorer)
  ipcMain.handle('file:read-dir', async (_event, dirPath: string) => {
    if (!isPathSafe(dirPath)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const entries = await readdir(dirPath, { withFileTypes: true })
      const results = []

      for (const entry of entries) {
        // Skip hidden files and common non-data directories
        if (entry.name.startsWith('.') || entry.name === 'node_modules' || entry.name === '__pycache__') {
          continue
        }

        const fullPath = join(dirPath, entry.name)
        try {
          const stats = await stat(fullPath)
          results.push({
            path: fullPath,
            name: entry.name,
            type: entry.isDirectory() ? 'directory' : 'file',
            extension: entry.isDirectory() ? '' : extname(entry.name).toLowerCase(),
            size: stats.size,
            modifiedTime: stats.mtime.toISOString(),
            isDirectory: entry.isDirectory(),
          })
        } catch {
          // Skip files we can't stat (permission errors, etc.)
        }
      }

      return { success: true, entries: results }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Open folder dialog (for Asset Explorer workspace selection)
  ipcMain.handle('file:open-folder-dialog', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
      title: 'Select Workspace Folder',
    })

    if (result.canceled || result.filePaths.length === 0) {
      return null
    }

    return result.filePaths[0]
  })

  // Delete file or directory
  ipcMain.handle('file:delete', async (_event, path: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const { rm } = await import('fs/promises')
      const stats = await stat(path)
      if (stats.isDirectory()) {
        await rm(path, { recursive: true, force: true })
      } else {
        await rm(path)
      }
      return { success: true }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Rename file or directory
  ipcMain.handle('file:rename', async (_event, oldPath: string, newPath: string) => {
    if (!isPathSafe(oldPath) || !isPathSafe(newPath)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      const { rename } = await import('fs/promises')
      await rename(oldPath, newPath)
      return { success: true }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })

  // Ensure a directory exists (mkdir -p semantics). Safe to call repeatedly.
  // Used by features (Workflows, run-archive, ...) that need to lazily
  // create subdirectories under the workspace without forcing the user to
  // create them in Explorer first.
  ipcMain.handle('file:mkdir', async (_event, path: string) => {
    if (!isPathSafe(path)) {
      return { success: false, error: 'Access denied: invalid or unsafe path' }
    }
    try {
      await mkdir(path, { recursive: true })
      return { success: true }
    } catch (error) {
      return { success: false, error: String(error) }
    }
  })
}
