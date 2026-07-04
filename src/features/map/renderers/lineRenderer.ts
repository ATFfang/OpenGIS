/**
 * 线渲染器 —— LineString / MultiLineString 的默认渲染方式。
 *
 * 使用单条 line 图层进行渲染，支持悬停高亮效果。
 */
import {
  type LayerRenderer,
  renderLayerId,
  sourceIdFor,
} from './types'

export const lineRenderer: LayerRenderer = {
  renderType: 'line',

  /**
   * 将线图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const lineId = renderLayerId(def.id, 'line')
    const visibility = def.visible ? 'visible' : 'none'

    if (!map.getLayer(lineId)) {
      ctx.addRenderLayer({
        id: lineId,
        type: 'line',
        source: sourceId,
        layout: { visibility },
        paint: {
          'line-color': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            '#818cf8',
            def.style.color,
          ] as any,
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            (def.style.strokeWidth ?? 2) + 3,
            def.style.strokeWidth,
          ] as any,
          'line-opacity': def.style.strokeOpacity ?? def.style.opacity,
        },
      })
      ctx.registerRenderLayerId(def.id, lineId)
    }
  },

  /**
   * 更新线图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const lineId = renderLayerId(def.id, 'line')
    if (ctx.map.getLayer(lineId)) {
      ctx.map.setPaintProperty(lineId, 'line-color', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        '#818cf8',
        def.style.color,
      ] as any)
      ctx.map.setPaintProperty(lineId, 'line-width', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        (def.style.strokeWidth ?? 2) + 3,
        def.style.strokeWidth,
      ] as any)
      ctx.map.setPaintProperty(lineId, 'line-opacity', def.style.strokeOpacity ?? def.style.opacity)
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'line')]
  },
}
