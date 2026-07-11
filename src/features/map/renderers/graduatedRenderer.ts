/**
 * 分级渲染器 —— 分级专题图（choropleth）。
 *
 * 按某个数值字段分档（分位数 / 等距 / 自然断点 / 手动），每档一个颜色。
 * 对 Polygon 渲染成 fill，对 Point 渲染成 circle（颜色使用分档表达式），
 * 对 Line 渲染成 line（颜色使用分档表达式）。
 *
 * 断点算法：
 * - quantile: 把样本排序后按分位取断点。典型的专题图默认算法。
 * - equal-interval: (max - min) / classes 等距。
 * - jenks: 简化实现 — 回退到 quantile（真正的 Jenks 优化代价较高，
 *   当前版本够用；需要时再接入 simple-statistics）。
 * - manual: 直接使用用户传入的 breaks。
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

// Viridis-ish 默认色带
const DEFAULT_PALETTES: Record<number, string[]> = {
  3: ['#fde725', '#21918c', '#440154'],
  4: ['#fde725', '#5ec962', '#3b528b', '#440154'],
  5: ['#fde725', '#5ec962', '#21918c', '#3b528b', '#440154'],
  6: ['#fde725', '#a0da39', '#4ac16d', '#1fa187', '#365c8d', '#440154'],
  7: ['#fde725', '#bddf26', '#5ec962', '#21918c', '#31688e', '#440154', '#20103a'],
}

/** 缓存 buildGraduated 的计算结果，避免直接修改 def.style.graduated */
const graduateCache = new Map<string, { breaks: number[]; palette: string[] }>()

/** 供 legend 组件读取计算结果的 getter */
export function getGraduatedCache(defId: string): { breaks: number[]; palette: string[] } | undefined {
  return graduateCache.get(defId)
}

export const graduatedRenderer: LayerRenderer = {
  renderType: 'graduated',

  /**
   * 将分级渲染图层挂载到地图上。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  attach(def, ctx) {
    const { map } = ctx
    const { geomRenderType, mainLayerId } = geomToRenderLayer(def)
    const sourceId = sourceIdFor(def.id)
    const visibility = def.visible ? 'visible' : 'none'

    if (map.getLayer(mainLayerId)) return

    const { colorExpr, breaks, palette } = buildGraduated(def)
    // 存入缓存，供 legend 组件读取（不再直接修改 def.style）
    graduateCache.set(def.id, { breaks, palette })

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
    } else if (geomRenderType === 'line') {
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
   * 更新分级渲染图层的样式属性。
   *
   * 更新时会重建表达式并替换 paint 属性。
   * 断点字段变化时需要重建整个图层，但 MapLibre 支持通过
   * setPaintProperty 直接更新表达式，因此此处使用增量更新。
   * @param def - 图层定义
   * @param ctx - 渲染器上下文
   */
  update(def, ctx) {
    const { geomRenderType, mainLayerId } = geomToRenderLayer(def)
    if (!ctx.map.getLayer(mainLayerId)) return

    const { colorExpr, breaks, palette } = buildGraduated(def)
    graduateCache.set(def.id, { breaks, palette })

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
    const colorProp =
      geomRenderType === 'fill'
        ? 'fill-color'
        : geomRenderType === 'circle'
          ? 'circle-color'
          : 'line-color'
    ctx.map.setPaintProperty(mainLayerId, colorProp, hoverColorExpr(colorExpr, '#6366f1') as any)

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
    return { geomRenderType: 'fill', mainLayerId: renderLayerId(def.id, 'graduated-fill') }
  }
  const t = def.data.geometryType
  if (t === 'Polygon' || t === 'MultiPolygon') {
    return { geomRenderType: 'fill', mainLayerId: renderLayerId(def.id, 'graduated-fill') }
  }
  if (t === 'LineString' || t === 'MultiLineString') {
    return { geomRenderType: 'line', mainLayerId: renderLayerId(def.id, 'graduated-line') }
  }
  return { geomRenderType: 'circle', mainLayerId: renderLayerId(def.id, 'graduated-circle') }
}

/**
 * 构造 MapLibre 的 `step` 表达式：按数值区间映射到颜色。
 *
 * 表达式格式：['step', ['to-number', ['get', field]], color0, b0, color1, b1, ..., colorN]
 *
 * @param def - 图层定义
 * @returns 包含颜色表达式、断点数组和色带的对象
 */
