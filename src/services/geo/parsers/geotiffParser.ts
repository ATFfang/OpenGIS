/**
 * GeoTIFF parser — handles .tif / .tiff files.
 *
 * 流水线：ArrayBuffer → geotiff.fromArrayBuffer → GeoTIFFImage →
 *   读第一/二/三 band → canvas 渲染 → toDataURL('image/png') → ImageSource
 *
 * 关键选择：
 * - **直接在 renderer 里解**，不经 Python。纯 JS 的 `geotiff` 包足以满足需求，
 *   TS 自带的栅格处理能力让此路径无需依赖 Python sidecar。
 * - **渲染成 PNG + MapLibre ImageSource**，不用瓦片切片。原因：本地 TIFF
 *   基本是 < 5000×5000 的小栅格（DEM、模型输出、NDVI 等），图片源够用；
 *   瓦片服务器没必要。> 5000 的大 tiff 走 COG tile 通道（未实装，留口）。
 * - **坐标系**：只支持 EPSG:4326 / EPSG:3857。其它投影需要 proj4 外挂，
 *   本轮不做，抛提示让用户预先 warp 到 WGS84。
 *
 * 渲染模式：
 * - 单 band（灰度 or DEM）→ 线性拉伸到 0-255，viridis 调色；
 * - 3/4 band（真彩 RGB/RGBA）→ 原色直出（按前三个 band 当 R/G/B）；
 * - nodata → 透明像素。
 */
import { fromArrayBuffer, type GeoTIFFImage } from 'geotiff'
import type { ParsedRasterData, BBox, RasterColorRampName, RasterColorStop, RasterStyleSettings } from '../types'

export interface GeoTIFFParseOptions {
  rasterStyle?: RasterStyleSettings
  sourcePath?: string
  sourceBufferId?: string
}

/**
 * 解析一个 GeoTIFF buffer，返回 ParsedRasterData。
 *
 * @param buffer - 整个 tif 文件的 ArrayBuffer
 * @param fileName - 仅用于错误信息里引用
 */
export async function parseGeoTIFF(
  buffer: ArrayBuffer,
  fileName: string,
  options: GeoTIFFParseOptions = {},
): Promise<ParsedRasterData> {
  return renderGeoTIFFFromBuffer(buffer, fileName, options)
}

