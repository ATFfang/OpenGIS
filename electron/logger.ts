/**
 * Electron main-process logger.
 *
 * Writes to <userData>/logs/electron-main-YYYY-MM-DD.log in addition to stdout.
 * Also exposes the log directory so the renderer can open it in the OS file
 * explorer via a "Reveal logs" button.
 *
 * The same directory is shared with the Python sidecar (via --log-dir), so
 * everything ends up in one place:
 *
 *   <userData>/logs/
 *     ├─ electron-main-2026-04-21.log
 *     ├─ python-stdout-2026-04-21.log     (written by pythonManager)
 *     ├─ backend-2026-04-21.log            (written by Python logging)
 *     └─ ...
 */

import { app } from 'electron'
import { createWriteStream, mkdirSync, readdirSync, statSync, unlinkSync, WriteStream } from 'fs'
import { join } from 'path'

const MAX_LOG_DIR_BYTES = 20 * 1024 * 1024  // 20 MB

let stream: WriteStream | null = null
let logDir: string | null = null
let installed = false

/**
 * Remove oldest log files when the directory exceeds MAX_LOG_DIR_BYTES.
 * Keeps the current day's log untouched.
 */
function pruneOldLogs(dir: string): void {
  try {
    const todayPrefix = `electron-main-${today()}`
    const files = readdirSync(dir)
      .filter((f) => f.endsWith('.log') && !f.startsWith(todayPrefix))
      .map((f) => ({
        name: f,
        path: join(dir, f),
        mtime: statSync(join(dir, f)).mtimeMs,
        size: statSync(join(dir, f)).size,
      }))
      .sort((a, b) => a.mtime - b.mtime)  // oldest first

    let totalSize = files.reduce((sum, f) => sum + f.size, 0)
    // Also count today's file
    try {
      totalSize += statSync(join(dir, `${todayPrefix}.log`)).size
    } catch { /* not created yet */ }

    for (const f of files) {
      if (totalSize <= MAX_LOG_DIR_BYTES) break
      try {
        unlinkSync(f.path)
        totalSize -= f.size
        console.log(`[logger] Pruned old log: ${f.name} (${(f.size / 1024).toFixed(0)} KB)`)
      } catch { /* best effort */ }
    }
  } catch { /* best effort */ }
}

function today(): string {
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

function ts(): string {
  const d = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${hh}:${mi}:${ss}.${ms}`
}

/**
 * Initialise the log directory and start mirroring console.log/error/warn to
 * a daily rotated file. Safe to call more than once.
 */
export function initLogger(): string {
  if (installed && logDir) return logDir

  const userData = app.getPath('userData')
  logDir = join(userData, 'logs')
  try {
    mkdirSync(logDir, { recursive: true })
  } catch {
    // best effort
  }

  // Prune old logs on startup
  pruneOldLogs(logDir)

  const file = join(logDir, `electron-main-${today()}.log`)
  stream = createWriteStream(file, { flags: 'a', encoding: 'utf-8' })

  // Wrap console methods — keep originals so terminal still shows them.
  const origLog = console.log.bind(console)
  const origErr = console.error.bind(console)
  const origWarn = console.warn.bind(console)

  const serialize = (args: unknown[]): string =>
    args
      .map((a) => {
        if (typeof a === 'string') return a
        if (a instanceof Error) return `${a.message}\n${a.stack ?? ''}`
        try {
          return JSON.stringify(a)
        } catch {
          return String(a)
        }
      })
      .join(' ')

  console.log = (...args: unknown[]) => {
    origLog(...args)
    stream?.write(`${ts()} [INFO ] ${serialize(args)}\n`)
  }
  console.error = (...args: unknown[]) => {
    origErr(...args)
    stream?.write(`${ts()} [ERROR] ${serialize(args)}\n`)
  }
  console.warn = (...args: unknown[]) => {
    origWarn(...args)
    stream?.write(`${ts()} [WARN ] ${serialize(args)}\n`)
  }

  // Catch anything that slips past the console wrappers.
  process.on('uncaughtException', (err) => {
    stream?.write(`${ts()} [FATAL] uncaughtException: ${err.message}\n${err.stack}\n`)
    stream?.end(() => process.exit(1))
    // Fallback if stream.end hangs
    setTimeout(() => process.exit(1), 1000).unref()
  })
  process.on('unhandledRejection', (reason) => {
    const msg = reason instanceof Error ? `${reason.message}\n${reason.stack}` : String(reason)
    stream?.write(`${ts()} [FATAL] unhandledRejection: ${msg}\n`)
  })

  // Flush and close log stream on app quit.
  app.on('before-quit', () => {
    if (stream) {
      stream.write(`${ts()} [INFO ] ─── Electron main shutting down ───\n`)
      stream.end()
      stream = null
    }
  })

  installed = true
  origLog(`[logger] Electron main log → ${file}`)
  stream.write(`${ts()} [INFO ] ─── Electron main started, logging to ${file} ───\n`)
  return logDir
}

export function getLogDir(): string | null {
  return logDir
}

/** Open a write stream under the log dir, creating it if needed. */
export function openLogFile(name: string): WriteStream | null {
  if (!logDir) return null
  const file = join(logDir, name)
  try {
    return createWriteStream(file, { flags: 'a', encoding: 'utf-8' })
  } catch {
    return null
  }
}
