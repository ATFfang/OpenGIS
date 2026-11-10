import type { LayerStyle, MapLayerDefinition } from './types'

/**
 * Canonicalize common model/UI color shorthand before it reaches MapLibre.
 * MapLibre rejects bare hexadecimal values, and treats `none` as an invalid
 * color even though both forms are common in generated styling instructions.
 */
export function normalizeMapColor<T>(value: T): T {
  if (typeof value !== 'string') return value

  const color = value.trim()
  if (/^[0-9a-f]{3,4}$/i.test(color) || /^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(color)) {
    return `#${color.toLowerCase()}` as T
  }
  if (color.toLowerCase() === 'none') {
    return 'transparent' as T
  }
  return color as T
}

/** Keep LayerStyle color fields canonical regardless of their input path. */
export function normalizeLayerStyleColors(style: LayerStyle): LayerStyle {
  const color = normalizeMapColor(style.color)
  const strokeColor = normalizeMapColor(style.strokeColor)
  let categorized = style.categorized
  if (categorized) {
    const colors = categorized.colors
      ? Object.fromEntries(
          Object.entries(categorized.colors).map(([key, value]) => [
            key,
            normalizeMapColor(value),
          ]),
        )
      : undefined
    const colorsChanged = Boolean(
      colors
      && categorized.colors
      && Object.keys(colors).some((key) => colors[key] !== categorized!.colors![key]),
    )
    const otherColor = categorized.otherColor
      ? normalizeMapColor(categorized.otherColor)
      : undefined
    if (colorsChanged || otherColor !== categorized.otherColor) {
      categorized = { ...categorized, colors, otherColor }
    }
  }

  let graduated = style.graduated
  if (graduated?.palette) {
    const palette = graduated.palette.map(normalizeMapColor)
    if (palette.some((value, index) => value !== graduated!.palette![index])) {
      graduated = { ...graduated, palette }
    }
  }

  let label = style.label
  if (label) {
    const labelColor = label.color ? normalizeMapColor(label.color) : undefined
    const haloColor = label.haloColor ? normalizeMapColor(label.haloColor) : undefined
    if (labelColor !== label.color || haloColor !== label.haloColor) {
      label = { ...label, color: labelColor, haloColor }
    }
  }

  let raster = style.raster
  if (raster?.stops) {
    const stops = raster.stops.map((stop) => {
      const stopColor = normalizeMapColor(stop.color)
      return stopColor === stop.color ? stop : { ...stop, color: stopColor }
    })
    if (stops.some((stop, index) => stop !== raster!.stops![index])) {
      raster = { ...raster, stops }
    }
  }

  if (
    color === style.color
    && strokeColor === style.strokeColor
    && categorized === style.categorized
    && graduated === style.graduated
    && label === style.label
    && raster === style.raster
  ) {
    return style
  }

  return {
    ...style,
    color,
    strokeColor,
    categorized,
    graduated,
    label,
    raster,
  }
}

export function normalizeLayerDefinitionColors(
  layer: MapLayerDefinition,
): MapLayerDefinition {
  const style = normalizeLayerStyleColors(layer.style)
  if (style === layer.style) return layer
  return {
    ...layer,
    style,
  }
}