export async function renderGeoTIFFFromBuffer(
  buffer: ArrayBuffer,
  fileName: string,
  options: GeoTIFFParseOptions = {},
): Promise<ParsedRasterData> {
  const tiff = await fromArrayBuffer(buffer)
  const image = (await tiff.getImage()) as GeoTIFFImage

  const width = image.getWidth()
  const height = image.getHeight()
  const bandCount = image.getSamplesPerPixel()
  const rasterStyle = normalizeRasterStyle(options.rasterStyle, bandCount)

  // ── 读地理信息 ─────────────────────────────────────────
  const sourceBbox = readBBox(image, fileName)
  const crs = readCRS(image)
  const bbox = rasterBBoxForMap(sourceBbox, crs, fileName)
  const noDataValue = readNoData(image)

  // ── Downsample oversized rasters ──────────────────────────
  // Browser Canvas has a hard pixel limit (~16M on most engines, ~268M on
  // Chrome desktop). Beyond that, getContext('2d') silently returns a
  // black/empty canvas. We cap at 4096×4096 (16M) to be safe and fast.
  const MAX_DIM = 4096
  let renderWidth = width
  let renderHeight = height
  let needsDownsample = false
  if (width > MAX_DIM || height > MAX_DIM) {
    const scaleFactor = Math.min(MAX_DIM / width, MAX_DIM / height)
    renderWidth = Math.round(width * scaleFactor)
    renderHeight = Math.round(height * scaleFactor)
    needsDownsample = true
    console.info(
      `[parseGeoTIFF] "${fileName}" is ${width}×${height} — downsampling to ${renderWidth}×${renderHeight} for display`,
    )
  }

  // ── 读像素 ─────────────────────────────────────────────
  // When downsampling, read at reduced resolution directly from geotiff.js
  // to avoid allocating the full-res array (saves GBs of RAM for large DEMs).
  const readOpts = needsDownsample
    ? { interleave: false as const, width: renderWidth, height: renderHeight }
    : { interleave: false as const }
  const rasters = (await image.readRasters(readOpts)) as Array<
    Uint8Array | Uint16Array | Int16Array | Uint32Array | Int32Array | Float32Array | Float64Array
  >

  // ── 统计每个 band 的 min/max（先 stride-sample，全扫百 MB 级别会卡 UI） ──
  const bandStats = rasters.map((band) => computeMinMax(band, noDataValue))

  // ── 渲染到 canvas ──────────────────────────────────────
  const canvas = document.createElement('canvas')
  canvas.width = renderWidth
  canvas.height = renderHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    throw new Error(`parseGeoTIFF: failed to get 2d canvas context for "${fileName}"`)
  }
  const imageData = ctx.createImageData(renderWidth, renderHeight)

  if (bandCount >= 3 && rasterStyle.mode !== 'singleband') {
    renderRGB(imageData, rasters, renderWidth, renderHeight, noDataValue)
  } else {
    const bandIndex = Math.min(Math.max(1, rasterStyle.band ?? 1), rasters.length) - 1
    const stats = bandStats[bandIndex] ?? bandStats[0]
    renderSingleBand(
      imageData,
      rasters[bandIndex],
      renderWidth,
      renderHeight,
      rasterStyle.min ?? stats.min,
      rasterStyle.max ?? stats.max,
      noDataValue,
      rasterStyle,
    )
  }

  ctx.putImageData(imageData, 0, 0)

  // Convert canvas to blob URL instead of data URL — avoids huge base64 strings
  // and is more memory-efficient for large rasters.
  const imageUrl = await new Promise<string>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error(`parseGeoTIFF: canvas.toBlob returned null for "${fileName}"`))
          return
        }
        resolve(URL.createObjectURL(blob))
      },
      'image/png',
    )
  })

  // ── MapLibre ImageSource 需要四角坐标（WGS84，NW,NE,SE,SW） ────
  const imageCoordinates: ParsedRasterData['imageCoordinates'] = [
    [bbox.minX, bbox.maxY], // NW
    [bbox.maxX, bbox.maxY], // NE
    [bbox.maxX, bbox.minY], // SE
    [bbox.minX, bbox.minY], // SW
  ]

  return {
    kind: 'raster',
    source: 'image',
    imageUrl,
    bbox,
    width,
    height,
    bandCount,
    crs,
    noDataValue,
    bandStats,
    imageCoordinates,
    sourcePath: options.sourcePath,
    sourceBufferId: options.sourceBufferId,
    rasterStyle,
    rerenderable: !!(options.sourcePath || options.sourceBufferId),
  }
}

// ─── 内部：地理元信息 ──────────────────────────────────────────────

/**
 * 从 GeoTIFF 读 bbox。优先级：
 * 1) `image.getBoundingBox()`（需要 ModelTiepoint + ModelPixelScale 齐活）
 * 2) fallback：抛错，不猜一个 [0, 0, width, height] 假坐标——那种
 *    伪地理 TIFF（比如截图转的）就不该走地图通道。
 */
function readBBox(image: GeoTIFFImage, fileName: string): BBox {
  try {
    const [minX, minY, maxX, maxY] = image.getBoundingBox() as [number, number, number, number]
    if (![minX, minY, maxX, maxY].every(Number.isFinite)) {
      throw new Error('bbox contains non-finite values')
    }
    return { minX, minY, maxX, maxY }
  } catch (err) {
    throw new Error(
      `parseGeoTIFF: "${fileName}" has no geographic extent (missing ModelTiepoint/ModelPixelScale). ` +
        `Re-export with gdal_translate or QGIS to assign a CRS.`,
    )
  }
}

