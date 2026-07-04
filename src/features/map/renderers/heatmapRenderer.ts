/**
 * 热力图渲染器 —— 点热力图。
 *
 * 使用 MapLibre 原生的 `heatmap` 图层类型，GPU 渲染，
 * 支持百万级点数的渲染。
 *
 * 核心理念：热力图仅支持 Point 几何类型；
 * 对 Polygon / Line 类型将抛出警告且不生效。
 *
 * 参数来自 `def.style.heatmap`：
 * - weightField: 权重字段（数值），未指定则默认权重为 1
 * - radius: 核半径（像素），默认 30
 * - intensity: 整体强度，默认 1
 */
import {
  type LayerRenderer,
  renderLayerId,
  sourceIdFor,
} from './types'

const HEAT_PALETTE = [
  0, 'rgba(33,102,172,0)',
  0.2, 'rgb(103,169,207)',
  0.4, 'rgb(209,229,240)',
  0.6, 'rgb(253,219,199)',
  0.8, 'rgb(239,138,98)',
  1.0, 'rgb(178,24,43)',
]

export const heatmapRenderer: LayerRenderer = {
  renderType: 'heatmap',

  /**
   * 将热力图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const heatId = renderLayerId(def.id, 'heatmap')
    const visibility = def.visible ? 'visible' : 'none'

    const settings = def.style.heatmap ?? {}
    const radius = settings.radius ?? 30
    const intensity = settings.intensity ?? 1

    // 权重表达式：字段存在 → 归一化到 0-1，不存在 → 常数 1
    const weight = buildWeightExpression(settings.weightField)

    if (!map.getLayer(heatId)) {
      ctx.addRenderLayer({
        id: heatId,
        type: 'heatmap',
        source: sourceId,
        layout: { visibility },
        paint: {
          'heatmap-weight': weight,
          'heatmap-intensity': intensity,
          'heatmap-radius': radius,
          'heatmap-opacity': def.style.opacity,
          'heatmap-color': [
            'interpolate',
            ['linear'],
            ['heatmap-density'],
            ...HEAT_PALETTE,
          ] as any,
        },
      })
      ctx.registerRenderLayerId(def.id, heatId)
    }
  },

  /**
   * 更新热力图图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const heatId = renderLayerId(def.id, 'heatmap')
    if (!ctx.map.getLayer(heatId)) return

    const settings = def.style.heatmap ?? {}
    ctx.map.setPaintProperty(heatId, 'heatmap-radius', settings.radius ?? 30)
    ctx.map.setPaintProperty(
      heatId,
      'heatmap-intensity',
      settings.intensity ?? 1,
    )
    ctx.map.setPaintProperty(heatId, 'heatmap-opacity', def.style.opacity)
    ctx.map.setPaintProperty(
      heatId,
      'heatmap-weight',
      buildWeightExpression(settings.weightField),
    )
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'heatmap')]
  },
}

// ─── 内部函数 ──────────────────────────────────────

/**
 * 构造权重表达式。
 *
 * 如果 weightField 在字段中存在，则使用该字段的值；
 * 如果不存在，则默认权重为 1。
 * MapLibre 支持 `['coalesce', ['get', field], 1]` 这样的表达式。
 * @param def - 图层定义
 * @param weightField - 权重字段名
 * @returns 权重表达式
 */
function buildWeightExpression(weightField?: string): any {
  if (!weightField) return 1
  return [
    'case',
    ['==', ['typeof', ['get', weightField]], 'number'],
    ['get', weightField],
    1,
  ]
}
