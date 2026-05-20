/**
 * Cross-platform Python backend setup script.
 * Creates a virtual environment and installs dependencies.
 * Works on Windows (cmd/PowerShell), macOS, and Linux.
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { execSync } from 'node:child_process'
import { platform } from 'node:os'

const backendDir = import.meta.dirname
const venvDir = join(backendDir, '.venv')
const isWin = platform() === 'win32'
const pythonCmd = isWin ? 'python' : 'python3'
const venvPython = isWin ? join(venvDir, 'Scripts', 'python.exe') : join(venvDir, 'bin', 'python')

function run(cmd) {
  console.log(`  > ${cmd}`)
  execSync(cmd, { stdio: 'inherit', cwd: backendDir })
}

// Step 1: Create venv if missing
if (!existsSync(venvPython)) {
  console.log('[setup:python] Creating virtual environment...')
  run(`${pythonCmd} -m venv "${venvDir}"`)
} else {
  console.log('[setup:python] Virtual environment already exists.')
}

// Step 2: Install dependencies
console.log('[setup:python] Installing dependencies...')
run(`"${venvPython}" -m pip install --upgrade pip`)
run(`"${venvPython}" -m pip install -e "${backendDir}"`)

console.log(`\n[setup:python] Done. Python: ${venvPython}`)
