/**
 * Symbol renderer — renders points as icons (SVG/emoji/PNG) and/or text labels.
 *
 * Uses MapLibre's `symbol` layer type which supports:
 * - icon-image (custom icons registered via addImage)
 * - text-field (attribute-based labels)
 * - Combined icon + text rendering
 *
 * Style fields consumed:
 *   style.icon    — 'emoji:📍' | 'svg:pin' | 'path:/abs/icon.svg' | null
 *   style.label   — { field, fontSize, color, offset, haloColor, haloWidth }
 *   style.color   — fallback icon tint / text color
 *   style.opacity — overall layer opacity
 */
import {
  type LayerRenderer,
  type RendererContext,
  renderLayerId,
  sourceIdFor,
} from './types'

// Built-in emoji icons mapped to simple IDs
const EMOJI_ICONS: Record<string, string> = {
  'pin': '📍',
  'marker': '📌',
  'star': '⭐',
  'fire': '🔥',
  'dot': '●',
  'cross': '✕',
  'check': '✓',
  'warning': '⚠️',
}

function resolveIconConfig(icon: string | undefined): { imageId: string | null; isEmoji: boolean } {
  if (!icon) return { imageId: null, isEmoji: false }
  if (icon.startsWith('emoji:')) {
    return { imageId: icon, isEmoji: true }
  }
  if (icon.startsWith('svg:') || icon.startsWith('path:')) {
    return { imageId: icon, isEmoji: false }
  }
  // Check built-in emoji map
  if (EMOJI_ICONS[icon]) {
    return { imageId: `emoji:${EMOJI_ICONS[icon]}`, isEmoji: true }
  }
  return { imageId: icon, isEmoji: false }
}

