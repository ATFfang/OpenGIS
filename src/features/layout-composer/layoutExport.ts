import type { MapLayerDefinition } from '@/services/geo'
import { getCategorizedCache } from '@/features/map/renderers/categorizedRenderer'
import { getGraduatedCache } from '@/features/map/renderers/graduatedRenderer'
import { getLayoutDesignWidth, scaleLayoutValue } from './layoutMetrics'
import type { LayoutElement, LayoutElementStyle, LayoutExportOptions, LayoutExportResult, LayoutPage } from './types'

const PX_PER_MM = 4

export async function exportLayoutAsPng(options: {
  page: LayoutPage
  elements: LayoutElement[]
  layers: MapLayerDefinition[]
  mapSnapshotUrl: string | null
  exportOptions: LayoutExportOptions
}): Promise<LayoutExportResult> {
  const ratio = options.exportOptions.pixelRatio
  const width = Math.round(options.page.widthMm * PX_PER_MM * ratio)
  const height = Math.round(options.page.heightMm * PX_PER_MM * ratio)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas export context is unavailable')

  ctx.fillStyle = options.page.background
  ctx.fillRect(0, 0, width, height)

  const visibleElements = options.elements.filter((element) => element.visible !== false)
  const mapSnapshot = options.mapSnapshotUrl ? await loadImage(options.mapSnapshotUrl).catch(() => null) : null
  const layoutScale = width / getLayoutDesignWidth(options.page)

  for (const element of visibleElements) {
    drawElement(ctx, element, width, height, {
      layers: options.layers,
      mapSnapshot,
      scaleDenominator: Number(element.props?.scaleDenominator ?? 0),
      layoutScale,
      page: options.page,
    })
  }

  const blob = await canvasToBlob(canvas)
  const fileName = `${options.exportOptions.fileName}.png`
  if (options.exportOptions.autoDownload ?? true) {
    triggerDownload(blob, fileName)
  }
  return { blob, width, height, fileName }
}

function drawElement(
  ctx: CanvasRenderingContext2D,
  element: LayoutElement,
  pageWidth: number,
  pageHeight: number,
  context: {
    layers: MapLayerDefinition[]
    mapSnapshot: HTMLImageElement | null
    scaleDenominator: number
    layoutScale: number
    page: LayoutPage
  },
): void {
  const box = frameToPixels(element, pageWidth, pageHeight)
  if (element.type === 'map-frame') {
    drawMapFrame(ctx, box, element, context.mapSnapshot, context.layoutScale)
  } else if (element.type === 'scale-bar') {
    drawScaleBar(ctx, box, element, context.layoutScale, context.page, context.scaleDenominator)
  } else if (element.type === 'north-arrow') {
    drawNorthArrow(ctx, box, element, context.layoutScale)
  } else if (element.type === 'legend') {
    drawLegend(ctx, box, element, context.layers, context.layoutScale)
  } else if (element.type === 'text') {
    drawText(ctx, box, element, context.layoutScale)
  }
}

function frameToPixels(
  element: LayoutElement,
  pageWidth: number,
  pageHeight: number,
): { x: number; y: number; width: number; height: number } {
  return {
    x: (element.frame.x / 100) * pageWidth,
    y: (element.frame.y / 100) * pageHeight,
    width: (element.frame.width / 100) * pageWidth,
    height: (element.frame.height / 100) * pageHeight,
  }
}

