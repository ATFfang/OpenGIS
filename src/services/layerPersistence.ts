/**
 * Layer Persistence Service
 *
 * 把地图图层持久化到 `<workspace>/.opengis/map-layers.json`。
 *
 * 设计目标（修复"图层零持久化"与"跨工作区泄漏"）：
 * - 图层是工作区作用域：打开工作区时加载，切换/关闭工作区前 flush，
 *   切换后清空再加载新工作区的图层。
 * - 使用 Electron IPC（file:read / file:write / file:mkdir）。
 * - 防抖写盘，避免每次 store 变更都打盘。
 * - 优雅降级：没有工作区或非 Electron 环境时静默禁用。
 *
 * 注意：扩展（extension）托管的图层和基于 blob URL 的 image overlay
 * 不适合持久化（blob 重启即失效），这里按 `extension` 字段过滤掉扩展层；
 * image overlay 仍会被写入但重启后 imageUrl 可能失效（已知限制）。
 */

import type { MapLayerDefinition } from '@/services/geo'

const LAYERS_FILE = '.opengis/map-layers.json'
const DEBOUNCE_MS = 1000

let _pendingTimer: ReturnType<typeof setTimeout> | null = null

function getLayersFilePath(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/${LAYERS_FILE}`
}

function getOpengisDir(workspacePath: string): string {
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/.opengis`
}

/** 只持久化非扩展托管的图层。 */
function serializableLayers(layers: MapLayerDefinition[]): MapLayerDefinition[] {
  return layers.filter((l) => !l.extension)
}

/**
 * 从工作区加载图层。目录/文件不存在或非 Electron 环境返回空数组。
 */
export async function loadLayers(
  workspacePath: string | null,
): Promise<MapLayerDefinition[]> {
  if (!workspacePath || !window.electronAPI) return []

  const filePath = getLayersFilePath(workspacePath)
  try {
    const result = await window.electronAPI.readFile(filePath)
    if (!result?.success || !result.content) return []
    const data = JSON.parse(result.content)
    if (!Array.isArray(data?.layers)) return []
    // 宽松校验：保留结构正确的图层定义。
    return (data.layers as MapLayerDefinition[]).filter(
      (l) => l && typeof l.id === 'string' && l.data && l.style,
    )
  } catch {
    // 文件不存在 / JSON 损坏 → 视为无持久化数据
    return []
  }
}

/**
 * 防抖持久化图层。没有工作区时 no-op。
 */
export function persistLayers(
  workspacePath: string | null,
  layers: MapLayerDefinition[],
): void {
  if (!workspacePath || !window.electronAPI) return

  if (_pendingTimer) clearTimeout(_pendingTimer)
  _pendingTimer = setTimeout(() => {
    _pendingTimer = null
    void _writeLayers(workspacePath, layers)
  }, DEBOUNCE_MS)
}

/**
 * 立即持久化（不防抖）。在切换工作区或关闭应用前调用。
 */
export async function flushLayers(
  workspacePath: string | null,
  layers: MapLayerDefinition[],
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return
  if (_pendingTimer) {
    clearTimeout(_pendingTimer)
    _pendingTimer = null
  }
  await _writeLayers(workspacePath, layers)
}

async function _writeLayers(
  workspacePath: string,
  layers: MapLayerDefinition[],
): Promise<void> {
  if (!window.electronAPI) return
  const dir = getOpengisDir(workspacePath)
  const filePath = getLayersFilePath(workspacePath)
  try {
    await window.electronAPI.ensureDirectory(dir)
    const payload = JSON.stringify(
      { version: 1, layers: serializableLayers(layers) },
      null,
      2,
    )
    await window.electronAPI.writeFile(filePath, payload)
  } catch (e) {
    console.error('[layerPersistence] 写入图层失败:', e)
  }
}
