import type {
  GeoJSONFeatureCollection,
  LayerFilterSpec,
  MapLayerDefinition,
  NumericVisualVariable,
  SortVisualVariable,
} from '@/services/geo'
import { resolveVectorGeoJSON } from '@/services/geo'

export function compileLayerFilter(filter: LayerFilterSpec | undefined): unknown[] | undefined {
  const attribute = filter?.attribute?.filter((item) => item.field) ?? []
  if (attribute.length === 0) return undefined

  const clauses: unknown[] = []
  for (const item of attribute) {
    const fieldValue = ['get', item.field]
    const stringValue = ['to-string', fieldValue]
    switch (item.op) {
      case '=':
        clauses.push(['==', stringValue, String(item.value)])
        break
      case '!=':
        clauses.push(['!=', stringValue, String(item.value)])
        break
      case '>':
      case '<':
      case '>=':
      case '<=':
        clauses.push([item.op, ['to-number', fieldValue], Number(item.value)])
        break
      case 'contains':
        clauses.push(['in', String(item.value ?? ''), stringValue])
        break
      case 'in': {
        const values = Array.isArray(item.value) ? item.value.map(String) : [String(item.value)]
        clauses.push(['in', stringValue, ['literal', values]])
        break
      }
    }
  }

  if (clauses.length === 0) return undefined
  return clauses.length === 1 ? clauses[0] as unknown[] : ['all', ...clauses]
}

export function compileNumericVisualVariable(
  def: MapLayerDefinition,
  variable: NumericVisualVariable | undefined,
  fallback: number,
  options: { defaultRange?: [number, number]; clampRange?: [number, number] } = {},
): number | unknown[] {
  if (!variable?.field) return fallback

  const classes = resolveClassCount(variable)
  const values = resolveOutputValues(variable, classes, options.defaultRange ?? [fallback, fallback])
  const breaks = resolveBreaks(def, variable, classes)
  if (breaks.length === 0 || values.length === 0) return fallback

  const normalizedValues = options.clampRange
    ? values.map((value) => clamp(value, options.clampRange![0], options.clampRange![1]))
    : values
  const expr: unknown[] = [
    'step',
    ['to-number', ['get', variable.field], -1e15],
    normalizedValues[0],
  ]
  for (let i = 0; i < breaks.length; i++) {
    expr.push(breaks[i], normalizedValues[Math.min(i + 1, normalizedValues.length - 1)])
  }
  return expr
}

export function hoverColorExpr(base: unknown, hoverColor: string): unknown[] {
  return ['case', ['boolean', ['feature-state', 'hover'], false], hoverColor, base]
}

export function hoverNumberExpr(base: number | unknown[], hoverDelta: number): unknown[] {
  return [
    'case',
    ['boolean', ['feature-state', 'hover'], false],
    ['+', base, hoverDelta],
    base,
  ]
}

export function hoverOpacityExpr(base: number | unknown[], fallback: number, hoverDelta = 0.25): unknown[] {
  return [
    'case',
    ['boolean', ['feature-state', 'hover'], false],
    Math.min(fallback + hoverDelta, 0.85),
    base,
  ]
}

export function compileSortKey(variable: SortVisualVariable | undefined): unknown[] | undefined {
  if (!variable?.field) return undefined
  const valueExpr: unknown[] = ['to-number', ['get', variable.field], 0]
  return variable.order === 'ascending' ? ['*', valueExpr, -1] : valueExpr
}

function resolveClassCount(variable: NumericVisualVariable): number {
  if (variable.method === 'manual' && variable.breaks?.length) return variable.breaks.length + 1
  if (variable.values?.length && variable.values.length > 1) return variable.values.length
  return Math.max(2, Math.min(12, Math.round(variable.classes ?? 5)))
}

function resolveOutputValues(
  variable: NumericVisualVariable,
  classes: number,
  fallbackRange: [number, number],
): number[] {
  const explicit = variable.values
    ?.map((value) => Number(value))
    .filter((value) => Number.isFinite(value))
  if (explicit?.length) {
    if (explicit.length >= classes) return explicit.slice(0, classes)
    const last = explicit[explicit.length - 1]
    return Array.from({ length: classes }, (_, i) => explicit[i] ?? last)
  }

  const range = variable.range ?? fallbackRange
  const start = Number(range[0])
  const end = Number(range[1])
  if (!Number.isFinite(start) || !Number.isFinite(end)) return [fallbackRange[0]]
  if (classes <= 1) return [start]
  return Array.from({ length: classes }, (_, i) => start + ((end - start) * i) / (classes - 1))
}

function resolveBreaks(
  def: MapLayerDefinition,
  variable: NumericVisualVariable,
  classes: number,
): number[] {
  if (variable.method === 'manual' && variable.breaks?.length) {
    return dedupeSorted(variable.breaks.map(Number).filter(Number.isFinite).sort((a, b) => a - b))
  }
  const values = sampleNumericField(def, variable.field)
  if (values.length === 0) return []
  values.sort((a, b) => a - b)
  const method = variable.method ?? 'quantile'
  return method === 'equal-interval'
    ? equalIntervalBreaks(values, classes)
    : quantileBreaks(values, classes)
}

function sampleNumericField(def: MapLayerDefinition, field: string): number[] {
  if (def.data.kind !== 'vector') return []
  const fc = resolveVectorGeoJSON(def.data) as GeoJSONFeatureCollection
  const out: number[] = []
  for (const feature of fc.features) {
    const raw = (feature.properties ?? {})[field]
    const value = typeof raw === 'number' ? raw : Number(raw)
    if (Number.isFinite(value)) out.push(value)
  }
  return out
}

function quantileBreaks(sorted: number[], classes: number): number[] {
  const breaks: number[] = []
  for (let i = 1; i < classes; i++) {
    const idx = Math.floor((i / classes) * sorted.length)
    breaks.push(sorted[Math.min(idx, sorted.length - 1)])
  }
  return stabilizeBreaks(sorted, breaks, classes)
}

function equalIntervalBreaks(sorted: number[], classes: number): number[] {
  const min = sorted[0]
  const max = sorted[sorted.length - 1]
  const step = (max - min) / classes
  const breaks: number[] = []
  for (let i = 1; i < classes; i++) breaks.push(min + step * i)
  return stabilizeBreaks(sorted, breaks, classes)
}

function dedupeSorted(values: number[]): number[] {
  const out: number[] = []
  for (const value of values) {
    if (out.length === 0 || value > out[out.length - 1]) out.push(value)
  }
  return out
}

function stabilizeBreaks(sorted: number[], breaks: number[], classes: number): number[] {
  const target = Math.max(0, classes - 1)
  const deduped = dedupeSorted(breaks)
  if (deduped.length === target) return deduped
  const unique = dedupeSorted(sorted)
  if (unique.length <= 1) return []
  const effectiveClasses = Math.min(classes, unique.length)
  const fallback: number[] = []
  for (let i = 1; i < effectiveClasses; i++) {
    const pos = (i / effectiveClasses) * (unique.length - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, unique.length - 1)
    fallback.push((unique[lo] + unique[hi]) / 2)
  }
  return dedupeSorted(fallback)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}