export function rasterBBoxForMap(sourceBbox: BBox, crs: string, fileName: string): BBox {
  const normalized = crs.toUpperCase()
  if (normalized === 'EPSG:4326' || normalized === 'OGC:CRS84') {
    assertLonLatBBox(sourceBbox, fileName)
    return sourceBbox
  }
  if (normalized === 'EPSG:3857' || normalized === 'EPSG:900913') {
    return webMercatorBBoxToLonLat(sourceBbox, fileName)
  }
  throw new Error(
    `parseGeoTIFF: "${fileName}" uses ${crs}. OpenGIS can display GeoTIFF rasters in EPSG:4326 or EPSG:3857 only. ` +
      `Warp it first, for example with gdalwarp -t_srs EPSG:4326.`,
  )
}

function webMercatorBBoxToLonLat(bbox: BBox, fileName: string): BBox {
  const max = 20037508.342789244
  const clampMeters = (value: number) => Math.max(-max, Math.min(max, value))
  const project = (x: number, y: number): [number, number] => {
    const clampedX = clampMeters(x)
    const clampedY = clampMeters(y)
    const lon = (clampedX / max) * 180
    const lat = (Math.atan(Math.sinh(Math.PI * clampedY / max)) * 180) / Math.PI
    return [lon, lat]
  }
  const [minLon, minLat] = project(bbox.minX, bbox.minY)
  const [maxLon, maxLat] = project(bbox.maxX, bbox.maxY)
  const out = {
    minX: Math.min(minLon, maxLon),
    minY: Math.min(minLat, maxLat),
    maxX: Math.max(minLon, maxLon),
    maxY: Math.max(minLat, maxLat),
  }
  assertLonLatBBox(out, fileName)
  return out
}

function assertLonLatBBox(bbox: BBox, fileName: string): void {
  const values = [bbox.minX, bbox.minY, bbox.maxX, bbox.maxY]
  if (!values.every(Number.isFinite)) {
    throw new Error(`parseGeoTIFF: "${fileName}" has a non-finite geographic extent.`)
  }
  if (
    bbox.minX < -180 || bbox.maxX > 180 ||
    bbox.minY < -90 || bbox.maxY > 90 ||
    bbox.minX >= bbox.maxX ||
    bbox.minY >= bbox.maxY
  ) {
    throw new Error(
      `parseGeoTIFF: "${fileName}" has an invalid lon/lat extent ` +
        `[${bbox.minX}, ${bbox.minY}, ${bbox.maxX}, ${bbox.maxY}]. ` +
        `Warp or assign the raster CRS before loading it.`,
    )
  }
}

/**
 * 从 GeoKeys 粗略识别 CRS。本轮只接受：
 * - EPSG:4326（WGS84）
 * - EPSG:3857（Web Mercator）
 * 其它投影抛警告但继续加载，由用户自行决定是否 warp。
 *
 * 实现简化：只从 GeoKeyDirectoryTag 里读 `ProjectedCSTypeGeoKey(3072)`
 * 或 `GeographicTypeGeoKey(2048)`，不做完整 GeoKeys 解析。
 */
function readCRS(image: GeoTIFFImage): string {
  const geoKeys = image.getGeoKeys() as Record<string, unknown> | undefined
  if (!geoKeys) return 'EPSG:4326'

  const projected = geoKeys.ProjectedCSTypeGeoKey as number | undefined
  const geographic = geoKeys.GeographicTypeGeoKey as number | undefined

  if (projected && Number.isFinite(projected)) return `EPSG:${projected}`
  if (geographic && Number.isFinite(geographic)) return `EPSG:${geographic}`
  return 'EPSG:4326'
}

/**
 * 读 GDAL_NODATA tag（常见为 "-9999" / "nan" 等字符串）。
 *
 * 如果 tag 缺失，根据数据类型推断常见的 nodata 值：
 * - Int16  → -32768  (SRTM, ASTER GDEM)
 * - Int32  → -2147483648
 * - Float32/64 → -9999 (GDAL default) or -3.4028235e+38
 * 这些是 GIS 社区事实标准，覆盖 >95% 的无 tag 情况。
 */
