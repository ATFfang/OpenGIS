/**
 * 分类渲染器 —— 分类专题图。
 *
 * 按某个离散字段（类型/区划/行业）的值映射颜色。
 * 支持 Polygon / Point / Line 三种几何类型，
 * 几何类型决定使用 fill/circle/line 哪种 paint 属性来应用颜色表达式。
 *
 * 模式：
 * - 手动：`def.style.categorized.colors` = { value → hex }
 * - 自动：扫描要素取前 N 个频率最高的唯一值，
 *   按默认调色盘配色；其余归入"其它"档。
 */
import type {
  GeoJSONFeatureCollection,
  MapLayerDefinition,
} from '@/services/geo'
import {
  type LayerRenderer,
  renderLayerId,
  sourceIdFor,
} from './types'

// Tableau 10 — 常用分类配色
const DEFAULT_PALETTE = [
  '#4e79a7',
  '#f28e2b',
  '#e15759',
  '#76b7b2',
  '#59a14f',
  '#edc949',
  '#af7aa1',
  '#ff9da7',
  '#9c755f',
  '#bab0ab',
  '#7fc97f',
  '#beaed4',
]

/** 缓存 buildCategorized 的计算结果，避免直接修改 def.style.categorized */
const categorizedCache = new Map<string, Record<string, string>>()

/** 供 legend 组件读取计算结果的 getter */
export function getCategorizedCache(defId: string): Record<string, string> | undefined {
  return categorizedCache.get(defId)
}

export const categorizedRenderer: LayerRenderer = {
  renderType: 'categorized',

  /**
   * 将分类渲染图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const { geomRenderType, mainLayerId } = geomToRenderLayer(def)
    const sourceId = sourceIdFor(def.id)
    const visibility = def.visible ? 'visible' : 'none'

    if (map.getLayer(mainLayerId)) return

    const { colorExpr, resolved } = buildCategorized(def)
    // 存入缓存，不再直接修改 def.style
    categorizedCache.set(def.id, resolved)

    if (geomRenderType === 'fill') {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'fill',
        source: sourceId,
        layout: { visibility },
        paint: {
          'fill-color': colorExpr,
          'fill-opacity': def.style.fillOpacity ?? def.style.opacity,
        },
      })
      ctx.registerRenderLayerId(def.id, mainLayerId)
      const strokeId = renderLayerId(def.id, 'stroke')
      if (!map.getLayer(strokeId)) {
        ctx.addRenderLayer({
          id: strokeId,
          type: 'line',
          source: sourceId,
          layout: { visibility },
          paint: {
            'line-color': def.style.strokeColor || '#555',
            'line-width': def.style.strokeWidth,
            'line-opacity': def.style.opacity,
          },
        })
        ctx.registerRenderLayerId(def.id, strokeId)
      }
    } else if (geomRenderType === 'circle') {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'circle',
        source: sourceId,
        layout: { visibility },
        paint: {
          'circle-color': colorExpr,
          'circle-radius': def.style.radius ?? 5,
          'circle-opacity': def.style.opacity,
          'circle-stroke-color': def.style.strokeColor,
          'circle-stroke-width': def.style.strokeWidth,
        },
      })
      ctx.registerRenderLayerId(def.id, mainLayerId)
    } else {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'line',
        source: sourceId,
        layout: { visibility },
        paint: {
          'line-color': colorExpr,
          'line-width': def.style.strokeWidth,
          'line-opacity': def.style.opacity,
        },
      })
      ctx.registerRenderLayerId(def.id, mainLayerId)
    }
  },

  /**
   * 更新分类渲染图层的样式属性。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const { geomRenderType, mainLayerId } = geomToRenderLayer(def)
    if (!ctx.map.getLayer(mainLayerId)) return
    const { colorExpr, resolved } = buildCategorized(def)
    // 存入缓存，不再直接修改 def.style
    categorizedCache.set(def.id, resolved)

    // Update color expression
    const prop =
      geomRenderType === 'fill'
        ? 'fill-color'
        : geomRenderType === 'circle'
          ? 'circle-color'
          : 'line-color'
    ctx.map.setPaintProperty(mainLayerId, prop, colorExpr)

    // Sync common paint properties (strokeWidth, radius, opacity)
    if (geomRenderType === 'fill') {
      ctx.map.setPaintProperty(mainLayerId, 'fill-opacity', def.style.fillOpacity ?? def.style.opacity)
      const strokeId = renderLayerId(def.id, 'stroke')
      if (ctx.map.getLayer(strokeId)) {
        ctx.map.setPaintProperty(strokeId, 'line-color', def.style.strokeColor || '#555')
        ctx.map.setPaintProperty(strokeId, 'line-width', def.style.strokeWidth)
        ctx.map.setPaintProperty(strokeId, 'line-opacity', def.style.opacity)
      }
    } else if (geomRenderType === 'circle') {
      ctx.map.setPaintProperty(mainLayerId, 'circle-radius', def.style.radius ?? 5)
      ctx.map.setPaintProperty(mainLayerId, 'circle-opacity', def.style.opacity)
      ctx.map.setPaintProperty(mainLayerId, 'circle-stroke-color', def.style.strokeColor)
      ctx.map.setPaintProperty(mainLayerId, 'circle-stroke-width', def.style.strokeWidth)
    } else {
      ctx.map.setPaintProperty(mainLayerId, 'line-width', def.style.strokeWidth)
      ctx.map.setPaintProperty(mainLayerId, 'line-opacity', def.style.opacity)
    }
  },

  /**
   * 获取该渲染器管理的所有渲染图层 ID。
   * @param def - 图层定义
   * @returns 渲染图层 ID 数组
   */
  listRenderLayerIds(def) {
    const { geomRenderType, mainLayerId } = geomToRenderLayer(def)
    if (geomRenderType === 'fill') {
      return [mainLayerId, renderLayerId(def.id, 'stroke')]
    }
    return [mainLayerId]
  },
}

