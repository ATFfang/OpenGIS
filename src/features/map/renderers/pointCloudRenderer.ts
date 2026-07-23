/**
 * 点云渲染器 —— 裸点云（.las / .laz）。
 *
 * 与 tiles3dRenderer 同属 deck.gl 叠加通道：不产出 MapLibre 图层 / source，
 * 交给共享的 `deckOverlay`（MapboxOverlay + PointCloudLayer + loaders.gl
 * LASLoader）渲染。
 *
 * 坐标：
 * - `coordinate='lnglat'`：点坐标已是经纬度，直接落位。
 * - `coordinate='meter-offset'`：点坐标是相对 `origin` 锚点的米偏移。
 */
import type { ParsedPointCloudData } from '@/services/geo'
import { deckOverlay } from './deckOverlay'
import { type LayerRenderer } from './types'

export const pointCloudRenderer: LayerRenderer = {
  renderType: 'pointcloud',

  attach(def, ctx) {
    if (def.data.kind !== 'pointcloud') return
    const data = def.data as ParsedPointCloudData
    const s = def.style.pointcloud ?? {}
    deckOverlay.upsertPointCloud(def.id, ctx.map, {
      url: data.url,
      format: data.format,
      coordinate: data.coordinate,
      origin: data.origin,
      visible: def.visible,
      pointSize: s.pointSize,
      color: s.color,
    })
  },

  update(def, ctx) {
    this.attach(def, ctx)
  },

  listRenderLayerIds() {
    return []
  },
}
