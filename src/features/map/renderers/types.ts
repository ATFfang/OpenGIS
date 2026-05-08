/**
 * Renderer 接口 —— 将 MapLayerDefinition 转换为 MapLibre 的若干渲染图层。
 *
 * 设计动机（用户明确要求"每个渲染功能一个单独代码文件"）：
 * - 原先 `MapEngine.syncRenderLayers` 是一个大 switch，每加一种样式就改一次。
 *   现在拆成 registry，每个 renderer 自治管理"添加/更新/移除"自己的
 *   渲染图层，MapEngine 只做分发和 id 记录。
 * - 每个 renderer 知道怎么算出自己的**子渲染图层 id 列表**（
 *   `layer-${defId}-<suffix>`）。MapEngine 用 id 前缀统一管理移除/重排。
 *
 * 命名约定：
 * - 所有 renderer 产出的子图层 id **必须**以 `layer-${def.id}-` 开头，
 *   suffix 部分各自规定。否则 `MapEngine.removeMapLayer` / `applyLayerOrder`
 *   的前缀扫描会遗漏。
 */
import type maplibregl from 'maplibre-gl'
import type { MapLayerDefinition } from '@/services/geo'

/**
 * Renderer 上下文 —— 封装 MapEngine 共享给 renderer 的能力。
 *
 * 抽这一层是为了让 renderer **不直接依赖** MapEngine 类，方便单元测试。
 */
export interface RendererContext {
  map: maplibregl.Map

  /**
   * 在样式已加载的情况下添加图层并同步到 `managedLayerIds` 跟踪集合。
   * Renderer 必须使用这个方法而不是直接调用 `map.addLayer`，
   * 否则 MapEngine 的 `removeMapLayer` 无法感知要删掉哪些 id。
   */
  addRenderLayer(layerSpec: { id: string; [key: string]: any }): void

  /**
   * 注册 Renderer 创建的 source id，让 MapEngine 在 removeMapLayer 时一并删除。
   */
  registerSourceId(sourceId: string): void

  /**
   * 注册 renderLayerId → defId 的映射，供 getDefIdFromRenderLayerId 使用。
   * 每个 renderer 在 attach() 中每添加一个子图层后都应调用此方法。
   */
  registerRenderLayerId(defId: string, renderLayerId: string): void
}

/**
 * 每个渲染模式实现这个接口。
 */
export interface LayerRenderer {
  /**
   * 此 renderer 对应的 `LayerStyle.renderType` 值。
   * MapEngine 用这个做 registry key。
   */
  readonly renderType: string

  /**
   * 首次将定义挂到 MapLibre 上。典型流程：
   *   1) addSource（若不存在）
   *   2) addLayer（每个子渲染层）
   * 每一步都要通过 `ctx.addRenderLayer` / `ctx.registerSourceId` 登记。
   *
   * **注意**：`attach` 应该幂等 —— 可能会被 basemap 切换后的
   * re-sync 重新调用。内部自行判断 `map.getLayer(id)` 再 addLayer。
   */
  attach(def: MapLayerDefinition, ctx: RendererContext): void

  /**
   * 热更新 paint/layout 属性（不销毁重建），由 mapStore 的
   * setLayerOpacity / updateLayerStyle 触发。
   *
   * 如果更新的内容 renderer 没法增量处理（比如 graduated 换字段要重建
   * expression），直接调用 `ctx.map.removeLayer` + `attach` 重新来即可，
   * MapEngine 不介意。
   */
  update(def: MapLayerDefinition, ctx: RendererContext): void

  /**
   * 返回此 renderer 会产生的所有**子渲染图层 id 列表**。
   * MapEngine 用它做 setLayerVisibility / applyLayerOrder。
   */
  listRenderLayerIds(def: MapLayerDefinition): string[]
}

// ─── 工具函数 ──────────────────────────────────────────────

/**
 * 生成一个子渲染图层 id 的约定前缀。
 */
export function renderLayerId(defId: string, suffix: string): string {
  return `layer-${defId}-${suffix}`
}

/**
 * Source id 约定：每个 MapLayerDefinition 只有一个 source。
 */
export function sourceIdFor(defId: string): string {
  return `source-${defId}`
}
