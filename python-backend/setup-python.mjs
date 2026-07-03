/**
 * Cross-platform Python backend setup script.
 * Creates a virtual environment in the user data directory
 * and installs dependencies. Shared between dev mode and packaged app.
 */

import { existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { execSync } from 'node:child_process'
import { platform, homedir } from 'node:os'

const backendDir = import.meta.dirname
const isWin = platform() === 'win32'
const isMac = platform() === 'darwin'
const pythonCmd = isWin ? 'python' : 'python3'

// ── Venv lives in user data dir (shared between dev and packaged app) ──
function getVenvDir() {
  const appName = 'opengis'
  if (isWin) {
    return join(process.env.APPDATA || join(homedir(), 'AppData', 'Roaming'), appName, 'venv')
  } else if (isMac) {
    return join(homedir(), 'Library', 'Application Support', appName, 'venv')
  } else {
    return join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), appName, 'venv')
  }
}

const venvDir = getVenvDir()
const venvPython = isWin ? join(venvDir, 'Scripts', 'python.exe') : join(venvDir, 'bin', 'python')

function run(cmd) {
  console.log(`  > ${cmd}`)
  execSync(cmd, { stdio: 'inherit', cwd: backendDir })
}

// Step 1: Create venv if missing
if (!existsSync(venvPython)) {
  console.log(`[setup:python] Creating virtual environment at ${venvDir}...`)
  mkdirSync(venvDir, { recursive: true })
  run(`${pythonCmd} -m venv "${venvDir}"`)
} else {
  console.log(`[setup:python] Virtual environment already exists at ${venvDir}`)
}

// Step 2: Install dependencies
console.log('[setup:python] Installing dependencies...')
run(`"${venvPython}" -m pip install --upgrade pip`)
run(`"${venvPython}" -m pip install -e "${backendDir}"`)

console.log(`\n[setup:python] Done. Python: ${venvPython}`)
