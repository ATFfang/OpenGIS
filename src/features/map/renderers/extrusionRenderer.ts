/**
 * 拉伸渲染器 —— 3D 拉伸（fill-extrusion）。
 *
 * 将 Polygon 按某个数值字段拉伸出高度，
 * 常用于建筑物高度、人口密度柱状图、气温分级等场景。
 *
 * 注意：必须设置 pitch > 0 才能看到 3D 效果；
 * MapEngine.init 的 pitch 默认是 0，MapLibre 不会自动调整。
 * 渲染器本身不修改 pitch（那是 viewState 的职责），只负责 paint 属性。
 */
import {
  type LayerRenderer,
  renderLayerId,
  sourceIdFor,
} from './types'

export const extrusionRenderer: LayerRenderer = {
  renderType: 'extrusion',

  /**
   * 将拉伸渲染图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const cfg = def.style.extrusion
    if (!cfg) return

    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const extrudeId = renderLayerId(def.id, 'extrude')
    const visibility = def.visible ? 'visible' : 'none'
    const multiplier = cfg.heightMultiplier ?? 1

    if (!map.getLayer(extrudeId)) {
      ctx.addRenderLayer({
        id: extrudeId,
        type: 'fill-extrusion',
        source: sourceId,
        layout: { visibility },
        paint: {
          'fill-extrusion-color': def.style.color,
          'fill-extrusion-opacity': def.style.opacity,
          'fill-extrusion-height': [
            '*',
            ['to-number', ['get', cfg.heightField]],
            multiplier,
          ] as any,
          'fill-extrusion-base': cfg.baseField
            ? (['to-number', ['get', cfg.baseField]] as any)
            : 0,
        },
      })
      ctx.registerRenderLayerId(def.id, extrudeId)
    }
  },

  /**
   * 更新拉伸渲染图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const cfg = def.style.extrusion
    if (!cfg) return
    const extrudeId = renderLayerId(def.id, 'extrude')
    if (!ctx.map.getLayer(extrudeId)) return
    ctx.map.setPaintProperty(extrudeId, 'fill-extrusion-color', def.style.color)
    ctx.map.setPaintProperty(extrudeId, 'fill-extrusion-opacity', def.style.opacity)
    ctx.map.setPaintProperty(extrudeId, 'fill-extrusion-height', [
      '*',
      ['to-number', ['get', cfg.heightField]],
      cfg.heightMultiplier ?? 1,
    ] as any)
    ctx.map.setPaintProperty(
      extrudeId,
      'fill-extrusion-base',
      cfg.baseField
        ? (['to-number', ['get', cfg.baseField]] as any)
        : 0,
    )
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'extrude')]
  },
}