// ─── 辅助函数 ──────────────────────────────────────

/**
 * 根据图层定义的几何类型，确定渲染图层类型和 ID。
 * @param def - 图层定义
 * @returns 包含几何渲染类型和主图层 ID 的对象
 */
function geomToRenderLayer(def: MapLayerDefinition): {
  geomRenderType: 'fill' | 'circle' | 'line'
  mainLayerId: string
} {
  if (def.data.kind !== 'vector') {
    return { geomRenderType: 'fill', mainLayerId: renderLayerId(def.id, 'cat-fill') }
  }
  const t = def.data.geometryType
  if (t === 'Polygon' || t === 'MultiPolygon') {
    return { geomRenderType: 'fill', mainLayerId: renderLayerId(def.id, 'cat-fill') }
  }
  if (t === 'LineString' || t === 'MultiLineString') {
    return { geomRenderType: 'line', mainLayerId: renderLayerId(def.id, 'cat-line') }
  }
  return { geomRenderType: 'circle', mainLayerId: renderLayerId(def.id, 'cat-circle') }
}

/**
 * 构造 `match` 表达式，按字段值映射颜色。
 *
 * 表达式格式：
 * ['match', ['to-string', ['get', field]], v1, c1, v2, c2, ..., otherColor]
 *
 * @param def - 图层定义
 * @returns 包含颜色表达式和解析结果的对象
 */
function buildCategorized(def: MapLayerDefinition): {
  colorExpr: any
  resolved: Record<string, string>
} {
  const cfg = def.style.categorized
  if (!cfg) {
    return { colorExpr: def.style.color, resolved: {} }
  }
  const otherColor = cfg.otherColor ?? '#9ca3af'
  let colors = cfg.colors
  if (!colors || Object.keys(colors).length === 0) {
    colors = autoCategorize(def, cfg.field, cfg.maxCategories ?? 12)
  }

  const entries = Object.entries(colors)
  if (entries.length === 0) {
    return { colorExpr: def.style.color, resolved: {} }
  }

  const expr: any[] = ['match', ['to-string', ['get', cfg.field]]]
  for (const [value, color] of entries) {
    expr.push(value, color)
  }
  expr.push(otherColor)
  return { colorExpr: expr, resolved: colors }
}

/**
 * 自动分类：按频率取前 N 个唯一值并分配颜色。
 * @param def - 图层定义
 * @param field - 分类字段名
 * @param maxCategories - 最大分类数量
 * @returns 值到颜色的映射对象
 */
function autoCategorize(
  def: MapLayerDefinition,
  field: string,
  maxCategories: number,
): Record<string, string> {
  if (def.data.kind !== 'vector') return {}
  const fc = def.data.geojson as GeoJSONFeatureCollection
  const counts = new Map<string, number>()

  for (const f of fc.features) {
    const raw = (f.properties ?? {})[field]
    if (raw === null || raw === undefined) continue
    const key = String(raw)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }

  const sorted = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, maxCategories)

  const out: Record<string, string> = {}
  sorted.forEach(([value], i) => {
    out[value] = DEFAULT_PALETTE[i % DEFAULT_PALETTE.length]
  })
  return out
}
