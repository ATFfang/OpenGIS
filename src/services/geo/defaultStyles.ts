/**
 * Default style generator — assigns sensible map styles based on geometry type.
 * Provides a consistent color palette for auto-styling layers.
 */
import type { GeometryType, LayerStyle, LayerRenderType } from './types'

/**
 * Color palette for auto-assigning layer colors.
 * Designed for dark backgrounds with good contrast.
 */
const LAYER_COLORS = [
  '#3b82f6', // blue
  '#ef4444', // red
  '#22c55e', // green
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#14b8a6', // teal
  '#f97316', // orange
  '#6366f1', // indigo
]

let colorIndex = 0

/**
 * Get the next color from the palette (cycles through).
 */
export function getNextLayerColor(): string {
  const color = LAYER_COLORS[colorIndex % LAYER_COLORS.length]
  colorIndex++
  return color
}

/**
 * Reset the color index (e.g. when clearing all layers).
 */
export function resetLayerColorIndex(): void {
  colorIndex = 0
}

/**
 * Generate a default LayerStyle based on geometry type.
 */
export function getDefaultStyle(geometryType: GeometryType): LayerStyle {
  const color = getNextLayerColor()

  const renderType = geometryTypeToRenderType(geometryType)

  switch (renderType) {
    case 'fill':
      return {
        renderType: 'fill',
        color,
        opacity: 0.6,
        strokeColor: color,
        strokeWidth: 1,
        fillOpacity: 0.4,
      }
    case 'line':
      return {
        renderType: 'line',
        color,
        opacity: 1,
        strokeColor: color,
        strokeWidth: 2,
      }
    case 'circle':
      return {
        renderType: 'circle',
        color,
        opacity: 0.8,
        strokeColor: '#ffffff',
        strokeWidth: 1,
        radius: 5,
      }
    default:
      return {
        renderType: 'circle',
        color,
        opacity: 0.8,
        strokeColor: '#ffffff',
        strokeWidth: 1,
        radius: 5,
      }
  }
}

/**
 * Map GeoJSON geometry type to MapLibre render type.
 */
export function geometryTypeToRenderType(geometryType: GeometryType): LayerRenderType {
  switch (geometryType) {
    case 'Polygon':
    case 'MultiPolygon':
      return 'fill'
    case 'LineString':
    case 'MultiLineString':
      return 'line'
    case 'Point':
    case 'MultiPoint':
      return 'circle'
    default:
      return 'circle'
  }
}
