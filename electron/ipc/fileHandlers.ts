import { ipcMain, dialog } from 'electron'
import { createReadStream, createWriteStream } from 'fs'
import { readFile, writeFile, stat, lstat, readdir, mkdir } from 'fs/promises'
import { pipeline } from 'stream/promises'
import { extname, basename, dirname, isAbsolute, join, normalize, parse, relative, resolve } from 'path'
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

type ImportProgressPhase = 'scanning' | 'copying' | 'done' | 'error'

interface ImportProgressPayload {
  id: string
  phase: ImportProgressPhase
  currentName?: string
  copiedBytes: number
  totalBytes: number
  copiedFiles: number
  totalFiles: number
  errors?: string[]
}

interface ImportPathEntry {
  source: string
  destination: string
  isDirectory: boolean
  size: number
}

interface ImportSourcePlan {
  source: string
  destination: string
  isDirectory: boolean
  files: ImportPathEntry[]
}

function toComparablePath(path: string): string {
  const resolved = resolve(normalize(path))
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

function isSameOrInside(parentPath: string, childPath: string): boolean {
  const parent = toComparablePath(parentPath)
  const child = toComparablePath(childPath)
  const rel = relative(parent, child)
  return rel === '' || (!!rel && !rel.startsWith('..') && !isAbsolute(rel))
}

function isSubpath(parentPath: string, childPath: string): boolean {
  const parent = toComparablePath(parentPath)
  const child = toComparablePath(childPath)
  const rel = relative(parent, child)
  return !!rel && !rel.startsWith('..') && !isAbsolute(rel)
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function uniqueDestinationPath(
  destinationDir: string,
  name: string,
  reservedPaths: Set<string>,
): Promise<string> {
  const initial = resolve(destinationDir, name)
  if (!reservedPaths.has(toComparablePath(initial)) && !(await pathExists(initial))) return initial

  const parsed = parse(name)
  for (let idx = 2; idx < 10000; idx += 1) {
    const suffix = idx === 2 ? ' copy' : ` copy ${idx - 1}`
    const candidateName = parsed.ext
      ? `${parsed.name}${suffix}${parsed.ext}`
      : `${parsed.name}${suffix}`
    const candidate = resolve(destinationDir, candidateName)
    if (!reservedPaths.has(toComparablePath(candidate)) && !(await pathExists(candidate))) return candidate
  }

  throw new Error(`Could not create a unique destination for ${name}`)
}

async function scanImportSource(source: string, destination: string): Promise<ImportSourcePlan> {
  const sourceLinkStats = await lstat(source)
  if (sourceLinkStats.isSymbolicLink()) {
    throw new Error('Symbolic links are not supported for workspace import')
  }

  const sourceStats = await stat(source)
  const isDirectory = sourceStats.isDirectory()
  const files: ImportPathEntry[] = []

  async function walk(src: string, dest: string): Promise<void> {
    const linkStats = await lstat(src)
    if (linkStats.isSymbolicLink()) {
      return
    }

    const srcStats = await stat(src)
    if (srcStats.isDirectory()) {
      const entries = await readdir(src, { withFileTypes: true })
      for (const entry of entries) {
        await walk(join(src, entry.name), join(dest, entry.name))
      }
      return
    }

    if (srcStats.isFile()) {
      files.push({
        source: src,
        destination: dest,
        isDirectory: false,
        size: srcStats.size,
      })
    }
  }

  if (isDirectory) {
    await walk(source, destination)
  } else {
    files.push({
      source,
      destination,
      isDirectory: false,
      size: sourceStats.size,
    })
  }

  return { source, destination, isDirectory, files }
}

async function copyFileWithProgress(
  source: string,
  destination: string,
  onBytes: (bytes: number) => void,
): Promise<void> {
  await mkdir(dirname(destination), { recursive: true })

  const reader = createReadStream(source)
  reader.on('data', (chunk: Buffer) => onBytes(chunk.byteLength))

  await pipeline(reader, createWriteStream(destination))
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

  // Import dropped files/folders into the active workspace.
  // The renderer cannot reliably copy external folders itself across platforms:
  // Electron exposes native paths through preload, and the main process owns the
  // recursive copy plus progress events.
  ipcMain.handle(
    'file:import-paths',
    async (
      event,
      payload: { sources?: string[]; destinationDir?: string; workspaceRoot?: string },
    ): Promise<{
      success: boolean
      id?: string
      imported?: Array<{ source: string; destination: string; isDirectory: boolean }>
      totalBytes?: number
      copiedBytes?: number
      totalFiles?: number
      copiedFiles?: number
      errors?: string[]
      error?: string
    }> => {
      const id = `import_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
      const sources = Array.from(new Set((payload.sources || []).filter(Boolean))).map((p) => resolve(normalize(p)))
      const destinationDir = payload.destinationDir ? resolve(normalize(payload.destinationDir)) : ''
      const workspaceRoot = payload.workspaceRoot ? resolve(normalize(payload.workspaceRoot)) : ''
      const errors: string[] = []

      const emitProgress = (progress: Omit<ImportProgressPayload, 'id'>) => {
        event.sender.send('file:import-progress', { id, ...progress })
      }

      if (sources.length === 0) {
        return { success: false, error: 'No files or folders were provided for import' }
      }
      if (!destinationDir || !isPathSafe(destinationDir)) {
        return { success: false, error: 'Access denied: invalid destination directory' }
      }
      if (!workspaceRoot || !isPathSafe(workspaceRoot)) {
        return { success: false, error: 'Access denied: invalid workspace root' }
      }
      if (!isSameOrInside(workspaceRoot, destinationDir)) {
        return { success: false, error: 'Destination must be inside the active workspace' }
      }

      try {
        const workspaceStats = await stat(workspaceRoot)
        if (!workspaceStats.isDirectory()) {
          return { success: false, error: 'Workspace root is not a directory' }
        }
        const destinationStats = await stat(destinationDir)
        if (!destinationStats.isDirectory()) {
          return { success: false, error: 'Destination is not a directory' }
        }
      } catch (error) {
        return { success: false, error: `Destination is not available: ${String(error)}` }
      }

      try {
        emitProgress({
          phase: 'scanning',
          copiedBytes: 0,
          totalBytes: 0,
          copiedFiles: 0,
          totalFiles: 0,
        })

        const plans: ImportSourcePlan[] = []
        const reservedDestinations = new Set<string>()
        for (const source of sources) {
          try {
            const sourceStats = await stat(source)
            if (!sourceStats.isFile() && !sourceStats.isDirectory()) {
              errors.push(`${source}: unsupported file type`)
              continue
            }

            if (sourceStats.isDirectory() && isSameOrInside(source, destinationDir)) {
              errors.push(`${source}: destination cannot be inside the source folder`)
              continue
            }

            const destination = await uniqueDestinationPath(destinationDir, basename(source), reservedDestinations)
            if (isSubpath(source, destination)) {
              errors.push(`${source}: destination cannot be inside the source folder`)
              continue
            }
            reservedDestinations.add(toComparablePath(destination))
            plans.push(await scanImportSource(source, destination))
          } catch (error) {
            errors.push(`${source}: ${String(error)}`)
          }
        }

        const totalFiles = plans.reduce((sum, plan) => sum + plan.files.length, 0)
        const totalBytes = plans.reduce(
          (sum, plan) => sum + plan.files.reduce((fileSum, file) => fileSum + file.size, 0),
          0,
        )
        let copiedFiles = 0
        let copiedBytes = 0
        let lastEmitAt = 0

        const emitCopyProgress = (currentName?: string, force = false) => {
          const now = Date.now()
          if (!force && now - lastEmitAt < 80) return
          lastEmitAt = now
          emitProgress({
            phase: 'copying',
            currentName,
            copiedBytes,
            totalBytes,
            copiedFiles,
            totalFiles,
            errors: errors.length > 0 ? errors : undefined,
          })
        }

        emitCopyProgress(undefined, true)

        for (const plan of plans) {
          if (plan.isDirectory) {
            await mkdir(plan.destination, { recursive: true })
          }

          for (const file of plan.files) {
            try {
              await copyFileWithProgress(file.source, file.destination, (bytes) => {
                copiedBytes += bytes
                emitCopyProgress(basename(file.source))
              })
              copiedFiles += 1
              emitCopyProgress(basename(file.source), true)
            } catch (error) {
              errors.push(`${file.source}: ${String(error)}`)
            }
          }
        }

        emitProgress({
          phase: errors.length > 0 ? 'error' : 'done',
          copiedBytes,
          totalBytes,
          copiedFiles,
          totalFiles,
          errors: errors.length > 0 ? errors : undefined,
        })

        return {
          success: errors.length === 0,
          id,
          imported: plans.map((plan) => ({
            source: plan.source,
            destination: plan.destination,
            isDirectory: plan.isDirectory,
          })),
          totalBytes,
          copiedBytes,
          totalFiles,
          copiedFiles,
          errors: errors.length > 0 ? errors : undefined,
          error: errors.length > 0 ? errors.join('\n') : undefined,
        }
      } catch (error) {
        emitProgress({
          phase: 'error',
          copiedBytes: 0,
          totalBytes: 0,
          copiedFiles: 0,
          totalFiles: 0,
          errors: [String(error)],
        })
        return { success: false, id, error: String(error), errors: [String(error)] }
      }
    },
  )

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