function readNoData(image: GeoTIFFImage): number | null {
  // geotiff 库把 GDAL_NODATA 放在 fileDirectory.GDAL_NODATA
  const fd = image.fileDirectory as Record<string, unknown>
  const raw = fd?.GDAL_NODATA
  if (raw !== undefined && raw !== null) {
    const str = String(raw).trim()
    if (str !== '') {
      if (str.toLowerCase() === 'nan') return NaN
      const num = Number(str)
      if (Number.isFinite(num)) return num
    }
  }

  // ── Fallback: infer nodata from SampleFormat + BitsPerSample ──
  const sampleFormat = (fd?.SampleFormat as number[] | number | undefined)
  const bitsPerSample = (fd?.BitsPerSample as number[] | number | undefined)
  const fmt = Array.isArray(sampleFormat) ? sampleFormat[0] : (sampleFormat ?? 1)
  const bits = Array.isArray(bitsPerSample) ? bitsPerSample[0] : (bitsPerSample ?? 8)

  // SampleFormat: 1=uint, 2=int, 3=float
  if (fmt === 2 && bits === 16) return -32768        // Int16 — SRTM convention
  if (fmt === 2 && bits === 32) return -2147483648    // Int32
  // For float types, don't guess — too many conventions (-9999, -3.4e38, NaN…)
  return null
}

// ─── 内部：像素渲染 ──────────────────────────────────────────────

/**
 * Compute robust min/max using percentile stretch (P2/P98).
 *
 * Why not plain min/max?
 * - A single outlier pixel (e.g. nodata that slipped through, or a sensor
 *   spike) can compress the entire color ramp into a tiny range, making the
 *   image appear uniformly dark or bright.
 * - 2%–98% percentile stretch is the industry standard (QGIS, ArcGIS,
 *   ENVI all default to it).
 *
 * Implementation: build a 1024-bin histogram from a stride-sampled subset,
 * then walk the CDF to find P2 and P98. O(n/stride + 1024), fast enough
 * for 200M-pixel rasters.
 */
function computeMinMax(
  band: ArrayLike<number>,
  noData: number | null,
): { min: number; max: number } {
  const total = band.length
  const stride = total > 200_000 ? Math.ceil(total / 200_000) : 1

  // ── Pass 1: find absolute min/max (needed to build histogram bins) ──
  let absMin = Infinity
  let absMax = -Infinity
  let validCount = 0
  for (let i = 0; i < total; i += stride) {
    const v = band[i]
    if (!isValidPixel(v, noData)) continue
    if (v < absMin) absMin = v
    if (v > absMax) absMax = v
    validCount++
  }
  if (!Number.isFinite(absMin) || !Number.isFinite(absMax) || absMin === absMax) {
    return { min: 0, max: 1 }
  }

  // ── Pass 2: build histogram and compute P2/P98 ──
  const BINS = 1024
  const hist = new Uint32Array(BINS)
  const range = absMax - absMin
  const scale = (BINS - 1) / range

  for (let i = 0; i < total; i += stride) {
    const v = band[i]
    if (!isValidPixel(v, noData)) continue
    const bin = Math.min(BINS - 1, Math.max(0, Math.round((v - absMin) * scale)))
    hist[bin]++
  }

  // Walk CDF to find P2 and P98
  const p2Target = Math.floor(validCount * 0.02)
  const p98Target = Math.floor(validCount * 0.98)
  let cumulative = 0
  let p2Bin = 0
  let p98Bin = BINS - 1

  for (let b = 0; b < BINS; b++) {
    cumulative += hist[b]
    if (cumulative <= p2Target) p2Bin = b
    if (cumulative < p98Target) p98Bin = b
  }

  const min = absMin + (p2Bin / scale)
  const max = absMin + ((p98Bin + 1) / scale)

  return min < max ? { min, max } : { min: absMin, max: absMax }
}

/** Check if a pixel value is valid (finite and not nodata). */
function isValidPixel(v: number, noData: number | null): boolean {
  if (!Number.isFinite(v)) return false
  if (noData !== null) {
    // For NaN nodata, Number.isFinite already filtered it above
    if (Number.isFinite(noData) && v === noData) return false
  }
  return true
}

/**
 * 单 band 渲染：线性拉伸到 0-255 + viridis 调色板。
 *
 * viridis 简化实现：6 个关键色节点，RGB 线性插值。视觉上近似 matplotlib viridis，
 * 足够 DEM / NDVI / 温度等连续量可读。
 */