function drawMapFrame(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  element: LayoutElement,
  mapSnapshot: HTMLImageElement | null,
  layoutScale: number,
): void {
  ctx.save()
  const style = element?.style ?? {}
  const mapView = element?.mapView ?? { x: 0, y: 0, scale: 1 }
  ctx.globalAlpha = style.opacity ?? 1
  ctx.fillStyle = style.backgroundColor ?? '#f3f4f6'
  ctx.fillRect(box.x, box.y, box.width, box.height)
  ctx.save()
  ctx.beginPath()
  roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
  ctx.clip()
  if (mapSnapshot) {
    const scale = Math.max(box.width / mapSnapshot.width, box.height / mapSnapshot.height) * mapView.scale
    const sw = box.width / scale
    const sh = box.height / scale
    const sx = (mapSnapshot.width - sw) / 2 - (mapView.x / 100) * sw
    const sy = (mapSnapshot.height - sh) / 2 - (mapView.y / 100) * sh
    ctx.drawImage(mapSnapshot, sx, sy, sw, sh, box.x, box.y, box.width, box.height)
  } else {
    ctx.fillStyle = '#dbeafe'
    ctx.fillRect(box.x, box.y, box.width, box.height)
    ctx.fillStyle = '#64748b'
    ctx.font = `${scaleLayoutValue(12, layoutScale)}px sans-serif`
    ctx.textAlign = 'center'
    ctx.fillText('Map snapshot unavailable', box.x + box.width / 2, box.y + box.height / 2)
  }
  ctx.restore()
  ctx.strokeStyle = style.borderColor ?? '#111827'
  ctx.lineWidth = scaleLayoutValue(style.borderWidth ?? 1.5, layoutScale)
  roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
  ctx.stroke()
  ctx.restore()
}

function drawScaleBar(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  element: LayoutElement,
  layoutScale: number,
  page: LayoutPage,
  fallbackScaleDenominator: number,
): void {
  ctx.save()
  const style = element.style ?? {}
  const variant = style.variant ?? 'alternating'
  const color = style.strokeColor ?? '#111827'
  const fill = style.fillColor ?? color
  const textColor = style.textColor ?? color
  const strokeWidth = scaleLayoutValue(style.strokeWidth ?? 2, layoutScale)
  const segments = Math.max(1, Math.min(8, Number(element.props?.segments ?? 4)))
  const padding = scaleLayoutValue(style.padding ?? 0, layoutScale)
  const inner = {
    x: box.x + padding,
    y: box.y + padding,
    width: Math.max(1, box.width - padding * 2),
    height: Math.max(1, box.height - padding * 2),
  }
  const fontSize = style.fontSize != null
    ? scaleLayoutValue(style.fontSize, layoutScale)
    : Math.max(scaleLayoutValue(9, layoutScale), box.height * 0.28)
  const metrics = buildScaleBarMetrics(element, page, fallbackScaleDenominator)
  const labels = metrics.labels
  drawDecoratedBackground(ctx, box, style, color, layoutScale)
  ctx.globalAlpha = style.opacity ?? 1
  const y = Math.max(inner.y + inner.height * 0.25, inner.y + inner.height - fontSize - scaleLayoutValue(6, layoutScale))
  const segmentHeight = Math.min(scaleLayoutValue(10, layoutScale), Math.max(scaleLayoutValue(4, layoutScale), inner.height * 0.28))
  ctx.strokeStyle = color
  ctx.fillStyle = fill
  ctx.lineWidth = strokeWidth
  ctx.beginPath()
  const barWidth = inner.width * metrics.widthRatio
  ctx.moveTo(inner.x, y)
  ctx.lineTo(inner.x + barWidth, y)
  ctx.stroke()
  if (variant === 'alternating') {
    const segW = barWidth / segments
    for (let i = 0; i < segments; i += 1) {
      ctx.fillStyle = i % 2 === 0 ? fill : 'rgba(255,255,255,0)'
      ctx.fillRect(inner.x + segW * i, y - segmentHeight, segW, segmentHeight)
      ctx.strokeRect(inner.x + segW * i, y - segmentHeight, segW, segmentHeight)
    }
  } else {
    for (let i = 0; i <= segments; i += 1) {
      const x = inner.x + (barWidth / segments) * i
      ctx.beginPath()
      ctx.moveTo(x, y - segmentHeight)
      ctx.lineTo(x, y + segmentHeight * 0.65)
      ctx.stroke()
    }
    if (variant === 'double-line') {
      ctx.beginPath()
      ctx.moveTo(inner.x, y - segmentHeight * 0.8)
      ctx.lineTo(inner.x + barWidth, y - segmentHeight * 0.8)
      ctx.stroke()
    }
  }
  ctx.fillStyle = textColor
  ctx.font = `${fontSize}px sans-serif`
  ctx.textBaseline = 'bottom'
  labels.forEach((label, index) => {
    const ratio = labels.length === 1 ? 0.5 : index / (labels.length - 1)
    ctx.textAlign = labels.length === 1 ? 'center' : index === 0 ? 'left' : index === labels.length - 1 ? 'right' : 'center'
    ctx.fillText(label, inner.x + barWidth * ratio, inner.y + inner.height)
  })
  ctx.restore()
}

