import type { FileNode } from '@/stores/assetStore'
import type { MapLayerDefinition, ParsedRasterData, ParsedVectorData } from '@/services/geo'
import { parseGeoJSON, parseGeoTIFF, parseKML, parseShapefile } from '@/services/geo/parsers'
import type { PivotData, PivotRasterStats, PivotTable, PivotTarget } from './types'

const MAX_TEXT_PREVIEW_BYTES = 25 * 1024 * 1024
const MAX_TIFF_PREVIEW_BYTES = 180 * 1024 * 1024
const MAX_DISPLAY_ROWS = 1000
const MAX_COLUMNS = 80

const PIVOT_FILE_EXTS = new Set([
  '.csv',
  '.tsv',
  '.json',
  '.geojson',
  '.kml',
  '.shp',
  '.tif',
  '.tiff',
  '.gpkg',
])

export function canPivotFile(node: FileNode): boolean {
  if (node.type !== 'file') return false
  return PIVOT_FILE_EXTS.has(node.extension.toLowerCase())
}

export function canPivotLayer(layer: MapLayerDefinition): boolean {
  return layer.data.kind === 'vector' || layer.data.kind === 'raster'
}

export async function loadPivotData(target: PivotTarget, layers: MapLayerDefinition[]): Promise<PivotData> {
  if (target.kind === 'layer') {
    const layer = layers.find((item) => item.id === target.layerId)
    if (!layer) throw new Error(`图层不存在或尚未恢复：${target.name}`)
    return pivotDataFromLayer(layer, target)
  }
  return loadPivotDataFromFile(target)
}

function pivotDataFromLayer(layer: MapLayerDefinition, target: PivotTarget): PivotData {
  if (layer.data.kind === 'raster') {
    return {
      target,
      dataKind: 'raster',
      title: layer.name,
      raster: rasterStatsFromLayer(layer),
      layer,
    }
  }
  return {
    target,
    dataKind: 'vector',
    title: layer.name,
    table: tableFromVector(layer.data, layer.name),
    layer,
  }
}

async function loadPivotDataFromFile(target: Extract<PivotTarget, { kind: 'file' }>): Promise<PivotData> {
  const ext = target.extension.toLowerCase()
  if (!window.electronAPI) {
    throw new Error('Electron 文件 API 不可用，无法读取本地数据。')
  }

  if (ext === '.gpkg') {
    return {
      target,
      dataKind: 'vector',
      title: target.name,
      warning: 'GPKG 前端预览已跳过；Agent 透视会直接基于文件路径读取。',
    }
  }

  if (ext === '.tif' || ext === '.tiff') {
    if (target.size > MAX_TIFF_PREVIEW_BYTES) {
      return {
        target,
        dataKind: 'raster',
        title: target.name,
        warning: `栅格文件过大（${formatFileSize(target.size)}），前端预览已跳过；Agent 透视会直接基于文件路径读取。`,
      }
    }
    const result = await window.electronAPI.readFileAsBuffer(target.path)
    if (!result.success || !result.buffer) throw new Error(result.error || '读取栅格文件失败。')
    const raster = await parseGeoTIFF(result.buffer, target.name)
    return {
      target,
      dataKind: 'raster',
      title: target.name,
      raster: rasterStatsFromRasterData(raster),
    }
  }

  if (ext === '.shp') {
    const vector = await readShapefileTarget(target)
    return {
      target,
      dataKind: 'vector',
      title: target.name,
      table: tableFromVector(vector, target.name),
    }
  }

  if (target.size > MAX_TEXT_PREVIEW_BYTES) {
    return {
      target,
      dataKind: ext === '.geojson' || ext === '.json' || ext === '.kml' ? 'vector' : 'table',
      title: target.name,
      warning: `文件过大（${formatFileSize(target.size)}），前端预览已跳过；Agent 透视会直接基于文件路径读取。`,
    }
  }

  const result = await window.electronAPI.readFile(target.path)
  if (!result.success || result.content === undefined) throw new Error(result.error || '读取文件失败。')
  const content = result.content

  if (ext === '.csv' || ext === '.tsv') {
    return {
      target,
      dataKind: 'table',
      title: target.name,
      table: tableFromDelimitedText(content, target.name, ext === '.tsv' ? '\t' : undefined),
    }
  }

  if (ext === '.geojson') {
    const vector = parseGeoJSON(content, target.name)
    return {
      target,
      dataKind: 'vector',
      title: target.name,
      table: tableFromVector(vector, target.name),
    }
  }

  if (ext === '.json') {
    try {
      const vector = parseGeoJSON(content, target.name)
      return {
        target,
        dataKind: 'vector',
        title: target.name,
        table: tableFromVector(vector, target.name),
      }
    } catch {
      return {
        target,
        dataKind: 'table',
        title: target.name,
        table: tableFromJson(content, target.name),
        warning: '该 JSON 不是 GeoJSON，已按普通记录表解析。',
      }
    }
  }

  if (ext === '.kml') {
    const vector = await parseKML(content, target.name)
    return {
      target,
      dataKind: 'vector',
      title: target.name,
      table: tableFromVector(vector, target.name),
    }
  }

  throw new Error(`暂不支持该格式的数据透视：${ext}`)
}

