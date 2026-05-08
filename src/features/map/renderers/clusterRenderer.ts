/**
 * 聚合渲染器 —— 点聚合显示。
 *
 * 关键点：MapLibre 原生聚合是在 **source** 层面开启的，
 * 必须设置 `cluster: true` 才会产出 `cluster/cluster_id/point_count` 字段。
 * 因此聚合渲染器要求对应的 source 在 attach 时已经是 cluster-source。
 *
 * 当前 MapEngine 的流程是：MapLayerDefinition 改变 → syncLayer
 * （addSource with type='geojson', cluster: undefined）。
 * 要实现聚合功能，有两种方案：
 * - 让 MapEngine 感知 renderType='cluster' 时重建 source；
 * - 让渲染器本身负责 remove + re-add source。
 *
 * 本文件采用后者：attach 时若检测到现有 source 不是 cluster-source 就替换掉。
 * 这样 MapEngine 无需特判 cluster。
 *
 * 子渲染图层：
 *   - `-cluster-circle`：聚合圆圈（半径按 point_count 阶梯变化）
 *   - `-cluster-count`：聚合的数字标签（symbol 类型）
 *   - `-cluster-point`：未聚合的独立点
 */
import {
  type LayerRenderer,
  type RendererContext,
  renderLayerId,
  sourceIdFor,
} from './types'
import type { MapLayerDefinition, ParsedVectorData } from '@/services/geo'

export const clusterRenderer: LayerRenderer = {
  renderType: 'cluster',

  /**
   * 将聚合渲染图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const settings = def.style.cluster ?? {}
    const radius = settings.radius ?? 50
    const maxZoom = settings.maxZoom ?? 14

    ensureClusterSource(def, ctx, radius, maxZoom)

    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const visibility = def.visible ? 'visible' : 'none'

    const clusterCircleId = renderLayerId(def.id, 'cluster-circle')
    const clusterCountId = renderLayerId(def.id, 'cluster-count')
    const singlePointId = renderLayerId(def.id, 'cluster-point')

    if (!map.getLayer(clusterCircleId)) {
      ctx.addRenderLayer({
        id: clusterCircleId,
        type: 'circle',
        source: sourceId,
        filter: ['has', 'point_count'],
        layout: { visibility },
        paint: {
          // 三档半径：<20 / <100 / 其它
          'circle-color': [
            'step',
            ['get', 'point_count'],
            def.style.color,
            20,
            lighten(def.style.color, 0.3),
            100,
            lighten(def.style.color, 0.6),
          ] as any,
          'circle-radius': [
            'step',
            ['get', 'point_count'],
            16,
            20,
            22,
            100,
            30,
          ] as any,
          'circle-opacity': def.style.opacity,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      })
    }

    if (!map.getLayer(clusterCountId)) {
      ctx.addRenderLayer({
        id: clusterCountId,
        type: 'symbol',
        source: sourceId,
        filter: ['has', 'point_count'],
        layout: {
          visibility,
          'text-field': ['get', 'point_count_abbreviated'] as any,
          'text-size': 12,
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': '#ffffff',
        },
      })
    }

    if (!map.getLayer(singlePointId)) {
      ctx.addRenderLayer({
        id: singlePointId,
        type: 'circle',
        source: sourceId,
        filter: ['!', ['has', 'point_count']],
        layout: { visibility },
        paint: {
          'circle-color': def.style.color,
          'circle-radius': def.style.radius ?? 5,
          'circle-opacity': def.style.opacity,
          'circle-stroke-color': def.style.strokeColor || '#fff',
          'circle-stroke-width': def.style.strokeWidth,
        },
      })
    }
  },

  /**
   * 更新聚合渲染图层的样式属性。
   *
   * 聚合半径或最大缩放级别变化时需要重建 source。
   * 若参数一致，则只更新颜色等属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    // cluster radius / maxZoom 变化只能通过 source 重建，简单粗暴：
    const clusterSource = ctx.map.getSource(sourceIdFor(def.id)) as any
    const settings = def.style.cluster ?? {}
    const radius = settings.radius ?? 50
    const maxZoom = settings.maxZoom ?? 14

    // 若现有 source 是 cluster 且参数一致，只改颜色即可；否则重建
    const needsRebuild =
      !clusterSource ||
      !clusterSource._options?.cluster ||
      clusterSource._options?.clusterRadius !== radius ||
      clusterSource._options?.clusterMaxZoom !== maxZoom
    if (needsRebuild) {
      // remove all layers + source，重 attach
      for (const id of this.listRenderLayerIds(def)) {
        if (ctx.map.getLayer(id)) ctx.map.removeLayer(id)
      }
      if (ctx.map.getSource(sourceIdFor(def.id))) {
        ctx.map.removeSource(sourceIdFor(def.id))
      }
      this.attach(def, ctx)
      return
    }

    const singlePointId = renderLayerId(def.id, 'cluster-point')
    if (ctx.map.getLayer(singlePointId)) {
      ctx.map.setPaintProperty(singlePointId, 'circle-color', def.style.color)
      ctx.map.setPaintProperty(singlePointId, 'circle-radius', def.style.radius ?? 5)
      ctx.map.setPaintProperty(singlePointId, 'circle-opacity', def.style.opacity)
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    return [
      renderLayerId(def.id, 'cluster-circle'),
      renderLayerId(def.id, 'cluster-count'),
      renderLayerId(def.id, 'cluster-point'),
    ]
  },
}

// ─── 辅助函数 ──────────────────────────────────────

/**
 * 确保数据源已配置为聚合源。
 *
 * 如果现有 source 不是 cluster 类型，则删除旧 source 并创建新的 cluster source。
 * @param def - 图层定义
 * @param ctx - 渲染器上下文
 * @param radius - 聚合半径
 * @param maxZoom - 最大聚合缩放级别
 */
function ensureClusterSource(
  def: MapLayerDefinition,
  ctx: RendererContext,
  radius: number,
  maxZoom: number,
): void {
  const sourceId = sourceIdFor(def.id)
  const existing = ctx.map.getSource(sourceId) as any
  const isAlreadyCluster =
    existing && existing._options?.cluster === true

  if (isAlreadyCluster) return

  // 删掉旧 source（如果有，说明是 GeoJSON 源而非 cluster 源）
  if (existing) {
    // 必须先删依赖它的 render layer
    const style = ctx.map.getStyle()
    if (style?.layers) {
      for (const l of style.layers) {
        if ((l as any).source === sourceId && ctx.map.getLayer(l.id)) {
          ctx.map.removeLayer(l.id)
        }
      }
    }
    ctx.map.removeSource(sourceId)
  }

  if (def.data.kind !== 'vector') return
  const vec = def.data as ParsedVectorData
  ctx.map.addSource(sourceId, {
    type: 'geojson',
    data: vec.geojson as any,
    cluster: true,
    clusterRadius: radius,
    clusterMaxZoom: maxZoom,
  } as any)
  ctx.registerSourceId(sourceId)
}

/**
 * 十六进制颜色变亮：与白色做线性插值。
 * @param hex - 十六进制颜色值（如 '#ff0000'）
 * @param amount - 变亮程度（0-1 之间）
 * @returns 变亮后的十六进制颜色值
 */
function lighten(hex: string, amount: number): string {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  const mix = (c: number) => Math.round(c + (255 - c) * amount)
  const toHex = (v: number) => v.toString(16).padStart(2, '0')
  return `#${toHex(mix(r))}${toHex(mix(g))}${toHex(mix(b))}`
}
