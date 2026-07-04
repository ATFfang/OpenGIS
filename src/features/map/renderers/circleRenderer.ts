/**
 * 圆点渲染器 —— Point / MultiPoint 的默认渲染方式。
 *
 * 渲染为实心圆 + 白色描边，支持悬停高亮效果。
 */
import {
  type LayerRenderer,
  renderLayerId,
  sourceIdFor,
} from './types'

export const circleRenderer: LayerRenderer = {
  renderType: 'circle',

  /**
   * 将圆点图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const circleId = renderLayerId(def.id, 'circle')
    const visibility = def.visible ? 'visible' : 'none'

    if (!map.getLayer(circleId)) {
      ctx.addRenderLayer({
        id: circleId,
        type: 'circle',
        source: sourceId,
        layout: { visibility },
        paint: {
          'circle-color': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            '#6366f1',
            def.style.color,
          ] as any,
          'circle-radius': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            (def.style.radius ?? 5) + 3,
            def.style.radius ?? 5,
          ] as any,
          'circle-opacity': def.style.opacity,
          'circle-stroke-color': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            '#818cf8',
            def.style.strokeColor,
          ] as any,
          'circle-stroke-width': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            (def.style.strokeWidth ?? 1) + 2,
            def.style.strokeWidth,
          ] as any,
          'circle-stroke-opacity': def.style.strokeOpacity ?? 1,
        },
      })
      ctx.registerRenderLayerId(def.id, circleId)
    }
  },

  /**
   * 更新圆点图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const circleId = renderLayerId(def.id, 'circle')
    if (ctx.map.getLayer(circleId)) {
      ctx.map.setPaintProperty(circleId, 'circle-color', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        '#6366f1',
        def.style.color,
      ] as any)
      ctx.map.setPaintProperty(circleId, 'circle-radius', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        (def.style.radius ?? 5) + 3,
        def.style.radius ?? 5,
      ] as any)
      ctx.map.setPaintProperty(circleId, 'circle-opacity', def.style.opacity)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-color', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        '#818cf8',
        def.style.strokeColor,
      ] as any)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-width', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        (def.style.strokeWidth ?? 1) + 2,
        def.style.strokeWidth,
      ] as any)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-opacity', def.style.strokeOpacity ?? 1)
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'circle')]
  },
}
