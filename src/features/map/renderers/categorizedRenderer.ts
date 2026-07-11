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
import { resolveVectorGeoJSON } from '@/services/geo'
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
  hoverOpacityExpr,
} from './styleExpressions'

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

    const fillOpacity = def.style.fillOpacity ?? def.style.opacity
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, fillOpacity, {
      defaultRange: [0.15, fillOpacity],
      clampRange: [0, 1],
    })
    const pointRadius = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.radius ?? 5, {
      defaultRange: [3, 14],
    })
    const lineWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [1, 8],
    })
    const strokeWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [0.5, 6],
    })
    const sortKey = compileSortKey(def.style.sortVariable)

    if (geomRenderType === 'fill') {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'fill',
        source: sourceId,
        layout: { visibility, ...(sortKey ? { 'fill-sort-key': sortKey } : {}) },
        paint: {
          'fill-color': hoverColorExpr(colorExpr, '#6366f1') as any,
          'fill-opacity': hoverOpacityExpr(opacity as any, fillOpacity) as any,
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
            'line-color': hoverColorExpr(def.style.strokeColor || '#555', '#818cf8') as any,
            'line-width': hoverNumberExpr(strokeWidth as any, 2) as any,
            'line-opacity': def.style.opacity,
            ...(def.style.lineDasharray ? { 'line-dasharray': def.style.lineDasharray } : {}),
          },
        })
        ctx.registerRenderLayerId(def.id, strokeId)
      }
    } else if (geomRenderType === 'circle') {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'circle',
        source: sourceId,
        layout: { visibility, ...(sortKey ? { 'circle-sort-key': sortKey } : {}) },
        paint: {
          'circle-color': hoverColorExpr(colorExpr, '#6366f1') as any,
          'circle-radius': hoverNumberExpr(pointRadius as any, 3) as any,
          'circle-opacity': opacity as any,
          'circle-stroke-color': hoverColorExpr(def.style.strokeColor, '#818cf8') as any,
          'circle-stroke-width': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            (def.style.strokeWidth ?? 0) + 2,
            def.style.strokeWidth,
          ] as any,
        },
      })
      ctx.registerRenderLayerId(def.id, mainLayerId)
    } else {
      ctx.addRenderLayer({
        id: mainLayerId,
        type: 'line',
        source: sourceId,
        layout: { visibility, ...(sortKey ? { 'line-sort-key': sortKey } : {}) },
        paint: {
          'line-color': hoverColorExpr(colorExpr, '#6366f1') as any,
          'line-width': hoverNumberExpr(lineWidth as any, 3) as any,
          'line-opacity': opacity as any,
          ...(def.style.lineDasharray ? { 'line-dasharray': def.style.lineDasharray } : {}),
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
    categorizedCache.set(def.id, resolved)

    const fillOpacity = def.style.fillOpacity ?? def.style.opacity
    const opacity = compileNumericVisualVariable(def, def.style.opacityVariable, fillOpacity, {
      defaultRange: [0.15, fillOpacity],
      clampRange: [0, 1],
    })
    const pointRadius = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.radius ?? 5, {
      defaultRange: [3, 14],
    })
    const lineWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [1, 8],
    })
    const strokeWidth = compileNumericVisualVariable(def, def.style.sizeVariable, def.style.strokeWidth ?? 1, {
      defaultRange: [0.5, 6],
    })

    // Update color expression with hover wrapper
    const prop =
      geomRenderType === 'fill'
        ? 'fill-color'
        : geomRenderType === 'circle'
          ? 'circle-color'
          : 'line-color'
    ctx.map.setPaintProperty(mainLayerId, prop, hoverColorExpr(colorExpr, '#6366f1') as any)

    // Sync common paint properties with hover support
    if (geomRenderType === 'fill') {
      ctx.map.setLayoutProperty(mainLayerId, 'fill-sort-key', compileSortKey(def.style.sortVariable) as any)
      ctx.map.setPaintProperty(mainLayerId, 'fill-opacity', hoverOpacityExpr(opacity as any, fillOpacity) as any)
      const strokeId = renderLayerId(def.id, 'stroke')
      if (ctx.map.getLayer(strokeId)) {
        ctx.map.setPaintProperty(strokeId, 'line-color', hoverColorExpr(def.style.strokeColor || '#555', '#818cf8') as any)
        ctx.map.setPaintProperty(strokeId, 'line-width', hoverNumberExpr(strokeWidth as any, 2) as any)
        ctx.map.setPaintProperty(strokeId, 'line-opacity', def.style.opacity)
        ctx.map.setPaintProperty(strokeId, 'line-dasharray', def.style.lineDasharray ?? [1, 0])
      }
    } else if (geomRenderType === 'circle') {
      ctx.map.setLayoutProperty(mainLayerId, 'circle-sort-key', compileSortKey(def.style.sortVariable) as any)
      ctx.map.setPaintProperty(mainLayerId, 'circle-radius', hoverNumberExpr(pointRadius as any, 3) as any)
      ctx.map.setPaintProperty(mainLayerId, 'circle-opacity', opacity as any)
      ctx.map.setPaintProperty(mainLayerId, 'circle-stroke-color', hoverColorExpr(def.style.strokeColor, '#818cf8') as any)
      ctx.map.setPaintProperty(mainLayerId, 'circle-stroke-width', ['case', ['boolean', ['feature-state', 'hover'], false], (def.style.strokeWidth ?? 0) + 2, def.style.strokeWidth] as any)
    } else {
      ctx.map.setLayoutProperty(mainLayerId, 'line-sort-key', compileSortKey(def.style.sortVariable) as any)
      ctx.map.setPaintProperty(mainLayerId, 'line-width', hoverNumberExpr(lineWidth as any, 3) as any)
      ctx.map.setPaintProperty(mainLayerId, 'line-opacity', opacity as any)
      ctx.map.setPaintProperty(mainLayerId, 'line-dasharray', def.style.lineDasharray ?? [1, 0])
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
  if (cfg.categories?.length) {
    const ordered: Record<string, string> = {}
    for (const value of cfg.categories) {
      if (colors[value]) ordered[value] = colors[value]
    }
    for (const [value, color] of Object.entries(colors)) {
      if (!(value in ordered)) ordered[value] = color
    }
    colors = ordered
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
  const fc = resolveVectorGeoJSON(def.data) as GeoJSONFeatureCollection
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
