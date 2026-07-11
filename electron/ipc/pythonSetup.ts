/**
 * Python environment setup for first-launch.
 *
 * Checks for an existing .venv, finds or downloads a Python 3.11+ interpreter,
 * creates a virtual environment, and installs dependencies.
 */

import { existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { execFileSync, execSync, spawn } from 'node:child_process'
import { homedir, platform, arch } from 'node:os'
import { createWriteStream } from 'node:fs'
import { pipeline } from 'node:stream/promises'
import { Readable } from 'node:stream'

const MIN_PYTHON_MAJOR = 3
const MIN_PYTHON_MINOR = 11

export interface SetupProgress {
  phase: 'checking' | 'downloading' | 'creating-venv' | 'installing' | 'done' | 'error'
  percent?: number
  detail?: string
  logLine?: string
}

export type ProgressCallback = (data: SetupProgress) => void

/**
 * Ensure the Python environment is ready. Returns the path to the venv Python.
 * If .venv already exists and is valid, returns immediately.
 *
 * @param backendPath - Path to python-backend source (read-only in packaged app)
 * @param venvPath - Writable path for the venv (e.g. ~/Library/Application Support/opengis/venv)
 */
export async function ensurePythonEnv(
  backendPath: string,
  venvPath: string,
  onProgress: ProgressCallback,
  appVersion?: string,
): Promise<string> {
  const isWin = platform() === 'win32'
  const venvPython = isWin
    ? join(venvPath, 'Scripts', 'python.exe')
    : join(venvPath, 'bin', 'python')
  const versionFile = join(venvPath, '.opengis-version.json')

  // ── 1. Check existing venv ──
  onProgress({ phase: 'checking', detail: 'Checking Python environment...' })
  if (existsSync(venvPython)) {
    try {
      const ver = execSync(`"${venvPython}" --version`, { encoding: 'utf-8', timeout: 5000 })
      if (ver.includes('Python 3')) {
        // Check if venv is from a different app version
        if (appVersion) {
          const needsUpgrade = checkVersionMismatch(versionFile, appVersion)
          if (needsUpgrade) {
            console.log(`[setup] App version changed, upgrading dependencies...`)
            onProgress({ phase: 'installing', detail: 'Updating dependencies for new version...' })
            await pipInstall(venvPython, backendPath, onProgress)
            writeVersionFile(versionFile, appVersion)
          }
        }
        console.log(`[setup] Existing venv OK: ${venvPython} (${ver.trim()})`)
        onProgress({ phase: 'done', detail: 'Python environment ready' })
        return venvPython
      }
    } catch {
      // venv exists but broken — fall through to recreate
    }
  }

  // ── 2. Find a suitable Python interpreter ──
  onProgress({ phase: 'checking', detail: 'Looking for Python 3.11+...' })
  let pythonPath = await findSystemPython()

  // ── 3. Download python-build-standalone if needed ──
  if (!pythonPath) {
    onProgress({ phase: 'downloading', percent: 0, detail: 'Downloading Python...' })
    pythonPath = await downloadPythonBuildStandalone(onProgress)
  }

  console.log(`[setup] Using Python: ${pythonPath}`)

  // ── 4. Create venv ──
  onProgress({ phase: 'creating-venv', detail: 'Creating virtual environment...' })
  await createVenv(pythonPath, venvPath)

  // ── 5. Check Xcode CLT (macOS only — needed for native deps) ──
  if (platform() === 'darwin') {
    checkXcodeCLT()
  }

  // ── 6. Install dependencies ──
  await pipInstall(venvPython, backendPath, onProgress)

  // ── 6. Write version stamp ──
  if (appVersion) {
    writeVersionFile(join(venvPath, '.opengis-version.json'), appVersion)
  }

  onProgress({ phase: 'done', detail: 'Python environment ready' })
  return venvPython
}

// ─── Find system Python 3.11+ ─────────────────────────────────

async function findSystemPython(): Promise<string | null> {
  const isWin = platform() === 'win32'
  const candidates: string[] = []

  if (isWin) {
    candidates.push('py', 'python', 'python3')
  } else {
    // Try versioned names first (more reliable)
    candidates.push('python3.13', 'python3.12', 'python3.11', 'python3')
    // Homebrew on Apple Silicon
    candidates.push('/opt/homebrew/bin/python3')
    // Homebrew on Intel Mac
    candidates.push('/usr/local/bin/python3')
    // Python.org framework builds
    for (const minor of [13, 12, 11]) {
      candidates.push(
        `/Library/Frameworks/Python.framework/Versions/3.${minor}/bin/python3`,
      )
    }
  }

  for (const cmd of candidates) {
    try {
      // Resolve to absolute path first, skip .venv paths
      let resolved = cmd
      try {
        const which = platform() === 'win32' ? 'where' : 'which'
        resolved = execFileSync(which, [cmd], { encoding: 'utf-8', timeout: 5000 }).trim().split('\n')[0].trim()
      } catch { /* use original */ }
      if (resolved.includes('.venv') || resolved.includes('site-packages')) continue

      const ver = execSync(`"${cmd}" --version`, { encoding: 'utf-8', timeout: 5000 })
      const match = ver.match(/Python (\d+)\.(\d+)/)
      if (match) {
        const major = parseInt(match[1], 10)
        const minor = parseInt(match[2], 10)
        if (major > MIN_PYTHON_MAJOR || (major === MIN_PYTHON_MAJOR && minor >= MIN_PYTHON_MINOR)) {
          if (existsSync(resolved)) return resolved
          if (existsSync(cmd)) return cmd
        }
      }
    } catch {
      // not found or not runnable — try next
    }
  }

  return null
}

// ─── Download python-build-standalone ──────────────────────────

async function downloadPythonBuildStandalone(
  onProgress: ProgressCallback,
): Promise<string> {
  const isMac = platform() === 'darwin'
  const isWin = platform() === 'win32'
  const cpuArch = arch()

  // Determine the correct archive name
  let archSuffix: string
  if (isMac) {
    archSuffix = cpuArch === 'arm64' ? 'aarch64' : 'x86_64'
  } else if (isWin) {
    archSuffix = cpuArch === 'arm64' ? 'aarch64' : 'x86_64'
  } else {
    archSuffix = cpuArch === 'arm64' ? 'aarch64' : 'x86_64'
  }

  // Use cpython-3.12 (stable, widely compatible)
  const version = '3.12.8'
  const osTag = isWin ? 'windows-mswin' : isMac ? 'apple-darwin' : 'unknown-linux-gnu'
  const fileName = `cpython-${version}+20241219-${archSuffix}-${osTag}-install_only_stripped.tar.gz`

  const tag = '20241219'
  // USTC mirror (China) as primary, GitHub as fallback
  const urls = [
    `https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/${tag}/${fileName}`,
    `https://github.com/astral-sh/python-build-standalone/releases/download/${tag}/${fileName}`,
  ]
  const dataDir = isWin
    ? join(process.env.APPDATA || join(homedir(), 'AppData', 'Roaming'), 'opengis', 'python')
    : isMac
      ? join(homedir(), 'Library', 'Application Support', 'opengis', 'python')
      : join(homedir(), '.local', 'share', 'opengis', 'python')

  mkdirSync(dataDir, { recursive: true })

  // Check if already downloaded
  const pythonDir = join(dataDir, 'python')
  const pythonBin = isWin
    ? join(pythonDir, 'python.exe')
    : join(pythonDir, 'bin', 'python3')

  if (existsSync(pythonBin)) {
    try {
      execSync(`"${pythonBin}" --version`, { encoding: 'utf-8', timeout: 5000 })
      console.log(`[setup] Using cached python-build-standalone: ${pythonBin}`)
      return pythonBin
    } catch {
      // Corrupted or incomplete — delete and re-download
      console.warn(`[setup] Cached Python is broken, deleting ${pythonDir}`)
      const fs = await import('node:fs')
      fs.rmSync(pythonDir, { recursive: true, force: true })
    }
  }

  // Download — try each URL in order, skip to next on failure
  const archivePath = join(dataDir, fileName)
  let downloaded = false
  for (const url of urls) {
    console.log(`[setup] Trying download: ${url}`)
    try {
      await downloadFile(url, archivePath, (received, total) => {
        const percent = total > 0 ? Math.round((received / total) * 100) : 0
        onProgress({
          phase: 'downloading',
          percent,
          detail: `Downloading Python... ${formatBytes(received)}${total > 0 ? ` / ${formatBytes(total)}` : ''}`,
        })
      })
      downloaded = true
      break
    } catch (err) {
      console.warn(`[setup] Download failed from ${url}: ${err}`)
      // Clean up partial file before trying next URL
      try { const fs = await import('node:fs'); fs.unlinkSync(archivePath) } catch { /* ok */ }
    }
  }
  if (!downloaded) {
    throw new Error('Failed to download Python from all sources. Please check your network connection.')
  }

  // Extract
  onProgress({ phase: 'downloading', percent: 100, detail: 'Extracting Python...' })
  await extractTarGz(archivePath, dataDir)

  // Make executable (macOS/Linux)
  if (!isWin) {
    try { execSync(`chmod +x "${pythonBin}"`, { timeout: 5000 }) } catch { /* ok */ }
  }

  console.log(`[setup] Python installed to: ${pythonBin}`)
  return pythonBin
}

// ─── Create venv ───────────────────────────────────────────────

async function createVenv(pythonPath: string, venvPath: string): Promise<void> {
  console.log(`[setup] Creating venv at ${venvPath}`)
  mkdirSync(venvPath, { recursive: true })
  execSync(`"${pythonPath}" -m venv "${venvPath}"`, {
    stdio: 'pipe',
    timeout: 60_000,
  })
}

// ─── pip mirror sources ────────────────────────────────────────

const PIP_MIRRORS = [
  { name: 'USTC', index: 'https://pypi.mirrors.ustc.edu.cn/simple/', timeout: 5000 },
  { name: 'Aliyun', index: 'https://mirrors.aliyun.com/pypi/simple/', timeout: 5000 },
  { name: 'PyPI', index: 'https://pypi.org/simple/', timeout: 8000 },
]

async function probeMirror(url: string, timeoutMs: number): Promise<boolean> {
  try {
    const https = await import('node:https')
    return await new Promise((resolve) => {
      const req = https.get(url, { timeout: timeoutMs, method: 'HEAD' }, (res) => {
        resolve(res.statusCode !== undefined && res.statusCode < 500)
        req.destroy()
      })
      req.on('error', () => resolve(false))
      req.on('timeout', () => { req.destroy(); resolve(false) })
    })
  } catch { return false }
}

async function selectPipMirror(): Promise<string | null> {
  for (const mirror of PIP_MIRRORS) {
    console.log(`[setup] Probing pip mirror: ${mirror.name}`)
    if (await probeMirror(mirror.index, mirror.timeout)) {
      console.log(`[setup] Using pip mirror: ${mirror.name}`)
      return mirror.index
    }
  }
  return null  // fallback to default
}

// ─── pip install with progress ─────────────────────────────────

const PIP_STALL_TIMEOUT_MS = 90_000  // 90s no output = stalled

async function pipInstall(
  venvPython: string,
  backendPath: string,
  onProgress: ProgressCallback,
): Promise<void> {
  // Select mirror
  onProgress({ phase: 'installing', percent: 0, detail: '检测网络环境...' })
  const mirrorIndex = await selectPipMirror()
  const mirrorArgs = mirrorIndex ? ['-i', mirrorIndex, '--trusted-host', new URL(mirrorIndex).hostname] : []
  const mirrorLabel = mirrorIndex ? new URL(mirrorIndex).hostname : 'default PyPI'

  // Upgrade pip first
  onProgress({ phase: 'installing', percent: 0, detail: `升级 pip (${mirrorLabel})...` })
  try {
    execSync(`"${venvPython}" -m pip install --upgrade pip ${mirrorArgs.join(' ')}`, {
      cwd: backendPath,
      stdio: 'pipe',
      timeout: 120_000,
    })
  } catch {
    // pip upgrade failure is non-fatal, continue with install
  }

  // Install project dependencies
  onProgress({ phase: 'installing', percent: 3, detail: `安装依赖 (${mirrorLabel})...` })

  return new Promise<void>((resolve, reject) => {
    const args = ['-m', 'pip', 'install', '-e', backendPath, ...mirrorArgs]
    const proc = spawn(venvPython, args, {
      cwd: backendPath,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    })

    let stderr = ''
    let stdoutLines: string[] = []
    let lastActivity = Date.now()
    let stallTimer: ReturnType<typeof setInterval> | null = null
    let totalPkgs = 0
    let installedPkgs = 0

    // Stall detection
    stallTimer = setInterval(() => {
      if (Date.now() - lastActivity > PIP_STALL_TIMEOUT_MS) {
        proc.kill('SIGTERM')
        if (stallTimer) clearInterval(stallTimer)
        reject(new Error(`pip install 已无响应超过 ${PIP_STALL_TIMEOUT_MS / 1000} 秒，可能是网络问题。\n\n请检查网络连接后重试。`))
      }
    }, 15_000)

    proc.stdout?.on('data', (data: Buffer) => {
      lastActivity = Date.now()
      const lines = data.toString().split('\n').filter(Boolean)
      for (const line of lines) {
        stdoutLines.push(line)
        if (stdoutLines.length > 500) stdoutLines.shift()

        // Collecting phase — count packages
        const collectingMatch = line.match(/^Collecting\s+(\S+)/)
        if (collectingMatch) {
          totalPkgs++
          const percent = Math.min(3 + Math.floor(totalPkgs * 0.6), 60)
          onProgress({
            phase: 'installing',
            percent,
            detail: `下载: ${collectingMatch[1]}`,
            logLine: line,
          })
          continue
        }

        // Installing phase — count installed
        const installingMatch = line.match(/^Installing collected packages:\s*(.+)/)
        if (installingMatch) {
          onProgress({
            phase: 'installing',
            percent: 65,
            detail: `安装: ${installingMatch[1]}`,
            logLine: line,
          })
          continue
        }

        // Per-package install progress
        const installingPkg = line.match(/^Installing\s+(\S+)/)
        if (installingPkg) {
          installedPkgs++
          const percent = totalPkgs > 0
            ? Math.min(65 + Math.floor((installedPkgs / totalPkgs) * 30), 95)
            : 80
          onProgress({
            phase: 'installing',
            percent,
            detail: `安装: ${installingPkg[1]}`,
            logLine: line,
          })
          continue
        }

        // Success
        if (line.startsWith('Successfully installed')) {
          onProgress({ phase: 'installing', percent: 98, detail: '完成', logLine: line })
          continue
        }

        // Building wheel / running setup.py
        if (line.includes('Building wheel') || line.includes('Running setup.py')) {
          const pkg = line.match(/for\s+(\S+)/)?.[1] || ''
          onProgress({
            phase: 'installing',
            percent: Math.min(3 + Math.floor(totalPkgs * 0.6), 60),
            detail: `编译: ${pkg}`,
            logLine: line,
          })
        }
      }
    })

    proc.stderr?.on('data', (data: Buffer) => {
      stderr += data.toString()
      lastActivity = Date.now()
    })

    proc.on('close', (code) => {
      if (stallTimer) clearInterval(stallTimer)
      if (code === 0) {
        onProgress({ phase: 'installing', percent: 100, detail: '依赖安装完成' })
        resolve()
      } else {
        const rawErr = stderr.trim()
        const hint = classifyPipError(rawErr, code)
        reject(new Error(hint))
      }
    })

    proc.on('error', (err) => {
      if (stallTimer) clearInterval(stallTimer)
      reject(new Error(`无法启动 pip: ${err.message}\n\n请确保 Python 环境完整。`))
    })
  })
}

// ─── Helpers ───────────────────────────────────────────────────

async function downloadFile(
  url: string,
  destPath: string,
  onProgress: (downloaded: number, total: number) => void,
): Promise<void> {
  const https = await import('node:https')
  const http = await import('node:http')

  return new Promise((resolve, reject) => {
    const DOWNLOAD_STALL_MS = 30_000  // 30s no data = stalled
    const makeRequest = (requestUrl: string, redirectCount = 0) => {
      if (redirectCount > 5) {
        reject(new Error('Too many redirects'))
        return
      }

      const mod = requestUrl.startsWith('https') ? https : http
      let stallCheck: ReturnType<typeof setInterval> | null = null

      const cleanup = () => {
        if (stallCheck) { clearInterval(stallCheck); stallCheck = null }
      }

      const req = mod.get(requestUrl, {
        headers: { 'User-Agent': 'OpenGIS-Setup/1.0' },
        timeout: 15_000,  // 15s connection timeout
      }, (res) => {
        // Handle redirects
        if (res.statusCode && [301, 302, 307, 308].includes(res.statusCode) && res.headers.location) {
          makeRequest(res.headers.location, redirectCount + 1)
          return
        }

        if (res.statusCode !== 200) {
          reject(new Error(`Download failed: HTTP ${res.statusCode}`))
          return
        }

        const total = parseInt(res.headers['content-length'] || '0', 10)
        let downloaded = 0
        let lastData = Date.now()
        stallCheck = setInterval(() => {
          if (Date.now() - lastData > DOWNLOAD_STALL_MS) {
            cleanup()
            req.destroy()
            reject(new Error(`Download stalled — no data for ${DOWNLOAD_STALL_MS / 1000}s`))
          }
        }, 5000)

        const file = createWriteStream(destPath)
        res.on('data', (chunk: Buffer) => {
          downloaded += chunk.length
          lastData = Date.now()
          onProgress(downloaded, total)
        })
        res.pipe(file)
        file.on('finish', () => { cleanup(); file.close(); resolve() })
        file.on('error', (err) => { cleanup(); reject(err) })
      }).on('error', (err) => { cleanup(); reject(err) })
        .on('timeout', () => { cleanup(); req.destroy(); reject(new Error('Connection timed out')) })
    }

    makeRequest(url)
  })
}

async function extractTarGz(archivePath: string, destDir: string): Promise<void> {
  try {
    execFileSync('tar', ['-xzf', archivePath, '-C', destDir], {
      stdio: 'pipe',
      timeout: 120_000,
    })
  } catch (err) {
    if (platform() === 'win32') {
      throw new Error(
        '无法解压内置 Python。Windows 需要可用的 tar.exe（Windows 10/11 通常自带）。\n\n' +
        '请确认系统 PATH 中存在 C:\\Windows\\System32\\tar.exe，或安装较新的 Windows 运行环境后重试。\n\n' +
        `详细错误: ${(err as Error).message}`,
      )
    }
    throw err
  }
  // Clean up archive
  try {
    const fs = await import('node:fs')
    fs.unlinkSync(archivePath)
  } catch { /* best effort */ }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function checkXcodeCLT(): void {
  try {
    execSync('xcode-select -p', { stdio: 'pipe', timeout: 5000 })
    console.log('[setup] Xcode CLT detected')
  } catch {
    console.warn('[setup] Xcode CLT not found — native dependencies may fail to compile')
    // Don't throw — let pip try anyway (many packages have prebuilt wheels)
    // The error message in classifyPipError will catch build failures
  }
}

function classifyPipError(stderr: string, code: number | null): string {
  const lastLines = stderr.split('\n').slice(-8).join('\n')
  const isWin = platform() === 'win32'

  if (stderr.includes('No space left on device') || stderr.includes('ENOSPC')) {
    return `磁盘空间不足，无法安装依赖。\n\n请清理磁盘空间后重试（需要至少 2GB 可用空间）。`
  }
  if (stderr.includes('Connection refused') || stderr.includes('Network is unreachable') || stderr.includes('ETIMEDOUT') || stderr.includes('Connection reset')) {
    return `网络连接失败，无法下载依赖。\n\n请检查网络连接后重试。`
  }
  if (stderr.includes('Could not find a version') || stderr.includes('No matching distribution')) {
    return `依赖版本不兼容。\n\n可能是 Python 版本、系统架构或平台 wheel 不匹配。请确保 Python >= 3.11，Windows 推荐 64 位 Python。`
  }
  if (
    stderr.includes('Microsoft Visual C++') ||
    stderr.includes('MSVC') ||
    stderr.includes('vcvarsall') ||
    stderr.includes('cl.exe') ||
    stderr.includes('error: command') ||
    stderr.includes('fatal error') ||
    stderr.includes('build failed') ||
    stderr.includes('clang') ||
    stderr.includes('gcc')
  ) {
    if (isWin) {
      return `依赖编译失败。Windows 上 GIS 依赖应优先使用预编译 wheel；如果 pip 退回源码编译，需要 Microsoft C++ Build Tools。\n\n建议：\n• 确认正在使用 64 位 Python\n• 检查网络/镜像能否下载 rasterio、fiona、pyproj、shapely 的 wheel\n• 必要时安装 Microsoft C++ Build Tools 后重试\n\n详细错误:\n${lastLines}`
    }
    return `依赖编译失败。原生库需要 C 编译器。\n\nmacOS 请在终端运行:\n  xcode-select --install\n\n然后重新打开应用重试。`
  }
  if (stderr.includes('Permission denied') || stderr.includes('EACCES')) {
    return `权限不足，无法写入安装目录。\n\n请检查应用数据目录的写入权限。`
  }

  const platformHint = isWin
    ? '• Windows GIS 依赖 wheel 下载失败或架构不匹配\n• 必要时安装 Microsoft C++ Build Tools\n'
    : '• 系统缺少编译工具 — macOS 可运行 xcode-select --install\n'
  return `依赖安装失败 (exit code ${code})。\n\n常见原因：\n• 网络不稳定 — 点击"重试"\n• 磁盘空间不足 — 需要至少 2GB\n${platformHint}\n详细错误:\n${lastLines}`
}

function checkVersionMismatch(versionFile: string, currentVersion: string): boolean {
  try {
    const fs = require('node:fs')
    if (!fs.existsSync(versionFile)) return true
    const data = JSON.parse(fs.readFileSync(versionFile, 'utf-8'))
    return data.appVersion !== currentVersion
  } catch {
    return true
  }
}

function writeVersionFile(versionFile: string, appVersion: string): void {
  try {
    const fs = require('node:fs')
    fs.writeFileSync(versionFile, JSON.stringify({ appVersion, updatedAt: new Date().toISOString() }, null, 2), 'utf-8')
  } catch {
    // best effort
  }
}
