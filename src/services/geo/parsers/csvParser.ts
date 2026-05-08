/**
 * CSV parser — handles .csv files with coordinate columns.
 * Detects latitude/longitude columns and converts rows to GeoJSON points.
 */
import type {
  ParsedVectorData,
  GeoJSONFeatureCollection,
  BBox,
  FieldDescriptor,
} from '../types'

/** Common column name patterns for coordinate detection */
const LAT_PATTERNS = [
  /^lat$/i, /^latitude$/i, /^lat_?d$/i, /^y$/i, /^纬度$/i,
  /^lat_wgs84$/i, /^point_y$/i, /^lat_dd$/i,
]

const LNG_PATTERNS = [
  /^lng$/i, /^lon$/i, /^long$/i, /^longitude$/i, /^lng_?d$/i,
  /^x$/i, /^经度$/i, /^lon_wgs84$/i, /^point_x$/i, /^lon_dd$/i,
]

interface CSVParseOptions {
  /** Override latitude column name */
  latColumn?: string
  /** Override longitude column name */
  lngColumn?: string
  /** CSV delimiter (auto-detected if not provided) */
  delimiter?: string
}

/**
 * Parse a CSV string into structured vector data (point features).
 */
export function parseCSV(raw: string, fileName: string, options: CSVParseOptions = {}): ParsedVectorData {
  const delimiter = options.delimiter || detectDelimiter(raw)
  const { headers, rows } = tokenize(raw, delimiter)

  if (headers.length === 0 || rows.length === 0) {
    throw new Error(`CSV file "${fileName}" is empty or has no data rows.`)
  }

  // Detect coordinate columns
  const latCol = options.latColumn || detectColumn(headers, LAT_PATTERNS)
  const lngCol = options.lngColumn || detectColumn(headers, LNG_PATTERNS)

  if (!latCol || !lngCol) {
    throw new Error(
      `CSV file "${fileName}" — could not detect coordinate columns. ` +
      `Found headers: [${headers.join(', ')}]. ` +
      `Expected columns like "lat/latitude/y" and "lng/longitude/x".`
    )
  }

  const latIdx = headers.indexOf(latCol)
  const lngIdx = headers.indexOf(lngCol)

  // Build GeoJSON features
  const features: any[] = []
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    const lat = parseFloat(row[latIdx])
    const lng = parseFloat(row[lngIdx])

    if (isNaN(lat) || isNaN(lng)) continue
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) continue

    // Build properties from all non-coordinate columns
    const properties: Record<string, any> = {}
    for (let j = 0; j < headers.length; j++) {
      if (j === latIdx || j === lngIdx) continue
      const val = row[j]
      // Try to parse numbers
      const num = Number(val)
      properties[headers[j]] = val === '' ? null : (!isNaN(num) && val.trim() !== '' ? num : val)
    }

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties,
      id: i,
    })

    if (lng < minX) minX = lng
    if (lat < minY) minY = lat
    if (lng > maxX) maxX = lng
    if (lat > maxY) maxY = lat
  }

  if (features.length === 0) {
    throw new Error(`CSV file "${fileName}" — no valid coordinate rows found.`)
  }

  const geojson: GeoJSONFeatureCollection = { type: 'FeatureCollection', features }
  const bbox: BBox = isFinite(minX) ? { minX, minY, maxX, maxY } : { minX: -180, minY: -90, maxX: 180, maxY: 90 }

  // Extract field descriptors (excluding coordinate columns)
  const fields = extractCSVFields(headers, rows, latIdx, lngIdx)

  return {
    kind: 'vector',
    geojson,
    geometryType: 'Point',
    featureCount: features.length,
    bbox,
    crs: 'EPSG:4326',
    fields,
  }
}

/**
 * Auto-detect CSV delimiter by counting occurrences in the first line.
 */
function detectDelimiter(raw: string): string {
  const firstLine = raw.split(/\r?\n/)[0] || ''
  const candidates = [',', '\t', ';', '|']
  let bestDelim = ','
  let bestCount = 0

  for (const d of candidates) {
    const count = firstLine.split(d).length - 1
    if (count > bestCount) {
      bestCount = count
      bestDelim = d
    }
  }

  return bestDelim
}

/**
 * Tokenize CSV into headers and rows.
 * Handles quoted fields with commas/newlines inside.
 */
function tokenize(raw: string, delimiter: string): { headers: string[]; rows: string[][] } {
  const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== '')
  if (lines.length === 0) return { headers: [], rows: [] }

  const parseLine = (line: string): string[] => {
    const result: string[] = []
    let current = ''
    let inQuotes = false

    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (inQuotes) {
        if (ch === '"' && line[i + 1] === '"') {
          current += '"'
          i++
        } else if (ch === '"') {
          inQuotes = false
        } else {
          current += ch
        }
      } else {
        if (ch === '"') {
          inQuotes = true
        } else if (ch === delimiter) {
          result.push(current.trim())
          current = ''
        } else {
          current += ch
        }
      }
    }
    result.push(current.trim())
    return result
  }

  const headers = parseLine(lines[0])
  const rows = lines.slice(1).map(parseLine)

  return { headers, rows }
}

/**
 * Detect a column name matching one of the given patterns.
 */
function detectColumn(headers: string[], patterns: RegExp[]): string | null {
  for (const pattern of patterns) {
    const match = headers.find((h) => pattern.test(h.trim()))
    if (match) return match
  }
  return null
}

/**
 * Extract field descriptors from CSV data.
 */
function extractCSVFields(
  headers: string[],
  rows: string[][],
  latIdx: number,
  lngIdx: number
): FieldDescriptor[] {
  const fields: FieldDescriptor[] = []
  const sampleLimit = 5
  const scanLimit = Math.min(rows.length, 100)

  for (let j = 0; j < headers.length; j++) {
    if (j === latIdx || j === lngIdx) continue

    let nullCount = 0
    let hasNumber = false
    let hasString = false
    const samples: any[] = []

    for (let i = 0; i < scanLimit; i++) {
      const val = rows[i]?.[j]
      if (val === undefined || val === '') {
        nullCount++
        continue
      }
      const num = Number(val)
      if (!isNaN(num) && val.trim() !== '') {
        hasNumber = true
        if (samples.length < sampleLimit) samples.push(num)
      } else {
        hasString = true
        if (samples.length < sampleLimit) samples.push(val)
      }
    }

    fields.push({
      name: headers[j],
      type: hasString ? 'string' : hasNumber ? 'number' : 'unknown',
      nullCount,
      sampleValues: samples,
    })
  }

  return fields
}