async function readShapefileTarget(target: PivotTarget & { kind: 'file' }): Promise<ParsedVectorData> {
  const api = window.electronAPI
  if (!api?.readFileAsBuffer) throw new Error('Electron 二进制读取 API 不可用。')

  const dot = target.path.lastIndexOf('.')
  const basePath = dot >= 0 ? target.path.slice(0, dot) : target.path
  const nameDot = target.name.lastIndexOf('.')
  const baseName = (nameDot >= 0 ? target.name.slice(0, nameDot) : target.name).toLowerCase()
  const files = new Map<string, ArrayBuffer>()

  for (const ext of ['.shp', '.dbf', '.shx', '.prj', '.cpg']) {
    const result = await readFirstExistingBuffer(api, [`${basePath}${ext}`, `${basePath}${ext.toUpperCase()}`])
    if (result.success && result.buffer) {
      files.set(`${baseName}${ext}`, result.buffer)
    }
  }

  if (!files.has(`${baseName}.shp`)) {
    throw new Error('Shapefile 缺少 .shp 主文件。')
  }

  return parseShapefile(files, baseName)
}

async function readFirstExistingBuffer(
  api: NonNullable<Window['electronAPI']>,
  paths: string[],
): Promise<{ success: boolean; buffer?: ArrayBuffer; error?: string }> {
  let last: { success: boolean; buffer?: ArrayBuffer; error?: string } = { success: false }
  for (const path of paths) {
    last = await api.readFileAsBuffer(path)
    if (last.success && last.buffer) return last
  }
  return last
}

function tableFromVector(vector: ParsedVectorData, name: string): PivotTable {
  const fieldSet = new Set<string>()
  for (const field of vector.fields ?? []) fieldSet.add(field.name)
  for (const feature of vector.geojson.features.slice(0, MAX_DISPLAY_ROWS)) {
    for (const key of Object.keys(feature.properties ?? {})) fieldSet.add(key)
  }
  const columns = [...fieldSet].slice(0, MAX_COLUMNS)
  const rows = vector.geojson.features.slice(0, MAX_DISPLAY_ROWS).map((feature, index) => {
    const row: Record<string, unknown> = {}
    for (const column of columns) row[column] = feature.properties?.[column] ?? null
    if (!columns.includes('__geometry')) row.__geometry = feature.geometry?.type ?? null
    if (!columns.includes('__fid')) row.__fid = feature.id ?? index
    return row
  })
  const finalColumns = ['__fid', '__geometry', ...columns.filter((c) => c !== '__fid' && c !== '__geometry')]
  return {
    columns: finalColumns,
    rows,
    totalRows: vector.featureCount,
    sampled: vector.featureCount > rows.length || fieldSet.size > columns.length,
    sourceLabel: name,
  }
}

function tableFromDelimitedText(raw: string, name: string, forcedDelimiter?: string): PivotTable {
  const delimiter = forcedDelimiter ?? detectDelimiter(firstLine(raw))
  const parsedRows = parseDelimitedRows(raw, delimiter)
  if (parsedRows.length === 0) {
    return { columns: [], rows: [], totalRows: 0, sampled: false, sourceLabel: name }
  }
  const headers = dedupeHeaders(parsedRows[0]).slice(0, MAX_COLUMNS)
  const body = parsedRows.slice(1)
  const rows = body.slice(0, MAX_DISPLAY_ROWS).map((cells) => {
    const row: Record<string, unknown> = {}
    headers.forEach((header, index) => {
      row[header] = coerceCell(cells[index] ?? '')
    })
    return row
  })
  return {
    columns: headers,
    rows,
    totalRows: body.length,
    sampled: body.length > rows.length || parsedRows[0].length > headers.length,
    sourceLabel: name,
  }
}

