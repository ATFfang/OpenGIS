/**
 * KML parser — handles .kml files.
 * Converts KML to GeoJSON using @tmcw/togeojson.
 */
import type {
  ParsedVectorData,
  GeoJSONFeatureCollection,
  BBox,
  FieldDescriptor,
} from '../types'
import { detectGeometryType } from '../geometry'

/**
 * Parse a KML string into structured vector data.
 */
export async function parseKML(raw: string, fileName: string): Promise<ParsedVectorData> {
  const toGeoJSON = await import('@tmcw/togeojson')

  const parser = new DOMParser()
  const doc = parser.parseFromString(raw, 'text/xml')

  // Check for parse errors
  const parseError = doc.querySelector('parsererror')
  if (parseError) {
    throw new Error(`KML file "${fileName}" contains invalid XML.`)
  }

  const geojson = toGeoJSON.kml(doc) as GeoJSONFeatureCollection

  if (!geojson.features || geojson.features.length === 0) {
    throw new Error(`KML file "${fileName}" contains no features.`)
  }

  const geometryType = detectGeometryType(geojson)
  const bbox = computeBBox(geojson)
  const fields = extractFields(geojson)

  return {
    kind: 'vector',
    geojson,
    geometryType,
    featureCount: geojson.features.length,
    bbox,
    crs: 'EPSG:4326',
    fields,
  }
}

// ─── Shared helpers ───────────────────────────────────────────────

function computeBBox(fc: GeoJSONFeatureCollection): BBox {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  function walk(coords: any) {
    if (typeof coords[0] === 'number') {
      const [x, y] = coords
      if (x < minX) minX = x; if (y < minY) minY = y
      if (x > maxX) maxX = x; if (y > maxY) maxY = y
    } else if (Array.isArray(coords)) {
      for (const c of coords) walk(c)
    }
  }
  for (const f of fc.features) {
    if (f.geometry?.coordinates) walk(f.geometry.coordinates)
  }
  return isFinite(minX) ? { minX, minY, maxX, maxY } : { minX: -180, minY: -90, maxX: 180, maxY: 90 }
}

function extractFields(fc: GeoJSONFeatureCollection): FieldDescriptor[] {
  const fieldMap = new Map<string, { types: Set<string>; nullCount: number; samples: any[] }>()
  const scanLimit = Math.min(fc.features.length, 100)
  for (let i = 0; i < scanLimit; i++) {
    const props = fc.features[i].properties || {}
    for (const [key, value] of Object.entries(props)) {
      if (!fieldMap.has(key)) fieldMap.set(key, { types: new Set(), nullCount: 0, samples: [] })
      const field = fieldMap.get(key)!
      if (value === null || value === undefined) {
        field.nullCount++
      } else {
        field.types.add(typeof value)
        if (field.samples.length < 5) field.samples.push(value)
      }
    }
  }
  return Array.from(fieldMap.entries()).map(([name, info]) => ({
    name,
    type: info.types.has('number') ? 'number' : info.types.has('string') ? 'string' : 'unknown',
    nullCount: info.nullCount,
    sampleValues: info.samples,
  }))
}
