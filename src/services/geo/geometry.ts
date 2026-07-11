import type { GeoJSONFeature, GeoJSONFeatureCollection, GeometryType } from './types'

const GEOMETRY_TIE_BREAK_PRIORITY: Record<string, number> = {
  MultiPolygon: 60,
  Polygon: 55,
  MultiLineString: 50,
  LineString: 45,
  MultiPoint: 40,
  Point: 35,
  GeometryCollection: 10,
}

export interface GeometryTypeSummary {
  geometryType: GeometryType
  counts: Partial<Record<GeometryType, number>>
  ignoredStructuralOsmNodes: number
}

export function summarizeGeometryTypes(fc: GeoJSONFeatureCollection): GeometryTypeSummary {
  const counts = new Map<GeometryType, number>()
  let ignoredStructuralOsmNodes = 0

  for (const feature of fc.features) {
    const type = feature.geometry?.type
    if (!type) continue
    if (isStructuralOsmNode(feature)) {
      ignoredStructuralOsmNodes += 1
      continue
    }
    counts.set(type, (counts.get(type) ?? 0) + 1)
  }

  if (counts.size === 0) {
    return {
      geometryType: 'Point',
      counts: {},
      ignoredStructuralOsmNodes,
    }
  }

  let best: GeometryType = 'Point'
  let bestCount = -1
  let bestPriority = -1
  for (const [type, count] of counts) {
    const priority = GEOMETRY_TIE_BREAK_PRIORITY[type] ?? 0
    if (count > bestCount || (count === bestCount && priority > bestPriority)) {
      best = type
      bestCount = count
      bestPriority = priority
    }
  }

  return {
    geometryType: best,
    counts: Object.fromEntries(counts) as Partial<Record<GeometryType, number>>,
    ignoredStructuralOsmNodes,
  }
}

export function detectGeometryType(fc: GeoJSONFeatureCollection): GeometryType {
  return summarizeGeometryTypes(fc).geometryType
}

function isStructuralOsmNode(feature: GeoJSONFeature): boolean {
  if (feature.geometry?.type !== 'Point') return false
  const props = feature.properties ?? {}
  if (props._osm_type !== 'node') return false
  const keys = Object.keys(props).filter((key) => props[key] !== undefined && props[key] !== null)
  return keys.length > 0 && keys.every((key) => key === '_osm_id' || key === '_osm_type')
}
