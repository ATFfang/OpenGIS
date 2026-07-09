import type { MapLayerDefinition } from '@/services/geo'
import { getCategorizedCache } from '@/features/map/renderers/categorizedRenderer'
import { getGraduatedCache } from '@/features/map/renderers/graduatedRenderer'
import type { LayoutElement } from './types'

export interface LegendSection {
  layerId: string
  title: string
  showTitle: boolean
  entries: Array<{ label: string; color: string }>
}

export function buildLegendSections(
  layers: MapLayerDefinition[],
  element: LayoutElement,
): LegendSection[] {
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
    const entries = Object.entries(colors).map(([label, color]) => ({
      label,
      color,
    }))
    if (entries.length > 0) return entries
  }

  if (layer.style.renderType === 'graduated') {
    const cached = getGraduatedCache(layer.id)
    const breaks = cached?.breaks ?? layer.style.graduated?.breaks ?? []
    const palette = cached?.palette ?? layer.style.graduated?.palette ?? []
    if (breaks.length > 0 && palette.length > 0) {
      return palette.map((color, index) => ({
        color,
        label: graduatedLabel(index, breaks),
      }))
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
