/**
 * Map View Persistence Service
 *
 * Persists the per-workspace map camera to `<workspace>/.opengis/map-view.json`.
 * Layers already have their own persistence file; keeping camera state separate
 * lets us update it frequently without rewriting potentially large layer data.
 */

export interface PersistedMapView {
  center: [number, number]
  zoom: number
  bearing: number
  pitch: number
}

const MAP_VIEW_FILE = '.opengis/map-view.json'
const DEBOUNCE_MS = 300

let _pendingTimer: ReturnType<typeof setTimeout> | null = null

function getMapViewFilePath(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/${MAP_VIEW_FILE}`
}

function getOpengisDir(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/.opengis`
}

function parseMapView(raw: unknown): PersistedMapView | null {
  const data = raw as { viewState?: unknown }
  const view = (data?.viewState ?? raw) as Partial<PersistedMapView> | null
  if (!view || !Array.isArray(view.center) || view.center.length !== 2) return null
  const [lng, lat] = view.center
  if (
    !Number.isFinite(lng) ||
    !Number.isFinite(lat) ||
    !Number.isFinite(view.zoom) ||
    !Number.isFinite(view.bearing) ||
    !Number.isFinite(view.pitch)
  ) {
    return null
  }
  return {
    center: [Number(lng), Number(lat)],
    zoom: Number(view.zoom),
    bearing: Number(view.bearing),
    pitch: Number(view.pitch),
  }
}

export async function loadMapView(
  workspacePath: string | null,
): Promise<PersistedMapView | null> {
  if (!workspacePath || !window.electronAPI) return null

  try {
    const result = await window.electronAPI.readFile(getMapViewFilePath(workspacePath))
    if (!result?.success || !result.content) return null
    return parseMapView(JSON.parse(result.content))
  } catch {
    return null
  }
}

export function persistMapView(
  workspacePath: string | null,
  viewState: PersistedMapView,
): void {
  if (!workspacePath || !window.electronAPI) return

  if (_pendingTimer) clearTimeout(_pendingTimer)
  _pendingTimer = setTimeout(() => {
    _pendingTimer = null
    void _writeMapView(workspacePath, viewState)
  }, DEBOUNCE_MS)
}

export async function flushMapView(
  workspacePath: string | null,
  viewState: PersistedMapView,
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return
  if (_pendingTimer) {
    clearTimeout(_pendingTimer)
    _pendingTimer = null
  }
  await _writeMapView(workspacePath, viewState)
}

async function _writeMapView(
  workspacePath: string,
  viewState: PersistedMapView,
): Promise<void> {
  if (!window.electronAPI) return
  try {
    await window.electronAPI.ensureDirectory(getOpengisDir(workspacePath))
    const payload = JSON.stringify({ version: 1, viewState }, null, 2)
    await window.electronAPI.writeFile(getMapViewFilePath(workspacePath), payload)
  } catch (e) {
    console.error('[mapViewPersistence] 写入地图视口失败:', e)
  }
}
