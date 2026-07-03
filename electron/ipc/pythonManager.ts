import { ChildProcess, spawn, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { app, BrowserWindow, dialog, shell } from 'electron'
import net from 'net'
import { WriteStream } from 'fs'
import { openLogFile, getLogDir } from '../logger'

export interface PythonStatus {
  status: 'stopped' | 'starting' | 'ready' | 'error'
  port?: number
  error?: string
  pythonPath?: string
  wsToken?: string  // WebSocket authentication token
}

function todayStamp(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function ts(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}.${String(d.getMilliseconds()).padStart(3, '0')}`
}

/**
 * Manages the Python backend sidecar process lifecycle.
 *
 * Responsibilities:
 * - Detect Python environment (conda/venv/system)
 * - Spawn Python backend as child process
 * - Monitor health and auto-restart
 * - Graceful shutdown
 */
export class PythonManager {
  private process: ChildProcess | null = null
  private status: PythonStatus = { status: 'stopped' }
  private port: number = 0
  private pythonPath: string = 'python'
  private isWindows: boolean = process.platform === 'win32'
  private stdoutMirror: WriteStream | null = null
  private wsToken: string = ''  // WebSocket authentication token
  private killTimeout: ReturnType<typeof setTimeout> | null = null
  // Auto-restart tracking
  private restartCount: number = 0
  private maxRestarts: number = 3
  private restartDelayMs: number = 2000
  private isRestarting: boolean = false
  // Renderer target for status / token broadcasts. Injected from main.ts
  // via setMainWindow() — the previous implementation referenced a
  // free-floating `mainWindow` symbol that never existed in this module,
  // crashing with `ReferenceError` the moment Python printed anything
  // matching one of the regexes below.
  private mainWindow: BrowserWindow | null = null

  /**
   * Wire up the renderer window so status / ws-token events can be
   * forwarded. Safe to call multiple times; pass `null` to detach.
   */
  setMainWindow(win: BrowserWindow | null): void {
    this.mainWindow = win
  }

  /**
   * Send an IPC event to the renderer if the window is still alive.
   * Centralises the `?.` + isDestroyed dance so individual call-sites
   * stay readable.
   */
  private sendToRenderer(channel: string, payload: unknown): void {
    const win = this.mainWindow
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, payload)
    }
  }

  getStatus(): PythonStatus {
    return { ...this.status }
  }

  getPort(): number | null {
    return this.status.status === 'ready' ? this.port : null
  }

  getWsToken(): string | null {
    return this.status.status === 'ready' ? this.wsToken : null
  }

  /**
   * Get the writable path for the Python virtual environment.
   * Stored in user data directory (not inside the app bundle).
   */
  getVenvPath(): string {
    return join(app.getPath('userData'), 'venv')
  }

  /**
   * Check if the venv exists in the user data directory.
   * Used by main.ts to decide whether to run the setup flow.
   */
  hasVenv(): boolean {
    const venvPython = this.isWindows
      ? join(this.getVenvPath(), 'Scripts', 'python.exe')
      : join(this.getVenvPath(), 'bin', 'python')
    return existsSync(venvPython)
  }

  /**
   * Start the Python backend server.
   */
  async start(): Promise<void> {
    if (this.status.status === 'ready' || this.status.status === 'starting') {
      return
    }

    // Clear any pending SIGKILL from a previous stop() to avoid killing the new process
    if (this.killTimeout) {
      clearTimeout(this.killTimeout)
      this.killTimeout = null
    }

    this.status = { status: 'starting' }

    try {
      // 1. Detect Python — use venv from user data directory
      this.pythonPath = await this.detectPython()
      this.status.pythonPath = this.pythonPath

      // 2. Find available port
      this.port = await this.findAvailablePort()

      // 3. Determine backend path
      const backendPath = this.getBackendPath()

      // 4. Prepare stdout mirror log file (best-effort)
      this.stdoutMirror = openLogFile(`python-stdout-${todayStamp()}.log`)
      this.stdoutMirror?.write(
        `${ts()} [INFO ] ─── Python sidecar starting · port=${this.port} · python=${this.pythonPath} ───\n`,
      )

      // 5. Spawn Python process — pass the shared log dir so Python's own
      //    RotatingFileHandler writes into the same <userData>/logs/ folder.
      const logDir = getLogDir() ?? ''
      const spawnEnv = {
        ...process.env,
        OPENGIS_PORT: String(this.port),
        OPENGIS_LOG_DIR: logDir,
        PYTHONUNBUFFERED: '1',
        // matplotlib 在 macOS 上默认会走 'macosx' 后端，子进程里要么因缺
        // PyObjC 报 "Cannot find a backend"，要么真的弹出 GUI 窗口。
        // sidecar 不需要交互式绘图，强制用无头 Agg 后端，行为可预测。
        // Windows / Linux 下 Agg 同样是安全选择。
        MPLBACKEND: process.env.MPLBACKEND || 'Agg',
      }
      const cliArgs = ['-m', 'opengis_backend', '--port', String(this.port)]
      if (logDir) cliArgs.push('--log-dir', logDir)
      // Log level is now controlled at runtime via rpc.debug.set_log_level
      // (Settings > Agent > Debug Mode toggle). Default is INFO.

      this.process = spawn(this.pythonPath, cliArgs, {
        cwd: backendPath,
        env: spawnEnv,
        stdio: ['pipe', 'pipe', 'pipe'],
      })

      // Handle stdout
      this.process.stdout?.on('data', (data: Buffer) => {
        const output = data.toString()
        // Mirror the raw bytes (keep newlines) to the log file.
        this.stdoutMirror?.write(`${ts()} [OUT  ] ${output}${output.endsWith('\n') ? '' : '\n'}`)
        // Console still shows the trimmed version for readability.
        const trimmed = output.trim()
        if (trimmed) console.log(`[Python] ${trimmed}`)

        // Capture WebSocket authentication token
        const tokenMatch = output.match(/OPENGIS_WS_TOKEN=(\S+)/)
        if (tokenMatch) {
          this.wsToken = tokenMatch[1]
          console.log(`[Python] WebSocket auth token captured`)
          // 如果 status 已经是 ready，立即发送 token 给渲染进程
          if (this.status.status === 'ready') {
            this.sendToRenderer('python:ws-token', this.wsToken)
          }
        }

        // Detect ready signal
        if (output.includes('OPENGIS_READY') || output.includes('Uvicorn running')) {
          this.status = { 
            status: 'ready', 
            port: this.port, 
            pythonPath: this.pythonPath,
            wsToken: this.wsToken || undefined 
          }
          // Notify renderer about token
          this.sendToRenderer('python:ws-token', this.wsToken)
        }
      })

      // Handle stderr (uvicorn logs come here by default)
      this.process.stderr?.on('data', (data: Buffer) => {
        const output = data.toString()
        this.stdoutMirror?.write(`${ts()} [ERR  ] ${output}${output.endsWith('\n') ? '' : '\n'}`)
        const trimmed = output.trim()
        if (trimmed) console.error(`[Python:err] ${trimmed}`)

        // Capture WebSocket authentication token (may be printed to stderr)
        const tokenMatch = output.match(/OPENGIS_WS_TOKEN=(\S+)/)
        if (tokenMatch) {
          this.wsToken = tokenMatch[1]
          console.log(`[Python] WebSocket auth token captured (from stderr)`)
        }

        if (output.includes('Uvicorn running') || output.includes('Application startup complete')) {
          this.status = { 
            status: 'ready', 
            port: this.port, 
            pythonPath: this.pythonPath,
            wsToken: this.wsToken || undefined 
          }
          // Notify renderer
          this.sendToRenderer('python:status-changed', this.getStatus())
          // Also send token explicitly
          if (this.wsToken) {
            this.sendToRenderer('python:ws-token', this.wsToken)
          }
        }
      })

      // Handle process exit — auto-restart on unexpected exits
      this.process.on('exit', (code) => {
        const msg = `Python process exited with code ${code}`
        console.log(`[Python] ${msg}`)
        this.stdoutMirror?.write(`${ts()} [INFO ] ${msg}\n`)
        this.stdoutMirror?.end()
        this.stdoutMirror = null
        this.process = null

        // Intentional stop — don't restart
        if (this.status.status === 'stopped') return

        // User cancelled — don't restart
        if (code === null || code === 0) {
          this.status = { status: 'stopped' }
          return
        }

        // Unexpected exit — attempt auto-restart
        if (this.restartCount < this.maxRestarts && !this.isRestarting) {
          this.restartCount++
          this.isRestarting = true
          console.log(`[Python] Auto-restarting (${this.restartCount}/${this.maxRestarts}) in ${this.restartDelayMs}ms...`)
          this.status = { status: 'starting' }
          this.sendToRenderer('python:status-changed', this.getStatus())

          setTimeout(async () => {
            try {
              await this.start()
              this.restartCount = 0  // Reset on successful restart
              this.isRestarting = false
              console.log(`[Python] Auto-restart succeeded`)
            } catch (err) {
              this.isRestarting = false
              console.error(`[Python] Auto-restart failed:`, err)
              // If we've exhausted retries, show error dialog
              if (this.restartCount >= this.maxRestarts) {
                this.showFatalError(msg)
              }
            }
          }, this.restartDelayMs)
        } else {
          // Exhausted retries
          this.status = { status: 'error', error: msg }
          this.sendToRenderer('python:status-changed', this.getStatus())
          if (this.restartCount >= this.maxRestarts) {
            this.showFatalError(msg)
          }
        }
      })

      this.process.on('error', (err) => {
        console.error(`[Python] Process error:`, err)
        this.stdoutMirror?.write(`${ts()} [ERROR] spawn error: ${err.message}\n`)
        this.status = {
          status: 'error',
          error: `Failed to spawn Python: ${err.message}`,
        }
        this.process = null
      })

      // Wait for ready signal (timeout 30s)
      await this.waitForReady(30000)
    } catch (error) {
      this.status = {
        status: 'error',
        error: String(error),
      }
      throw error
    }
  }

  /**
   * Stop the Python backend server.
   */
  stop(): void {
    this.restartCount = 0  // Reset on intentional stop
    this.isRestarting = false
    // Clear any pending force-kill timeout from a previous stop/restart.
    if (this.killTimeout) {
      clearTimeout(this.killTimeout)
      this.killTimeout = null
    }
    if (this.process) {
      this.status = { status: 'stopped' }
      this.process.kill('SIGTERM')

      // Force kill after 5 seconds
      this.killTimeout = setTimeout(() => {
        if (this.process) {
          this.process.kill('SIGKILL')
          this.process = null
        }
        this.killTimeout = null
      }, 5000)
    }
    if (this.stdoutMirror) {
      this.stdoutMirror.end()
      this.stdoutMirror = null
    }
  }

  /**
   * Show a fatal error dialog when Python crashes repeatedly.
   */
  private showFatalError(detail: string): void {
    const win = this.mainWindow
    const options: Electron.MessageBoxOptions = {
      type: 'error',
      title: 'Python Backend Crash',
      message: 'Python backend has crashed repeatedly and cannot recover.',
      detail: `${detail}\n\nPlease check the logs for more information.`,
      buttons: ['View Logs', 'Restart App', 'OK'],
      defaultId: 0,
    }

    dialog.showMessageBox(win, options).then(({ response }) => {
      if (response === 0) {
        // Open log directory
        const logDir = app.getPath('userData') + '/logs'
        shell.openPath(logDir)
      } else if (response === 1) {
        // Restart app
        app.relaunch()
        app.quit()
      }
    })
  }

  /**
   * Restart the Python backend server.
   */
  async restart(): Promise<void> {
    this.stop()
    // Wait a bit for cleanup
    await new Promise((resolve) => setTimeout(resolve, 1000))
    await this.start()
  }

  /**
   * Detect Python interpreter from the venv in user data directory.
   */
  private async detectPython(): Promise<string> {
    const venvPython = this.isWindows
      ? join(this.getVenvPath(), 'Scripts', 'python.exe')
      : join(this.getVenvPath(), 'bin', 'python')

    if (existsSync(venvPython)) {
      try {
        const result = await this.execCommand(venvPython, ['--version'])
        if (result.includes('Python 3')) {
          console.log(`[Python] Using project venv: ${venvPython} (${result.trim()})`)
          return venvPython
        }
      } catch {
        // venv python exists but can't run — fall through to error
      }
    }

    throw new Error(
      `Python backend not set up. Run \`npm run setup:python\` first. (looked for ${venvPython})`
    )
  }

  /**
   * Resolve a python command name to its absolute path.
   * This avoids the need for shell: true when spawning.
   */
  private async resolvePythonPath(command: string): Promise<string> {
    // If already an absolute path, return as-is
    if (join(command) === command && (command.includes('/') || command.includes('\\'))) {
      return command
    }

    try {
      if (this.isWindows) {
        // Use 'where' to resolve the command to an absolute path
        const result = await this.execCommand('where', [command])
        const firstLine = result.trim().split('\n')[0].trim()
        if (firstLine && firstLine.length > 0) {
          return firstLine
        }
      } else {
        // Use 'which' on Unix
        const result = await this.execCommand('which', [command])
        const firstLine = result.trim().split('\n')[0].trim()
        if (firstLine && firstLine.length > 0) {
          return firstLine
        }
      }
    } catch {
      // Fall through to return original command
    }

    return command
  }

  /**
   * Find an available TCP port.
   */
  private findAvailablePort(): Promise<number> {
    return new Promise((resolve, reject) => {
      const server = net.createServer()
      server.listen(0, () => {
        const address = server.address()
        if (address && typeof address === 'object') {
          const port = address.port
          server.close(() => resolve(port))
        } else {
          reject(new Error('Failed to find available port'))
        }
      })
      server.on('error', reject)
    })
  }

  /**
   * Get the path to the Python backend directory.
   */
  getBackendPath(): string {
    if (app.isPackaged) {
      return join(process.resourcesPath, 'python-backend')
    }
    // __dirname in dev = <project>/out/electron/ → ../.. = <project>/
    return join(__dirname, '../..', 'python-backend')
  }

  /**
   * Wait for the Python backend to report ready.
   */
  private waitForReady(timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const startTime = Date.now()

      const check = () => {
        if (this.status.status === 'ready') {
          resolve()
          return
        }
        if (this.status.status === 'error') {
          reject(new Error(this.status.error || 'Python backend failed to start'))
          return
        }
        if (Date.now() - startTime > timeoutMs) {
          console.warn('[Python] Startup timeout')
          this.status = { status: 'error', error: 'Startup timeout' }
          reject(new Error('Python backend startup timeout'))
          return
        }
        setTimeout(check, 500)
      }

      check()
    })
  }

  /**
   * Execute a command and return stdout.
   */
  private execCommand(command: string, args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      // Use execFile for detection — it handles PATH resolution natively
      // without needing shell: true (which can fail if cmd.exe path is broken)
      const proc = execFile(command, args, { timeout: 5000 }, (error, stdout, stderr) => {
        if (error) {
          reject(error)
          return
        }
        resolve(stdout || stderr)
      })
    })
  }
}