function drawNorthArrow(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  element: LayoutElement,
  layoutScale: number,
): void {
  ctx.save()
  const style = element.style ?? {}
  const variant = style.variant ?? 'classic'
  const cx = box.x + box.width / 2
  drawDecoratedBackground(ctx, box, style, style.fillColor ?? '#111827', layoutScale)
  ctx.globalAlpha = style.opacity ?? 1
  ctx.fillStyle = style.fillColor ?? '#111827'
  ctx.strokeStyle = style.strokeColor ?? style.fillColor ?? '#111827'
  if (variant === 'compass') {
    ctx.lineWidth = Math.max(scaleLayoutValue(2, layoutScale), box.width * 0.04)
    ctx.beginPath()
    ctx.arc(cx, box.y + box.height * 0.45, Math.min(box.width, box.height) * 0.28, 0, Math.PI * 2)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(cx, box.y + box.height * 0.02)
    ctx.lineTo(box.x + box.width * 0.66, box.y + box.height * 0.45)
    ctx.lineTo(cx, box.y + box.height * 0.88)
    ctx.lineTo(box.x + box.width * 0.34, box.y + box.height * 0.45)
    ctx.closePath()
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.moveTo(cx, box.y)
    ctx.lineTo(box.x + box.width, box.y + box.height * 0.72)
    ctx.lineTo(cx, box.y + box.height * (variant === 'triangle' ? 0.48 : 0.55))
    ctx.lineTo(box.x, box.y + box.height * 0.72)
    ctx.closePath()
    ctx.fill()
  }
  ctx.fillStyle = style.textColor ?? style.fillColor ?? '#111827'
  const fontSize = style.fontSize != null
    ? scaleLayoutValue(style.fontSize, layoutScale)
    : Math.max(scaleLayoutValue(12, layoutScale), box.height * 0.22)
  ctx.font = `${fontSize}px sans-serif`
  ctx.textAlign = 'center'
  ctx.fillText('N', cx, box.y + box.height)
  ctx.restore()
}

function drawLegend(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  element: LayoutElement,
  layers: MapLayerDefinition[],
  layoutScale: number,
): void {
  ctx.save()
  const style = element.style ?? {}
  const padding = scaleLayoutValue(style.padding ?? 8, layoutScale)
  const fontSize = style.fontSize != null
    ? scaleLayoutValue(style.fontSize, layoutScale)
    : Math.max(scaleLayoutValue(9, layoutScale), box.width * 0.055)
  const titleFontSize = style.fontSize != null
    ? scaleLayoutValue(style.fontSize + 2, layoutScale)
    : Math.max(scaleLayoutValue(11, layoutScale), box.width * 0.08)
  const swatchSize = scaleLayoutValue(12, layoutScale)
  const rowGap = scaleLayoutValue(20, layoutScale)
  const sectionGap = scaleLayoutValue(8, layoutScale)
  ctx.globalAlpha = style.backgroundOpacity ?? (style.opacity ?? 0.94)
  ctx.fillStyle = style.backgroundColor ?? '#ffffff'
  roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
  ctx.fill()
  ctx.globalAlpha = style.opacity ?? 1
  ctx.strokeStyle = style.borderColor ?? '#d1d5db'
  ctx.lineWidth = scaleLayoutValue(style.borderWidth ?? 1, layoutScale)
  roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
  ctx.stroke()
  let cursorY = box.y + padding + titleFontSize
  const sections = buildLegendSections(layers, element)
  if (sections.length === 0) {
    ctx.fillStyle = '#737373'
    ctx.font = `${fontSize}px sans-serif`
    ctx.fillText('选择图层生成图例', box.x + padding, cursorY)
    ctx.restore()
    return
  }
  ctx.fillStyle = style.textColor ?? '#111827'
  ctx.font = `${titleFontSize}px sans-serif`
  ctx.fillText(String(element.props?.title ?? 'Legend'), box.x + padding, cursorY)
  cursorY += sectionGap
  sections.forEach((section) => {
    if (cursorY > box.y + box.height - padding) return
    if (section.showTitle) {
      cursorY += titleFontSize
      ctx.fillStyle = style.textColor ?? '#111827'
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillText(section.title, box.x + padding, cursorY)
      cursorY += scaleLayoutValue(4, layoutScale)
    }
    section.entries.forEach((entry) => {
      cursorY += rowGap
      if (cursorY > box.y + box.height - padding) return
      ctx.fillStyle = entry.color
      ctx.fillRect(box.x + padding, cursorY - swatchSize * 0.75, swatchSize, swatchSize)
      ctx.strokeStyle = '#111827'
      ctx.strokeRect(box.x + padding, cursorY - swatchSize * 0.75, swatchSize, swatchSize)
      ctx.fillStyle = style.textColor ?? '#111827'
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillText(entry.label, box.x + padding + scaleLayoutValue(20, layoutScale), cursorY)
    })
    cursorY += sectionGap
  })
  ctx.restore()
}

