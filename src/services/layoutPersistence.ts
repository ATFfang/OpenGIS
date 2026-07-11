import type { LayoutElement, LayoutPage } from '@/features/layout-composer/types'

export interface PersistedLayoutComposer {
  page: LayoutPage
  elements: LayoutElement[]
  selectedElementId: string | null
  zoom: number
  mapScaleDenominator: number
  mapSnapshotUrl: string | null
}

const LAYOUT_FILE = '.opengis/layout-composer.json'
const DEBOUNCE_MS = 500

let _pendingTimer: ReturnType<typeof setTimeout> | null = null

function getLayoutFilePath(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/${LAYOUT_FILE}`
}

function getOpengisDir(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/.opengis`
}

function parseLayout(raw: unknown): PersistedLayoutComposer | null {
  const data = raw as { layout?: unknown }
  const layout = (data?.layout ?? raw) as Partial<PersistedLayoutComposer> | null
  if (!layout || !layout.page || !Array.isArray(layout.elements)) return null
  if (typeof layout.page.widthMm !== 'number' || typeof layout.page.heightMm !== 'number') return null
  return {
    page: layout.page,
    elements: layout.elements.filter((element) => element && typeof element.id === 'string'),
    selectedElementId: typeof layout.selectedElementId === 'string' ? layout.selectedElementId : null,
    zoom: Number.isFinite(layout.zoom) ? Number(layout.zoom) : 0.9,
    mapScaleDenominator: Number.isFinite(layout.mapScaleDenominator)
      ? Number(layout.mapScaleDenominator)
      : 50000,
    mapSnapshotUrl: typeof layout.mapSnapshotUrl === 'string' ? layout.mapSnapshotUrl : null,
  }
}

export async function loadLayoutComposer(
  workspacePath: string | null,
): Promise<PersistedLayoutComposer | null> {
  if (!workspacePath || !window.electronAPI) return null
  try {
    const result = await window.electronAPI.readFile(getLayoutFilePath(workspacePath))
    if (!result?.success || !result.content) return null
    return parseLayout(JSON.parse(result.content))
  } catch {
    return null
  }
}

export function persistLayoutComposer(
  workspacePath: string | null,
  layout: PersistedLayoutComposer,
): void {
  if (!workspacePath || !window.electronAPI) return
  if (_pendingTimer) clearTimeout(_pendingTimer)
  _pendingTimer = setTimeout(() => {
    _pendingTimer = null
    void _writeLayout(workspacePath, layout)
  }, DEBOUNCE_MS)
}

export async function flushLayoutComposer(
  workspacePath: string | null,
  layout: PersistedLayoutComposer,
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return
  if (_pendingTimer) {
    clearTimeout(_pendingTimer)
    _pendingTimer = null
  }
  await _writeLayout(workspacePath, layout)
}

async function _writeLayout(
  workspacePath: string,
  layout: PersistedLayoutComposer,
): Promise<void> {
  if (!window.electronAPI) return
  try {
    await window.electronAPI.ensureDirectory(getOpengisDir(workspacePath))
    const payload = JSON.stringify({ version: 1, layout }, null, 2)
    await window.electronAPI.writeFile(getLayoutFilePath(workspacePath), payload)
  } catch (e) {
    console.error('[layoutPersistence] 写入制图画布失败:', e)
  }
}
