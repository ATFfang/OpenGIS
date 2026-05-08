/**
 * 面渲染器 —— Polygon / MultiPolygon 的默认渲染方式。
 *
 * 使用 fill + stroke（line）两个渲染图层。
 *
 * 继承自 Stage 3.9 之前 `MapEngine.syncRenderLayers` 的 fill 处理逻辑，
 * 此处仅作抽离，不改变语义。
 */
import type { MapLayerDefinition } from '@/services/geo'
import {
  type LayerRenderer,
  type RendererContext,
  renderLayerId,
  sourceIdFor,
} from './types'

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

    if (!map.getLayer(fillId)) {
      ctx.addRenderLayer({
        id: fillId,
        type: 'fill',
        source: sourceId,
        layout: { visibility },
        paint: {
          'fill-color': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            '#6366f1',
            def.style.color,
          ] as any,
          'fill-opacity': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            Math.min((def.style.fillOpacity ?? def.style.opacity) + 0.25, 0.85),
            def.style.fillOpacity ?? def.style.opacity,
          ] as any,
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
          'line-color': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            '#818cf8',
            def.style.strokeColor || def.style.color,
          ] as any,
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            (def.style.strokeWidth ?? 1) + 2,
            def.style.strokeWidth,
          ] as any,
          'line-opacity': def.style.opacity,
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
    if (ctx.map.getLayer(fillId)) {
      ctx.map.setPaintProperty(fillId, 'fill-color', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        '#6366f1',
        def.style.color,
      ] as any)
      ctx.map.setPaintProperty(fillId, 'fill-opacity', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        Math.min((def.style.fillOpacity ?? def.style.opacity) + 0.25, 0.85),
        def.style.fillOpacity ?? def.style.opacity,
      ] as any)
    }
    if (ctx.map.getLayer(strokeId)) {
      ctx.map.setPaintProperty(strokeId, 'line-color', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        '#818cf8',
        def.style.strokeColor || def.style.color,
      ] as any)
      ctx.map.setPaintProperty(strokeId, 'line-width', [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        (def.style.strokeWidth ?? 1) + 2,
        def.style.strokeWidth,
      ] as any)
      ctx.map.setPaintProperty(strokeId, 'line-opacity', def.style.opacity)
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