function drawText(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  element: LayoutElement,
  layoutScale: number,
): void {
  ctx.save()
  const style = element.style ?? {}
  ctx.globalAlpha = style.opacity ?? 1
  ctx.fillStyle = style.textColor ?? '#111827'
  const fontSize = style.fontSize != null
    ? scaleLayoutValue(style.fontSize, layoutScale)
    : Math.max(scaleLayoutValue(14, layoutScale), box.height * 0.5)
  ctx.font = `${style.fontWeight ?? 600} ${fontSize}px sans-serif`
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  const text = String(element.props?.text ?? 'Map Title')
  ctx.fillText(text, box.x, box.y + box.height / 2, box.width)
  ctx.restore()
}

function drawDecoratedBackground(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  style: LayoutElementStyle,
  fallbackBorderColor: string,
  layoutScale: number,
): void {
  const backgroundOpacity = style.backgroundOpacity ?? 0
  const borderWidth = scaleLayoutValue(style.borderWidth ?? 0, layoutScale)
  if (backgroundOpacity <= 0 && borderWidth <= 0) return

  ctx.save()
  if (backgroundOpacity > 0) {
    ctx.globalAlpha = backgroundOpacity
    ctx.fillStyle = style.backgroundColor ?? '#ffffff'
    roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
    ctx.fill()
  }
  if (borderWidth > 0) {
    ctx.globalAlpha = style.opacity ?? 1
    ctx.strokeStyle = style.borderColor ?? fallbackBorderColor
    ctx.lineWidth = borderWidth
    roundedRect(ctx, box.x, box.y, box.width, box.height, scaleLayoutValue(style.borderRadius ?? 0, layoutScale))
    ctx.stroke()
  }
  ctx.restore()
}

interface LegendSection {
  layerId: string
  title: string
  showTitle: boolean
  entries: Array<{ label: string; color: string }>
}

function buildLegendSections(layers: MapLayerDefinition[], element: LayoutElement): LegendSection[] {
  const selectedLayerIds = Array.isArray(element.props?.layerIds)
    ? element.props.layerIds.filter((id): id is string => typeof id === 'string')
    : []
  if (selectedLayerIds.length === 0) return []
  const grouped = element.props?.grouped !== false
  const selected = new Set(selectedLayerIds)
  return layers
    .filter((layer) => selected.has(layer.id))
    .map((layer) => ({
      layerId: layer.id,
      title: layer.name,
      showTitle: grouped || selectedLayerIds.length > 1,
      entries: buildLegendEntries(layer),
    }))
}