function buildGraduated(def: MapLayerDefinition): {
  colorExpr: any
  breaks: number[]
  palette: string[]
} {
  const cfg = def.style.graduated
  if (!cfg) {
    // 没配置 — 退化成单色
    return { colorExpr: def.style.color, breaks: [], palette: [def.style.color] }
  }
  const classes =
    cfg.method === 'manual' && cfg.breaks ? cfg.breaks.length + 1 : cfg.classes ?? 5
  const palette =
    cfg.palette && cfg.palette.length === classes
      ? cfg.palette
      : defaultPalette(classes)

  let breaks: number[]
  if (cfg.method === 'manual' && cfg.breaks) {
    breaks = [...cfg.breaks]
  } else {
    const values = sampleNumericField(def, cfg.field)
    if (values.length === 0) {
      return { colorExpr: def.style.color, breaks: [], palette }
    }
    values.sort((a, b) => a - b)
    breaks = cfg.method === 'equal-interval'
      ? equalIntervalBreaks(values, classes)
      : quantileBreaks(values, classes)
  }

  // 组装 step expression
  const expr: any[] = ['step', ['to-number', ['get', cfg.field], -1e15]]
  expr.push(palette[0])
  for (let i = 0; i < breaks.length; i++) {
    expr.push(breaks[i])
    expr.push(palette[Math.min(i + 1, palette.length - 1)])
  }
  return { colorExpr: expr, breaks, palette }
}

/**
 * 生成默认色带。
 * 当请求的颜色数量在预定义范围内时，直接返回预定义色带；
 * 否则基于 7 色调色板进行线性采样。
 * @param n - 需要的颜色数量
 * @returns 颜色数组
 */
function defaultPalette(n: number): string[] {
  const known = DEFAULT_PALETTES[n]
  if (known) return known
  // n < 3 或 n > 7：做简单线性采样
  const base = DEFAULT_PALETTES[7]
  const out: string[] = []
  for (let i = 0; i < n; i++) {
    const idx = Math.round((i / (n - 1 || 1)) * (base.length - 1))
    out.push(base[idx])
  }
  return out
}

/**
 * 从图层数据中提取指定字段的数值数组。
 * @param def - 图层定义
 * @param field - 字段名
 * @returns 数值数组
 */
function sampleNumericField(def: MapLayerDefinition, field: string): number[] {
  if (def.data.kind !== 'vector') return []
  const fc = resolveVectorGeoJSON(def.data) as GeoJSONFeatureCollection
  const out: number[] = []
  for (const f of fc.features) {
    const v = (f.properties ?? {})[field]
    const n = typeof v === 'number' ? v : Number(v)
    if (Number.isFinite(n)) out.push(n)
  }
  return out
}

/**
 * 计算分位数断点。
 * @param sorted - 已排序的数值数组
 * @param classes - 分类数量
 * @returns 断点数组
 */
function quantileBreaks(sorted: number[], classes: number): number[] {
  const breaks: number[] = []
  for (let i = 1; i < classes; i++) {
    const idx = Math.floor((i / classes) * sorted.length)
    breaks.push(sorted[Math.min(idx, sorted.length - 1)])
  }
  return stabilizeBreaks(sorted, breaks, classes)
}

/**
 * 计算等距断点。
 * @param sorted - 已排序的数值数组
 * @param classes - 分类数量
 * @returns 断点数组
 */
function equalIntervalBreaks(sorted: number[], classes: number): number[] {
  const min = sorted[0]
  const max = sorted[sorted.length - 1]
  const step = (max - min) / classes
  const breaks: number[] = []
  for (let i = 1; i < classes; i++) {
    breaks.push(min + step * i)
  }
  return stabilizeBreaks(sorted, breaks, classes)
}

/**
 * 数组去重（适用于已排序的数组）。
 * @param arr - 数值数组
 * @returns 去重后的数值数组
 */
function dedupe(arr: number[]): number[] {
  const out: number[] = []
  for (const v of arr) {
    if (out.length === 0 || v > out[out.length - 1]) out.push(v)
  }
  return out
}

function stabilizeBreaks(sorted: number[], breaks: number[], classes: number): number[] {
  const target = Math.max(0, classes - 1)
  const deduped = dedupe(breaks)
  if (deduped.length === target) return deduped
  const unique = dedupe(sorted)
  if (unique.length <= 1) return []
  const effectiveClasses = Math.min(classes, unique.length)
  const fallback: number[] = []
  for (let i = 1; i < effectiveClasses; i++) {
    const pos = (i / effectiveClasses) * (unique.length - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, unique.length - 1)
    fallback.push((unique[lo] + unique[hi]) / 2)
  }
  return dedupe(fallback)
}
