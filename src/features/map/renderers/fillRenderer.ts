/**
 * 面渲染器 —— Polygon / MultiPolygon 的默认渲染方式。
 *
 * 使用 fill + stroke（line）两个渲染图层。
 *
 * Fill-layer rendering helpers extracted from `MapEngine.syncRenderLayers`,
 * 此处仅作抽离，不改变语义。
 */
import type { MapLayerDefinition } from '@/services/geo'
import {
  type LayerRenderer,
  type RendererContext,
  renderLayerId,
  sourceIdFor,
} from './types'
import {
  compileSortKey,
  compileNumericVisualVariable,
  hoverColorExpr,
  hoverNumberExpr,
  hoverOpacityExpr,
} from './styleExpressions'

export const fillRenderer: LayerRenderer = {
  renderType: 'fill',

  /**
   * 将面图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const fillId = renderLayerId(def.id, 'fill')
    const strokeId = renderLayerId(def.id, 'stroke')
    const visibility = def.visible ? 'visible' : 'none'
    const fillOpacity = def.style.fillOpacity ?? def.style.opacity
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, fillOpacity, {
      defaultRange: [0.15, fillOpacity],
      clampRange: [0, 1],
    })
    const strokeWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [0.5, 6],
    })
    const sortKey = compileSortKey(def.style.sortVariable)

    if (!map.getLayer(fillId)) {
      ctx.addRenderLayer({
        id: fillId,
        type: 'fill',
        source: sourceId,
        layout: { visibility, ...(sortKey ? { 'fill-sort-key': sortKey } : {}) },
        paint: {
          'fill-color': hoverColorExpr(def.style.color, '#6366f1') as any,
          'fill-opacity': hoverOpacityExpr(opacity as any, fillOpacity) as any,
        },
      })
      ctx.registerRenderLayerId(def.id, fillId)
    }

    if (!map.getLayer(strokeId)) {
      ctx.addRenderLayer({
        id: strokeId,
        type: 'line',
        source: sourceId,
        layout: { visibility },
        paint: {
          'line-color': hoverColorExpr(def.style.strokeColor || def.style.color, '#818cf8') as any,
          'line-width': hoverNumberExpr(strokeWidth as any, 2) as any,
          'line-opacity': def.style.strokeOpacity ?? def.style.opacity,
          ...(def.style.lineDasharray ? { 'line-dasharray': def.style.lineDasharray } : {}),
        },
      })
      ctx.registerRenderLayerId(def.id, strokeId)
    }
  },

  /**
   * 更新面图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const fillId = renderLayerId(def.id, 'fill')
    const strokeId = renderLayerId(def.id, 'stroke')
    const fillOpacity = def.style.fillOpacity ?? def.style.opacity
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, fillOpacity, {
      defaultRange: [0.15, fillOpacity],
      clampRange: [0, 1],
    })
    const strokeWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [0.5, 6],
    })
    if (ctx.map.getLayer(fillId)) {
      ctx.map.setPaintProperty(fillId, 'fill-color', hoverColorExpr(def.style.color, '#6366f1') as any)
      ctx.map.setPaintProperty(fillId, 'fill-opacity', hoverOpacityExpr(opacity as any, fillOpacity) as any)
      ctx.map.setLayoutProperty(fillId, 'fill-sort-key', compileSortKey(def.style.sortVariable) as any)
    }
    if (ctx.map.getLayer(strokeId)) {
      ctx.map.setPaintProperty(strokeId, 'line-color', hoverColorExpr(def.style.strokeColor || def.style.color, '#818cf8') as any)
      ctx.map.setPaintProperty(strokeId, 'line-width', hoverNumberExpr(strokeWidth as any, 2) as any)
      ctx.map.setPaintProperty(strokeId, 'line-opacity', def.style.strokeOpacity ?? def.style.opacity)
      ctx.map.setPaintProperty(strokeId, 'line-dasharray', def.style.lineDasharray ?? [1, 0])
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'fill'), renderLayerId(def.id, 'stroke')]
  },
}

/**
 * 将面图层挂载到地图上的便捷函数。
 * @param def - 图层定义
 * @param ctx - 渲染器上下文
 */
export function attachFill(def: MapLayerDefinition, ctx: RendererContext): void {
  fillRenderer.attach(def, ctx)
}
