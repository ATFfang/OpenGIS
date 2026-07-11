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
import {
  compileSortKey,
  compileNumericVisualVariable,
  hoverColorExpr,
  hoverNumberExpr,
} from './styleExpressions'

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
    const radius = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.radius ?? 5, {
      defaultRange: [3, 14],
    })
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, def.style.opacity, {
      defaultRange: [0.25, def.style.opacity],
      clampRange: [0, 1],
    })
    const sortKey = compileSortKey(def.style.sortVariable)

    if (!map.getLayer(circleId)) {
      ctx.addRenderLayer({
        id: circleId,
        type: 'circle',
        source: sourceId,
        layout: { visibility, ...(sortKey ? { 'circle-sort-key': sortKey } : {}) },
        paint: {
          'circle-color': hoverColorExpr(def.style.color, '#6366f1') as any,
          'circle-radius': hoverNumberExpr(radius as any, 3) as any,
          'circle-opacity': opacity as any,
          'circle-stroke-color': hoverColorExpr(def.style.strokeColor, '#818cf8') as any,
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
      const radius = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.radius ?? 5, {
        defaultRange: [3, 14],
      })
      const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, def.style.opacity, {
        defaultRange: [0.25, def.style.opacity],
        clampRange: [0, 1],
      })
      ctx.map.setPaintProperty(circleId, 'circle-color', hoverColorExpr(def.style.color, '#6366f1') as any)
      ctx.map.setPaintProperty(circleId, 'circle-radius', hoverNumberExpr(radius as any, 3) as any)
      ctx.map.setPaintProperty(circleId, 'circle-opacity', opacity as any)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-color', hoverColorExpr(def.style.strokeColor, '#818cf8') as any)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-width', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        (def.style.strokeWidth ?? 1) + 2,
        def.style.strokeWidth,
      ] as any)
      ctx.map.setPaintProperty(circleId, 'circle-stroke-opacity', def.style.strokeOpacity ?? 1)
      ctx.map.setLayoutProperty(circleId, 'circle-sort-key', compileSortKey(def.style.sortVariable) as any)
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
