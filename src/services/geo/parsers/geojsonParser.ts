/**
 * GeoJSON parser — handles .geojson / .json files.
 * Pure function: File → ParsedVectorData
 */
import type {
  ParsedVectorData,
  GeoJSONFeatureCollection,
  GeoJSONFeature,
  BBox,
  FieldDescriptor,
} from '../types'
import { detectGeometryType } from '../geometry'

/**
 * Parse a GeoJSON string into structured vector data.
 */
export function parseGeoJSON(raw: string, fileName: string): ParsedVectorData {
  const parsed = JSON.parse(raw)
  const fc = normalizeToFeatureCollection(parsed)

  if (fc.features.length === 0) {
    throw new Error(`GeoJSON file "${fileName}" contains no features.`)
  }

  const geometryType = detectGeometryType(fc)
  const bbox = computeBBox(fc)
  const fields = extractFields(fc)

  return {
    kind: 'vector',
    geojson: fc,
    geometryType,
    featureCount: fc.features.length,
    bbox,
    crs: 'EPSG:4326', // GeoJSON is always WGS84 per RFC 7946
    fields,
  }
}

/**
 * Normalize various GeoJSON structures into a FeatureCollection.
 */
function normalizeToFeatureCollection(data: any): GeoJSONFeatureCollection {
  if (data.type === 'FeatureCollection') {
    return data as GeoJSONFeatureCollection
  }

  if (data.type === 'Feature') {
    return {
      type: 'FeatureCollection',
      features: [data as GeoJSONFeature],
    }
  }

  // Bare geometry
  if (data.type && data.coordinates) {
    return {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: data,
          properties: {},
        },
      ],
    }
  }

  throw new Error('Invalid GeoJSON: unrecognized structure.')
}

/**
 * Compute the bounding box of all features.
 */
function computeBBox(fc: GeoJSONFeatureCollection): BBox {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  function processCoords(coords: any) {
    if (typeof coords[0] === 'number') {
      // [lng, lat]
      const [x, y] = coords
      if (x < minX) minX = x
      if (y < minY) minY = y
      if (x > maxX) maxX = x
      if (y > maxY) maxY = y
    } else if (Array.isArray(coords)) {
      for (const c of coords) {
        processCoords(c)
      }
    }
  }

  for (const feature of fc.features) {
    if (feature.geometry?.coordinates) {
      processCoords(feature.geometry.coordinates)
    }
  }

  // Fallback if no valid coordinates found
  if (!isFinite(minX)) {
    return { minX: -180, minY: -90, maxX: 180, maxY: 90 }
  }

  return { minX, minY, maxX, maxY }
}

/**
 * Extract field descriptors from feature properties.
 */
export function extractFields(fc: GeoJSONFeatureCollection): FieldDescriptor[] {
  const fieldMap = new Map<string, { types: Set<string>; nullCount: number; samples: any[] }>()

  const sampleLimit = 5
  const scanLimit = Math.min(fc.features.length, 100) // Scan first 100 features

  for (let i = 0; i < scanLimit; i++) {
    const props = fc.features[i].properties || {}
    for (const [key, value] of Object.entries(props)) {
      if (!fieldMap.has(key)) {
        fieldMap.set(key, { types: new Set(), nullCount: 0, samples: [] })
      }
      const field = fieldMap.get(key)!

      if (value === null || value === undefined) {
        field.nullCount++
      } else {
        field.types.add(typeof value)
        if (field.samples.length < sampleLimit) {
          field.samples.push(value)
        }
      }
    }
  }

  return Array.from(fieldMap.entries()).map(([name, info]) => ({
    name,
    type: inferFieldType(info.types),
    nullCount: info.nullCount,
    sampleValues: info.samples,
  }))
}

function inferFieldType(types: Set<string>): FieldDescriptor['type'] {
  if (types.has('number')) return 'number'
  if (types.has('boolean')) return 'boolean'
  if (types.has('string')) return 'string'
  return 'unknown'
}
