/**
 * 栅格渲染器 —— 栅格图层（本地 GeoTIFF 解成 PNG 或 XYZ/WMTS 瓦片）。
 *
 * 与矢量渲染器的主要差别：数据源类型不同（image / raster 而非 geojson）。
 * 这意味着 MapEngine 不能使用"统一的 addSource geojson"方式，
 * 需要渲染器根据 ParsedRasterData.source 决定如何 addSource。
 */
import type { ParsedRasterData } from '@/services/geo'
import {
  type LayerRenderer,
  type RendererContext,
  renderLayerId,
  sourceIdFor,
} from './types'

export const rasterRenderer: LayerRenderer = {
  renderType: 'raster',

  /**
   * 将栅格渲染图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    if (def.data.kind !== 'raster') return
    const raster = def.data as ParsedRasterData

    const sourceId = sourceIdFor(def.id)
    const rasterId = renderLayerId(def.id, 'raster')
    const visibility = def.visible ? 'visible' : 'none'
    const { map } = ctx

    // ─── 添加数据源 ──────────────────────────────────────
    if (!map.getSource(sourceId)) {
      if (raster.source === 'image' && raster.imageUrl) {
        const coords = raster.imageCoordinates ?? [
          [raster.bbox.minX, raster.bbox.maxY],
          [raster.bbox.maxX, raster.bbox.maxY],
          [raster.bbox.maxX, raster.bbox.minY],
          [raster.bbox.minX, raster.bbox.minY],
        ]
        map.addSource(sourceId, {
          type: 'image',
          url: raster.imageUrl,
          coordinates: coords,
        } as any)
      } else if (
        (raster.source === 'tile-xyz' || raster.source === 'tile-wmts') &&
        raster.tileUrl
      ) {
        map.addSource(sourceId, {
          type: 'raster',
          tiles: [raster.tileUrl],
          tileSize: 256,
          // bounds 让 MapLibre 在 bbox 外不去请求瓦片
          bounds: [
            raster.bbox.minX,
            raster.bbox.minY,
            raster.bbox.maxX,
            raster.bbox.maxY,
          ],
        } as any)
      } else {
        console.warn(
          '[rasterRenderer] unsupported raster source config:',
          raster.source,
          'layer:',
          def.id,
        )
        return
      }
      ctx.registerSourceId(sourceId)
    }

    // ─── 添加图层 ──────────────────────────────────────
    if (!map.getLayer(rasterId)) {
      ctx.addRenderLayer({
        id: rasterId,
        type: 'raster',
        source: sourceId,
        layout: { visibility },
        paint: {
          'raster-opacity': def.style.opacity,
        },
      })
      ctx.registerRenderLayerId(def.id, rasterId)
    }
  },

  /**
   * 更新栅格渲染图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const rasterId = renderLayerId(def.id, 'raster')
    if (ctx.map.getLayer(rasterId)) {
      ctx.map.setPaintProperty(rasterId, 'raster-opacity', def.style.opacity)
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'raster')]
  },
}

/**
 * 将栅格图层挂载到地图上的便捷函数。
 * @param def - 图层定义
 * @param ctx - 渲染器上下文
 */
export function attachRaster(
  def: Parameters<LayerRenderer['attach']>[0],
  ctx: RendererContext,
): void {
  rasterRenderer.attach(def, ctx)
}