function tableFromJson(raw: string, name: string): PivotTable {
  const value = JSON.parse(raw)
  const records = findRecordArray(value)
  if (!records) {
    throw new Error('JSON 不是 GeoJSON，也没有找到可表格化的对象数组。')
  }
  const fieldSet = new Set<string>()
  for (const record of records.slice(0, MAX_DISPLAY_ROWS)) {
    for (const key of Object.keys(record)) fieldSet.add(key)
  }
  const columns = [...fieldSet].slice(0, MAX_COLUMNS)
  const rows = records.slice(0, MAX_DISPLAY_ROWS).map((record) => {
    const row: Record<string, unknown> = {}
    for (const column of columns) row[column] = normalizeJsonCell(record[column])
    return row
  })
  return {
    columns,
    rows,
    totalRows: records.length,
    sampled: records.length > rows.length || fieldSet.size > columns.length,
    sourceLabel: name,
  }
}

function findRecordArray(value: unknown): Array<Record<string, unknown>> | null {
  if (Array.isArray(value) && value.every((item) => item && typeof item === 'object' && !Array.isArray(item))) {
    return value as Array<Record<string, unknown>>
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const obj = value as Record<string, unknown>
  for (const candidate of Object.values(obj)) {
    const found = findRecordArray(candidate)
    if (found) return found
  }
  return null
}

function normalizeJsonCell(value: unknown): unknown {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
  return JSON.stringify(value)
}

function rasterStatsFromLayer(layer: MapLayerDefinition): PivotRasterStats {
  if (layer.data.kind !== 'raster') throw new Error('Layer is not raster.')
  return rasterStatsFromRasterData(layer.data)
}

function rasterStatsFromRasterData(raster: ParsedRasterData): PivotRasterStats {
  const totalPixels = raster.width * raster.height
  const rows: Array<Record<string, unknown>> = (raster.bandStats ?? []).map((stats, index) => ({
    band: index + 1,
    min: stats.min,
    max: stats.max,
    mean: null,
    valid_pixels: totalPixels,
    nodata_pixels: raster.noDataValue == null ? 0 : null,
    nodata_value: raster.noDataValue ?? '',
  }))
  if (rows.length === 0) {
    rows.push({
      band: 1,
      min: null,
      max: null,
      mean: null,
      valid_pixels: totalPixels,
      nodata_pixels: null,
      nodata_value: raster.noDataValue ?? '',
    })
  }
  return {
    rows,
    meta: [
      { label: '尺寸', value: `${raster.width.toLocaleString()} x ${raster.height.toLocaleString()}` },
      { label: '波段', value: String(raster.bandCount) },
      { label: 'CRS', value: raster.crs },
      { label: 'BBox', value: `${round(raster.bbox.minX)}, ${round(raster.bbox.minY)}, ${round(raster.bbox.maxX)}, ${round(raster.bbox.maxY)}` },
      { label: 'NoData', value: raster.noDataValue == null ? '未声明' : String(raster.noDataValue) },
    ],
  }
}

function firstLine(text: string): string {
  const idx = text.search(/\r?\n/)
  return idx >= 0 ? text.slice(0, idx) : text
}

function detectDelimiter(line: string): string {
  const candidates = [',', '\t', ';', '|']
  let best = ','
  let bestCount = -1
  for (const candidate of candidates) {
    const count = countUnquoted(line, candidate)
    if (count > bestCount) {
      best = candidate
      bestCount = count
    }
  }
  return best
}

function countUnquoted(line: string, delimiter: string): number {
  let count = 0
  let inQuote = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') inQuote = !inQuote
    else if (!inQuote && ch === delimiter) count++
  }
  return count
}

function parseDelimitedRows(raw: string, delimiter: string): string[][] {
  const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuote = false
  let i = 0

  while (i < text.length) {
    const ch = text[i]
    if (inQuote) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i += 2
          continue
        }
        inQuote = false
      } else {
        field += ch
      }
      i++
      continue
    }
    if (ch === '"') {
      inQuote = true
    } else if (ch === delimiter) {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
      if (ch === '\r' && text[i + 1] === '\n') i++
    } else {
      field += ch
    }
    i++
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field)
    rows.push(row)
  }
  while (rows.length > 0 && rows[rows.length - 1].every((cell) => cell === '')) rows.pop()
  return rows
}

function dedupeHeaders(headers: string[]): string[] {
  const seen = new Map<string, number>()
  return headers.map((raw, index) => {
    const base = raw.trim() || `col_${index + 1}`
    const count = seen.get(base) ?? 0
    seen.set(base, count + 1)
    return count === 0 ? base : `${base}_${count + 1}`
  })
}

function coerceCell(value: string): unknown {
  const text = value.trim()
  if (!text) return null
  if (text.toLowerCase() === 'true') return true
  if (text.toLowerCase() === 'false') return false
  const n = Number(text)
  return Number.isFinite(n) && /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(text) ? n : value
}

function round(value: number): string {
  return Number(value.toFixed(6)).toString()
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
