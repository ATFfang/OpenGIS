/**
 * GeoFileService — the single entry point for loading GIS files.
 *
 * Responsibilities:
 * 1. Detect file format from extension
 * 2. Read file content (text or binary)
 * 3. Dispatch to the correct parser
 * 4. Return a MapLayerDefinition ready for the store
 *
 * This service is stateless — it does NOT interact with stores or UI.
 */
import { v4 as uuidv4 } from 'uuid'
import type {
  DataSourceType,
  DataSourceMeta,
  MapLayerDefinition,
  ParsedData,
} from './types'
import {
  parseGeoJSON,
  parseCSV,
  parseShapefile,
  groupShapefileComponents,
  parseGeoTIFF,
} from './parsers'
import { getDefaultStyle } from './defaultStyles'

/** Supported file extensions and their source types */
const EXTENSION_MAP: Record<string, DataSourceType> = {
  '.geojson': 'geojson',
  '.json': 'geojson',
  '.csv': 'csv',
  '.tsv': 'csv',
  '.shp': 'shapefile',
  '.dbf': 'shapefile',
  '.shx': 'shapefile',
  '.prj': 'shapefile',
  '.cpg': 'shapefile',
  '.kml': 'kml',
  '.gpkg': 'geopackage',
  '.tif': 'geotiff',
  '.tiff': 'geotiff',
}

/**
 * Check if a file extension is supported.
 */
export function isSupportedExtension(fileName: string): boolean {
  const ext = getExtension(fileName)
  return ext in EXTENSION_MAP
}

/**
 * Get the list of supported file extensions for display.
 */
export function getSupportedExtensions(): string[] {
  return Object.keys(EXTENSION_MAP)
}

/**
 * Load a single file and return a MapLayerDefinition.
 * For text-based formats (GeoJSON, CSV, KML).
 */
export async function loadGeoFile(file: File): Promise<MapLayerDefinition> {
  const ext = getExtension(file.name)
  const sourceType = EXTENSION_MAP[ext]

  if (!sourceType) {
    throw new Error(`Unsupported file format: "${ext}". Supported: ${getSupportedExtensions().join(', ')}`)
  }

  const meta: DataSourceMeta = {
    fileName: file.name,
    extension: ext,
    sourceType,
    fileSize: file.size,
    mimeType: file.type || undefined,
  }

  let parsedData: ParsedData

  switch (sourceType) {
    case 'geojson': {
      const text = await file.text()
      parsedData = parseGeoJSON(text, file.name)
      break
    }
    case 'csv': {
      const text = await file.text()
      parsedData = parseCSV(text, file.name)
      break
    }
    case 'kml': {
      const text = await file.text()
      const { parseKML } = await import('./parsers/kmlParser')
      parsedData = await parseKML(text, file.name)
      break
    }
    case 'geotiff': {
      const buffer = await file.arrayBuffer()
      parsedData = await parseGeoTIFF(buffer, file.name)
      break
    }
    default:
      throw new Error(`Format "${sourceType}" requires special handling. Use loadGeoFiles() for Shapefiles.`)
  }

  return buildLayerDefinition(file.name, meta, parsedData)
}

/**
 * Load multiple files — handles Shapefile bundles and mixed file drops.
 * Returns an array of MapLayerDefinitions.
 */
export async function loadGeoFiles(files: File[]): Promise<MapLayerDefinition[]> {
  const results: MapLayerDefinition[] = []
  const shapefileFiles: { name: string; buffer: ArrayBuffer }[] = []
  const otherFiles: File[] = []

  // Separate shapefile components from other files
  for (const file of files) {
    const ext = getExtension(file.name)
    const sourceType = EXTENSION_MAP[ext]

    if (sourceType === 'shapefile') {
      const buffer = await file.arrayBuffer()
      shapefileFiles.push({ name: file.name.toLowerCase(), buffer })
    } else if (sourceType) {
      otherFiles.push(file)
    }
    // Silently skip unsupported files
  }

  // Process shapefile bundles
  if (shapefileFiles.length > 0) {
    const groups = groupShapefileComponents(shapefileFiles)

    for (const [baseName, componentFiles] of groups) {
      // Only process groups that have a .shp file
      if (!componentFiles.has(`${baseName}.shp`)) continue

      try {
        const parsedData = await parseShapefile(componentFiles, baseName)
        const meta: DataSourceMeta = {
          fileName: `${baseName}.shp`,
          extension: '.shp',
          sourceType: 'shapefile',
          fileSize: componentFiles.get(`${baseName}.shp`)!.byteLength,
        }
        results.push(buildLayerDefinition(baseName, meta, parsedData))
      } catch (err) {
        console.error(`Failed to parse shapefile "${baseName}":`, err)
      }
    }
  }

  // Process other files individually
  for (const file of otherFiles) {
    try {
      const layer = await loadGeoFile(file)
      results.push(layer)
    } catch (err) {
      console.error(`Failed to load file "${file.name}":`, err)
    }
  }

  return results
}

// ─── Internal helpers ─────────────────────────────────────────────

function getExtension(fileName: string): string {
  const dotIdx = fileName.lastIndexOf('.')
  return dotIdx !== -1 ? fileName.slice(dotIdx).toLowerCase() : ''
}

function buildLayerDefinition(
  displayName: string,
  meta: DataSourceMeta,
  data: ParsedData
): MapLayerDefinition {
  // Strip extension for display name
  const dotIdx = displayName.lastIndexOf('.')
  const name = dotIdx !== -1 ? displayName.slice(0, dotIdx) : displayName

  const style = data.kind === 'vector'
    ? getDefaultStyle(data.geometryType)
    : {
        renderType: 'raster' as const,
        color: '#ffffff',
        opacity: 1,
        strokeColor: '#ffffff',
        strokeWidth: 0,
      }

  return {
    id: uuidv4(),
    name,
    sourceType: meta.sourceType,
    visible: true,
    style,
    data,
    meta,
    addedAt: Date.now(),
  }
}
