/**
 * 渲染器注册表 —— 按 renderType 获取对应的渲染器。
 *
 * MapEngine 使用此注册表进行统一分发，不再使用 switch。
 * 新增渲染器只需：
 *   1) 编写 `xxxRenderer.ts` 并实现 LayerRenderer 接口
 *   2) 在此文件 import 并添加到 ALL_RENDERERS
 */
import { fillRenderer } from './fillRenderer'
import { lineRenderer } from './lineRenderer'
import { circleRenderer } from './circleRenderer'
import { heatmapRenderer } from './heatmapRenderer'
import { graduatedRenderer } from './graduatedRenderer'
import { categorizedRenderer } from './categorizedRenderer'
import { clusterRenderer } from './clusterRenderer'
import { extrusionRenderer } from './extrusionRenderer'
import { rasterRenderer } from './rasterRenderer'
import type { LayerRenderer } from './types'

export const ALL_RENDERERS: LayerRenderer[] = [
  fillRenderer,
  lineRenderer,
  circleRenderer,
  heatmapRenderer,
  graduatedRenderer,
  categorizedRenderer,
  clusterRenderer,
  extrusionRenderer,
  rasterRenderer,
]

const REGISTRY: Record<string, LayerRenderer> = Object.fromEntries(
  ALL_RENDERERS.map((r) => [r.renderType, r]),
)

/**
 * 根据 renderType 获取对应的渲染器。
 * @param renderType - 渲染类型
 * @returns 渲染器实例，未找到时返回 undefined
 */
export function getRenderer(renderType: string): LayerRenderer | undefined {
  return REGISTRY[renderType]
}

/**
 * 获取所有已注册的渲染类型列表。
 * 供 UI 下拉框或 RPC schema 校验使用。
 * @returns 渲染类型字符串数组
 */
export function listRenderTypes(): string[] {
  return ALL_RENDERERS.map((r) => r.renderType)
}

export type { LayerRenderer, RendererContext } from './types'
export { renderLayerId, sourceIdFor } from './types'