function renderSingleBand(
  imageData: ImageData,
  band: ArrayLike<number>,
  width: number,
  height: number,
  min: number,
  max: number,
  noData: number | null,
  style: RasterStyleSettings,
): void {
  const pixels = imageData.data
  const range = max - min || 1
  const total = width * height
  const ramp = buildColorRamp(style)
  const globalOpacity = clamp01(style.opacity ?? 1)

  for (let i = 0; i < total; i++) {
    const v = band[i]
    const px = i * 4

    if (!isValidPixel(v, noData)) {
      pixels[px] = 0
      pixels[px + 1] = 0
      pixels[px + 2] = 0
      pixels[px + 3] = 0 // transparent
      continue
    }

    // Clamp to [0,1] — values outside P2/P98 get clamped, not wrapped
    const t = clamp01((v - min) / range)
    const [r, g, b, a] = ramp(style.reverse ? 1 - t : t)
    pixels[px] = r
    pixels[px + 1] = g
    pixels[px + 2] = b
    pixels[px + 3] = Math.round(255 * a * globalOpacity)
  }
}

/**
 * 三 band 真彩色渲染。
 * 每个 band 单独线性拉伸到 0-255（避免 uint16 DN 值直接截断）。
 */
function renderRGB(
  imageData: ImageData,
  rasters: Array<ArrayLike<number>>,
  width: number,
  height: number,
  noData: number | null,
): void {
  const r = rasters[0]
  const g = rasters[1]
  const b = rasters[2]
  const a = rasters.length >= 4 ? rasters[3] : null

  // 预算三个 band 的 min/max
  const rStats = computeMinMax(r, noData)
  const gStats = computeMinMax(g, noData)
  const bStats = computeMinMax(b, noData)

  const stretch = (v: number, min: number, max: number): number => {
    if (!Number.isFinite(v)) return 0
    const t = (v - min) / (max - min || 1)
    return Math.max(0, Math.min(255, Math.round(t * 255)))
  }

  const pixels = imageData.data
  const total = width * height
  for (let i = 0; i < total; i++) {
    const px = i * 4
    const rv = r[i]
    const gv = g[i]
    const bv = b[i]

    const isNoData =
      !isValidPixel(rv, noData) || !isValidPixel(gv, noData) || !isValidPixel(bv, noData)

    if (isNoData) {
      pixels[px] = 0
      pixels[px + 1] = 0
      pixels[px + 2] = 0
      pixels[px + 3] = 0
      continue
    }

    pixels[px] = stretch(rv, rStats.min, rStats.max)
    pixels[px + 1] = stretch(gv, gStats.min, gStats.max)
    pixels[px + 2] = stretch(bv, bStats.min, bStats.max)
    // alpha：若第 4 band 是 0-255 直接用，否则 255
    pixels[px + 3] = a ? Math.max(0, Math.min(255, a[i] || 0)) : 255
  }
}

function normalizeRasterStyle(style: RasterStyleSettings | undefined, bandCount: number): RasterStyleSettings {
  const next: RasterStyleSettings = {
    mode: style?.mode ?? 'auto',
    band: style?.band ?? 1,
    ramp: style?.stops?.length ? 'custom' : (style?.ramp ?? 'viridis'),
    stops: style?.stops,
    min: style?.min,
    max: style?.max,
    opacity: style?.opacity ?? 1,
    reverse: style?.reverse ?? false,
  }
  if (next.mode === 'auto' && bandCount < 3) next.mode = 'singleband'
  return next
}

function buildColorRamp(style: RasterStyleSettings): (t: number) => [number, number, number, number] {
  const stops = normalizeStops(style.stops?.length ? style.stops : namedRamp(style.ramp ?? 'viridis'))
  return (value: number) => interpolateStops(stops, clamp01(value))
}