export const symbolRenderer: LayerRenderer = {
  renderType: 'symbol',

  attach(def, ctx) {
    const { map } = ctx
    const sourceId = sourceIdFor(def.id)
    const layerId = renderLayerId(def.id, 'symbol')
    const visibility = def.visible ? 'visible' : 'none'

    if (map.getLayer(layerId)) return

    const { icon, label, color, opacity } = def.style
    const iconConf = resolveIconConfig(icon)

    // Build layout properties
    const layout: Record<string, unknown> = {
      visibility,
      'icon-allow-overlap': true,
      'text-allow-overlap': true,
      'text-ignore-placement': false,
      'text-font': ['Noto Color Emoji', 'Apple Color Emoji', 'Segoe UI Emoji', 'Arial Unicode MS'],
    }

    // Build paint properties
    const paint: Record<string, unknown> = {
      'icon-opacity': opacity ?? 1,
      'text-opacity': opacity ?? 1,
    }

    // Icon configuration
    if (iconConf.imageId && !iconConf.isEmoji) {
      // SVG/PNG: register image and use icon-image
      this._ensureImage(ctx, iconConf.imageId)
      layout['icon-image'] = iconConf.imageId
      layout['icon-size'] = 1
    }

    // Label / Emoji text configuration
    if (iconConf.isEmoji) {
      // Emoji: render as text
      const emoji = iconConf.imageId!.replace('emoji:', '')
      layout['text-field'] = emoji
      layout['text-size'] = 20
      layout['text-anchor'] = 'center'
      layout['text-offset'] = [0, 0]
      paint['text-color'] = color ?? '#333333'
    } else if (label?.field) {
      // Text label from feature property
      layout['text-field'] = ['get', label.field]
      layout['text-size'] = label.fontSize ?? 12
      layout['text-anchor'] = 'bottom'
      layout['text-offset'] = label.offset ?? [0, -0.8]
      paint['text-color'] = label.color ?? color ?? '#333333'
      if (label.haloColor) {
        paint['text-halo-color'] = label.haloColor
        paint['text-halo-width'] = label.haloWidth ?? 1
      }
    } else {
      // Fallback: use color dot (render as small circle text)
      layout['text-field'] = '●'
      layout['text-size'] = 14
      layout['text-anchor'] = 'center'
      layout['text-offset'] = [0, 0]
      paint['text-color'] = color ?? '#333333'
    }

    // Hover state support
    const hoverColor = '#6366f1'

    ctx.addRenderLayer({
      id: layerId,
      type: 'symbol',
      source: sourceId,
      layout,
      paint: {
        ...paint,
        'text-color': [
          'case',
          ['boolean', ['feature-state', 'hover'], false],
          hoverColor,
          paint['text-color'] ?? color ?? '#333333',
        ] as any,
        'icon-opacity': [
          'case',
          ['boolean', ['feature-state', 'hover'], false],
          0.8,
          paint['icon-opacity'] ?? 1,
        ] as any,
      },
    })
    ctx.registerRenderLayerId(def.id, layerId)
  },

  update(def, ctx) {
    const layerId = renderLayerId(def.id, 'symbol')
    if (!ctx.map.getLayer(layerId)) return

    const { icon, label, color, opacity } = def.style
    const iconConf = resolveIconConfig(icon)

    ctx.map.setPaintProperty(layerId, 'icon-opacity', opacity ?? 1)
    ctx.map.setPaintProperty(layerId, 'text-opacity', opacity ?? 1)

    // Update icon
    if (iconConf.imageId && !iconConf.isEmoji) {
      this._ensureImage(ctx, iconConf.imageId)
      ctx.map.setLayoutProperty(layerId, 'icon-image', iconConf.imageId)
    } else {
      ctx.map.setLayoutProperty(layerId, 'icon-image', undefined)
    }

    // Update text
    if (iconConf.isEmoji) {
      const emoji = iconConf.imageId!.replace('emoji:', '')
      ctx.map.setLayoutProperty(layerId, 'text-field', emoji)
      ctx.map.setLayoutProperty(layerId, 'text-size', 20)
      ctx.map.setPaintProperty(layerId, 'text-color', color ?? '#333333')
    } else if (label?.field) {
      ctx.map.setLayoutProperty(layerId, 'text-field', ['get', label.field])
      ctx.map.setLayoutProperty(layerId, 'text-size', label.fontSize ?? 12)
      ctx.map.setPaintProperty(layerId, 'text-color', label.color ?? color ?? '#333333')
      if (label.haloColor) {
        ctx.map.setPaintProperty(layerId, 'text-halo-color', label.haloColor)
        ctx.map.setPaintProperty(layerId, 'text-halo-width', label.haloWidth ?? 1)
      }
    } else {
      ctx.map.setLayoutProperty(layerId, 'text-field', '●')
      ctx.map.setLayoutProperty(layerId, 'text-size', 14)
      ctx.map.setPaintProperty(layerId, 'text-color', color ?? '#333333')
    }
  },

  listRenderLayerIds(def) {
    return [renderLayerId(def.id, 'symbol')]
  },

  /** Register an image on the map if not already present. */
  _ensureImage(ctx: RendererContext, imageId: string) {
    const map = ctx.map as any
    if (map.hasImage?.(imageId)) return

    if (imageId.startsWith('emoji:')) {
      // Emoji — render to canvas and register as image
      const emoji = imageId.replace('emoji:', '')
      const size = 32
      const canvas = document.createElement('canvas')
      canvas.width = size
      canvas.height = size
      const cctx = canvas.getContext('2d')!
      cctx.font = `${size - 4}px serif`
      cctx.textAlign = 'center'
      cctx.textBaseline = 'middle'
      cctx.fillText(emoji, size / 2, size / 2)
      const imgData = cctx.getImageData(0, 0, size, size)
      map.addImage(imageId, { width: size, height: size, data: imgData.data })
    } else if (imageId.startsWith('svg:')) {
      // SVG — load as image via fetch
      const svgId = imageId.replace('svg:', '')
      const svgPath = `/icons/${svgId}.svg`  // Convention: SVGs in public/icons/
      _loadImage(map, imageId, svgPath)
    } else if (imageId.startsWith('path:')) {
      // Absolute file path — load via pathToImageUrl
      const filePath = imageId.replace('path:', '')
      _loadImage(map, imageId, filePath)
    }
  },
} as LayerRenderer & { _ensureImage: (ctx: RendererContext, imageId: string) => void }

function _loadImage(map: any, imageId: string, src: string) {
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.onload = () => {
    try {
      map.addImage(imageId, img)
    } catch {
      // Already added
    }
  }
  img.onerror = () => {
    console.warn(`[symbolRenderer] Failed to load image: ${src}`)
  }
  img.src = src
}
