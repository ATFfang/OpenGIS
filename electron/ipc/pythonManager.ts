import { ChildProcess, spawn, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { app, BrowserWindow } from 'electron'
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
   * Start the Python backend server.
   */
  async start(): Promise<void> {
    if (this.status.status === 'ready' || this.status.status === 'starting') {
      return
    }

    this.status = { status: 'starting' }

    try {
      // 1. Detect Python — use project-local .venv
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

      // Handle process exit
      this.process.on('exit', (code) => {
        const msg = `Python process exited with code ${code}`
        console.log(`[Python] ${msg}`)
        this.stdoutMirror?.write(`${ts()} [INFO ] ${msg}\n`)
        this.stdoutMirror?.end()
        this.stdoutMirror = null
        if (this.status.status !== 'stopped') {
          this.status = { status: 'error', error: msg }
        }
        this.process = null
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
   * Restart the Python backend server.
   */
  async restart(): Promise<void> {
    this.stop()
    // Wait a bit for cleanup
    await new Promise((resolve) => setTimeout(resolve, 1000))
    await this.start()
  }

  /**
   * Detect project-local Python interpreter from .venv.
   * The app always uses python-backend/.venv — no system scanning.
   */
  private async detectPython(): Promise<string> {
    const backendPath = this.getBackendPath()
    const venvPython = this.isWindows
      ? join(backendPath, '.venv', 'Scripts', 'python.exe')
      : join(backendPath, '.venv', 'bin', 'python')

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
  private getBackendPath(): string {
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