function namedRamp(name: RasterColorRampName): RasterColorStop[] {
  switch (name) {
    case 'gray':
      return [
        { value: 0, color: '#000000' },
        { value: 1, color: '#ffffff' },
      ]
    case 'magma':
      return [
        { value: 0, color: '#000004' },
        { value: 0.25, color: '#51127c' },
        { value: 0.5, color: '#b73779' },
        { value: 0.75, color: '#fc8961' },
        { value: 1, color: '#fcfdbf' },
      ]
    case 'plasma':
      return [
        { value: 0, color: '#0d0887' },
        { value: 0.25, color: '#7e03a8' },
        { value: 0.5, color: '#cc4778' },
        { value: 0.75, color: '#f89540' },
        { value: 1, color: '#f0f921' },
      ]
    case 'inferno':
      return [
        { value: 0, color: '#000004' },
        { value: 0.25, color: '#420a68' },
        { value: 0.5, color: '#932667' },
        { value: 0.75, color: '#dd513a' },
        { value: 1, color: '#fcffa4' },
      ]
    case 'turbo':
      return [
        { value: 0, color: '#30123b' },
        { value: 0.2, color: '#466be3' },
        { value: 0.4, color: '#35c5a3' },
        { value: 0.6, color: '#b5de2b' },
        { value: 0.8, color: '#f89540' },
        { value: 1, color: '#7a0403' },
      ]
    case 'terrain':
      return [
        { value: 0, color: '#0b3d91' },
        { value: 0.25, color: '#2b8cbe' },
        { value: 0.45, color: '#41ab5d' },
        { value: 0.7, color: '#d9c179' },
        { value: 1, color: '#ffffff' },
      ]
    case 'spectral':
      return [
        { value: 0, color: '#9e0142' },
        { value: 0.25, color: '#f46d43' },
        { value: 0.5, color: '#ffffbf' },
        { value: 0.75, color: '#66c2a5' },
        { value: 1, color: '#5e4fa2' },
      ]
    case 'viridis':
    case 'custom':
    default:
      return [
        { value: 0.0, color: '#440154' },
        { value: 0.2, color: '#46327f' },
        { value: 0.4, color: '#365c8d' },
        { value: 0.6, color: '#277f8e' },
        { value: 0.8, color: '#78d151' },
        { value: 1.0, color: '#fde725' },
      ]
  }
}

function normalizeStops(stops: RasterColorStop[]): Required<RasterColorStop>[] {
  const normalized = stops
    .map((stop) => ({
      value: clamp01(Number(stop.value)),
      color: normalizeHex(stop.color),
      opacity: clamp01(stop.opacity ?? 1),
    }))
    .sort((a, b) => a.value - b.value)
  if (!normalized.length) return normalizeStops(namedRamp('viridis'))
  if (normalized[0].value > 0) normalized.unshift({ ...normalized[0], value: 0 })
  if (normalized[normalized.length - 1].value < 1) {
    normalized.push({ ...normalized[normalized.length - 1], value: 1 })
  }
  return normalized
}

function interpolateStops(stops: Required<RasterColorStop>[], t: number): [number, number, number, number] {
  for (let i = 1; i < stops.length; i++) {
    const upper = stops[i]
    if (t <= upper.value) {
      const lower = stops[i - 1]
      const span = upper.value - lower.value || 1
      const k = (t - lower.value) / span
      const c0 = hexToRgb(lower.color)
      const c1 = hexToRgb(upper.color)
      return [
        Math.round(lerp(c0[0], c1[0], k)),
        Math.round(lerp(c0[1], c1[1], k)),
        Math.round(lerp(c0[2], c1[2], k)),
        lerp(lower.opacity, upper.opacity, k),
      ]
    }
  }
  const last = stops[stops.length - 1]
  const rgb = hexToRgb(last.color)
  return [rgb[0], rgb[1], rgb[2], last.opacity]
}

function normalizeHex(color: string): string {
  const raw = String(color || '').trim()
  if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase()
  if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
    return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`.toLowerCase()
  }
  return '#000000'
}

function hexToRgb(color: string): [number, number, number] {
  const hex = normalizeHex(color).slice(1)
  return [
    Number.parseInt(hex.slice(0, 2), 16),
    Number.parseInt(hex.slice(2, 4), 16),
    Number.parseInt(hex.slice(4, 6), 16),
  ]
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(1, value))
}
