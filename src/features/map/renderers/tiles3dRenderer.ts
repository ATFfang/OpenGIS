/**
 * 3D Tiles 渲染器 —— OGC 3D Tiles（tileset.json + b3dm/i3dm/pnts + glTF）。
 *
 * MapLibre 无法原生渲染 3D Tiles，因此本 renderer 不产出任何 MapLibre
 * 渲染图层 / source，而是把定义交给共享的 `deckOverlay`（deck.gl
 * MapboxOverlay + Tile3DLayer）叠加渲染。相机与 MapLibre 共享。
 *
 * 由于不产出 MapLibre 图层：
 * - `listRenderLayerIds` 返回空数组（MapEngine 的 order/filter 会安全跳过）。
 * - 增删 / 可见性由 MapEngine 的对应钩子转发给 `deckOverlay`（按 defId）。
 */
import type { ParsedTiles3DData } from '@/services/geo'
import { deckOverlay } from './deckOverlay'
import { type LayerRenderer } from './types'

export const tiles3dRenderer: LayerRenderer = {
  renderType: 'tiles3d',

  attach(def, ctx) {
    if (def.data.kind !== 'tiles3d') return
    const data = def.data as ParsedTiles3DData
    const s = def.style.tiles3d ?? {}
    deckOverlay.upsertTiles3D(def.id, ctx.map, {
      tilesetUrl: data.tilesetUrl,
      visible: def.visible,
      pointSize: s.pointSize,
      color: s.color,
      maximumScreenSpaceError: s.maximumScreenSpaceError,
    })
  },

  update(def, ctx) {
    // 样式增量更新等价于重新 upsert（deck.gl 内部 diff）。
    this.attach(def, ctx)
  },

  listRenderLayerIds() {
    // 无 MapLibre 子图层；deck 图层由 deckOverlay 自管。
    return []
  },
}