function buildLegendEntries(layer: MapLayerDefinition): Array<{ label: string; color: string }> {
  if (layer.style.renderType === 'categorized') {
    const colors = getCategorizedCache(layer.id) ?? layer.style.categorized?.colors ?? {}
    const entries = Object.entries(colors).map(([label, color]) => ({ label, color }))
    if (entries.length > 0) return entries
  }

  if (layer.style.renderType === 'graduated') {
    const cached = getGraduatedCache(layer.id)
    const breaks = cached?.breaks ?? layer.style.graduated?.breaks ?? []
    const palette = cached?.palette ?? layer.style.graduated?.palette ?? []
    if (breaks.length > 0 && palette.length > 0) {
      return palette.map((color, index) => ({ color, label: graduatedLabel(index, breaks) }))
    }
  }

  return [{ label: layer.name, color: layer.style.color || layer.style.strokeColor || '#64748b' }]
}

function graduatedLabel(index: number, breaks: number[]): string {
  const format = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (index === 0) return `< ${format(breaks[0])}`
  if (index >= breaks.length) return `>= ${format(breaks[breaks.length - 1])}`
  return `${format(breaks[index - 1])} - ${format(breaks[index])}`
}

function buildScaleBarMetrics(
  element: LayoutElement,
  page: LayoutPage,
  fallbackScaleDenominator: number,
): { labels: string[]; widthRatio: number; maxDistanceMeters: number } {
  const auto = element.props?.autoLabel !== false
  if (!auto) {
    return {
      labels: parseScaleBarLabels(String(element.props?.label ?? '0        5        10 km')),
      widthRatio: 1,
      maxDistanceMeters: 0,
    }
  }
  const scaleDenominator = Number(element.props?.scaleDenominator ?? fallbackScaleDenominator)
  const widthMm = page.widthMm * (element.frame.width / 100)
  const availableMeters = Math.max(0, (widthMm * scaleDenominator) / 1000)
  const maxDistanceMeters = niceDistance(availableMeters)
  const widthRatio = availableMeters > 0 ? Math.max(0.2, Math.min(1, maxDistanceMeters / availableMeters)) : 1
  const segments = Math.max(1, Math.min(8, Number(element.props?.segments ?? 4)))
  const labels = Array.from({ length: segments + 1 }, (_, index) =>
    formatDistance((maxDistanceMeters / segments) * index),
  )
  return { labels, widthRatio, maxDistanceMeters }
}

function niceDistance(maxMeters: number): number {
  if (!Number.isFinite(maxMeters) || maxMeters <= 0) return 0
  const exponent = Math.floor(Math.log10(maxMeters))
  const base = Math.pow(10, exponent)
  for (const multiple of [5, 2, 1]) {
    const candidate = multiple * base
    if (candidate <= maxMeters) return candidate
  }
  return base / 2
}

function formatDistance(meters: number): string {
  if (meters >= 1000) {
    const km = meters / 1000
    return `${Number.isInteger(km) ? km : km.toFixed(km >= 10 ? 1 : 2)} km`
  }
  return `${Math.round(meters)} m`
}

function parseScaleBarLabels(label: string): string[] {
  const parts = label.trim().split(/\s+/).filter(Boolean)
  if (parts.length <= 1) return parts.length === 1 ? parts : ['0', '5', '10 km']
  const last = parts[parts.length - 1]
  const previous = parts[parts.length - 2]
  if (!looksNumeric(last) && looksNumeric(previous)) {
    return [...parts.slice(0, -2), `${previous} ${last}`]
  }
  return parts
}

function looksNumeric(value: string): boolean {
  return /^[-+]?\d+(?:\.\d+)?(?:,\d{3})*$/.test(value)
}

function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2))
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + width - r, y)
  ctx.quadraticCurveTo(x + width, y, x + width, y + r)
  ctx.lineTo(x + width, y + height - r)
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
  ctx.lineTo(x + r, y + height)
  ctx.quadraticCurveTo(x, y + height, x, y + height - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('Failed to load image'))
    image.src = src
  })
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('Failed to export layout canvas'))
    }, 'image/png')
  })
}

function triggerDownload(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
