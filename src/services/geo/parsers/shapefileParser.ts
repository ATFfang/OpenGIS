/**
 * Shapefile parser — handles .shp + .dbf + .shx + .prj bundles.
 * Uses the shapefile npm package for pure-JS parsing (no GDAL dependency).
 *
 * NOTE: Shapefile import requires the user to drop all companion files
 * (.shp, .dbf, .shx, .prj) together. We match them by base name.
 */
import type {
  ParsedVectorData,
  GeoJSONFeatureCollection,
  GeometryType,
  BBox,
  FieldDescriptor,
} from '../types'

/**
 * Parse Shapefile from a set of companion files.
 * Expects at minimum .shp and .dbf ArrayBuffers.
 */
export async function parseShapefile(
  files: Map<string, ArrayBuffer>,
  baseName: string
): Promise<ParsedVectorData> {
  // Dynamically import shapefile library
  const shp = await import('shapefile')

  const shpBuffer = files.get(`${baseName}.shp`)
  const dbfBuffer = files.get(`${baseName}.dbf`)

  if (!shpBuffer) {
    throw new Error(`Shapefile: missing .shp file for "${baseName}".`)
  }

  // shapefile.read() accepts ArrayBuffer for both .shp and .dbf
  const geojson: GeoJSONFeatureCollection = await shp.read(
    shpBuffer,
    dbfBuffer || undefined
  ) as GeoJSONFeatureCollection

  if (!geojson.features || geojson.features.length === 0) {
    throw new Error(`Shapefile "${baseName}" contains no features.`)
  }

  const geometryType = detectGeometryType(geojson)
  const bbox = computeBBox(geojson)
  const fields = extractFields(geojson)

  // Try to read CRS from .prj file
  let crs = 'EPSG:4326'
  const prjBuffer = files.get(`${baseName}.prj`)
  if (prjBuffer) {
    const prjText = new TextDecoder().decode(prjBuffer)
    crs = parsePrjToCRS(prjText)
  }

  return {
    kind: 'vector',
    geojson,
    geometryType,
    featureCount: geojson.features.length,
    bbox,
    crs,
    fields,
  }
}

/**
 * Group dropped files by shapefile base name.
 * Returns a map of baseName → Map<extension, ArrayBuffer>.
 */
export function groupShapefileComponents(
  files: { name: string; buffer: ArrayBuffer }[]
): Map<string, Map<string, ArrayBuffer>> {
  const groups = new Map<string, Map<string, ArrayBuffer>>()
  const shpExtensions = new Set(['.shp', '.dbf', '.shx', '.prj', '.cpg', '.sbn', '.sbx'])

  for (const file of files) {
    const dotIdx = file.name.lastIndexOf('.')
    if (dotIdx === -1) continue

    const ext = file.name.slice(dotIdx).toLowerCase()
    if (!shpExtensions.has(ext)) continue

    const base = file.name.slice(0, dotIdx).toLowerCase()
    if (!groups.has(base)) {
      groups.set(base, new Map())
    }
    groups.get(base)!.set(`${base}${ext}`, file.buffer)
  }

  return groups
}

// ─── Internal helpers (reused from geojsonParser logic) ───────────

function detectGeometryType(fc: GeoJSONFeatureCollection): GeometryType {
  const typeCounts = new Map<string, number>()
  for (const f of fc.features) {
    if (f.geometry?.type) {
      const t = f.geometry.type
      typeCounts.set(t, (typeCounts.get(t) || 0) + 1)
    }
  }
  let maxType = 'Point'
  let maxCount = 0
  for (const [type, count] of typeCounts) {
    if (count > maxCount) { maxType = type; maxCount = count }
  }
  return maxType as GeometryType
}

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

/**
 * Attempt to extract EPSG code from a .prj WKT string.
 * Falls back to "EPSG:4326" if unrecognized.
 */
function parsePrjToCRS(prjText: string): string {
  // Simple heuristic: look for AUTHORITY["EPSG","XXXX"]
  const match = prjText.match(/AUTHORITY\["EPSG",\s*"(\d+)"\]/i)
  if (match) return `EPSG:${match[1]}`

  // Check for common WKT names
  if (prjText.includes('WGS_1984') || prjText.includes('WGS 84')) return 'EPSG:4326'
  if (prjText.includes('NAD83')) return 'EPSG:4269'
  if (prjText.includes('NAD27')) return 'EPSG:4267'

  return 'EPSG:4326' // Default fallback
}
