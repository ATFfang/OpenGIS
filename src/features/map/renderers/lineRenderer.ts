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
import {
  compileNumericVisualVariable,
  hoverColorExpr,
  hoverNumberExpr,
} from './styleExpressions'

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
    const width = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 2, {
      defaultRange: [1, 8],
    })
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, def.style.strokeOpacity ?? def.style.opacity, {
      defaultRange: [0.25, def.style.strokeOpacity ?? def.style.opacity],
      clampRange: [0, 1],
    })

    if (!map.getLayer(lineId)) {
      ctx.addRenderLayer({
        id: lineId,
        type: 'line',
        source: sourceId,
        layout: { visibility },
        paint: {
          'line-color': hoverColorExpr(def.style.color, '#818cf8') as any,
          'line-width': hoverNumberExpr(width as any, 3) as any,
          'line-opacity': opacity as any,
          ...(def.style.lineDasharray ? { 'line-dasharray': def.style.lineDasharray } : {}),
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
      const width = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 2, {
        defaultRange: [1, 8],
      })
      const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, def.style.strokeOpacity ?? def.style.opacity, {
        defaultRange: [0.25, def.style.strokeOpacity ?? def.style.opacity],
        clampRange: [0, 1],
      })
      ctx.map.setPaintProperty(lineId, 'line-color', hoverColorExpr(def.style.color, '#818cf8') as any)
      ctx.map.setPaintProperty(lineId, 'line-width', hoverNumberExpr(width as any, 3) as any)
      ctx.map.setPaintProperty(lineId, 'line-opacity', opacity as any)
      ctx.map.setPaintProperty(lineId, 'line-dasharray', def.style.lineDasharray ?? [1, 0])
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
