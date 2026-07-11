import { describe, expect, it, vi } from 'vitest'
import type { MapLayerDefinition } from '@/services/geo'
import { buildLegendSections } from '../layoutLegend'
import { buildScaleBarMetrics } from '../layoutScaleBar'
import type { LayoutElement, LayoutPage } from '../types'

vi.mock('@/features/map/renderers/categorizedRenderer', () => ({
  getCategorizedCache: (layerId: string) => (
    layerId === 'cat'
      ? { Cafe: '#ef4444', Tea: '#22c55e' }
      : null
  ),
}))

vi.mock('@/features/map/renderers/graduatedRenderer', () => ({
  getGraduatedCache: (layerId: string) => (
    layerId === 'grad'
      ? { breaks: [10, 20], palette: ['#93c5fd', '#1d4ed8', '#172554'] }
      : null
  ),
}))

const PAGE: LayoutPage = {
  id: 'landscape-16-9',
  name: '16:9',
  widthMm: 160,
  heightMm: 90,
  background: '#ffffff',
}

function element(overrides: Partial<LayoutElement>): LayoutElement {
  return {
    id: 'el',
    type: 'legend',
    label: 'Legend',
    visible: true,
    locked: false,
    frame: { x: 10, y: 10, width: 40, height: 20 },
    props: {},
    style: {},
    ...overrides,
  }
}

function layer(overrides: Partial<MapLayerDefinition>): MapLayerDefinition {
  return {
    id: 'layer',
    name: 'Layer',
    sourceType: 'geojson',
    visible: true,
    style: {
      renderType: 'circle',
      color: '#64748b',
      opacity: 1,
      strokeColor: '#334155',
      strokeWidth: 1,
    },
    data: {
      kind: 'vector',
      geojson: { type: 'FeatureCollection', features: [] },
      geometryType: 'Point',
      featureCount: 0,
      bbox: { minX: 0, minY: 0, maxX: 0, maxY: 0 },
      crs: 'EPSG:4326',
      fields: [],
    },
    meta: {
      fileName: 'layer.geojson',
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: 0,
    },
    addedAt: 1,
    ...overrides,
  }
}

describe('layout primitive contracts', () => {
  it('computes scale-bar labels from page size and map scale', () => {
    const metrics = buildScaleBarMetrics(
      element({
        type: 'scale-bar',
        frame: { x: 0, y: 0, width: 50, height: 10 },
        props: { segments: 4, autoLabel: true },
      }),
      PAGE,
      50_000,
    )

    expect(metrics.widthRatio).toBeGreaterThan(0)
    expect(metrics.widthRatio).toBeLessThanOrEqual(1)
    expect(metrics.labels).toEqual(['0 m', '500 m', '1 km', '1.5 km', '2 km'])
    expect(metrics.maxDistanceMeters).toBe(2000)
  })

  it('preserves manual scale-bar labels', () => {
    const metrics = buildScaleBarMetrics(
      element({
        type: 'scale-bar',
        props: { autoLabel: false, label: '0 2 4 km' },
      }),
      PAGE,
      50_000,
    )

    expect(metrics.labels).toEqual(['0', '2', '4 km'])
    expect(metrics.widthRatio).toBe(1)
  })

  it('builds categorized and graduated legend entries from renderer caches', () => {
    const sections = buildLegendSections(
      [
        layer({
          id: 'cat',
          name: 'Shop Type',
          style: {
            renderType: 'categorized',
            color: '#64748b',
            opacity: 1,
            strokeColor: '#334155',
            strokeWidth: 1,
          },
        }),
        layer({
          id: 'grad',
          name: 'Score',
          style: {
            renderType: 'graduated',
            color: '#64748b',
            opacity: 1,
            strokeColor: '#334155',
            strokeWidth: 1,
          },
        }),
      ],
      element({ props: { layerIds: ['cat', 'grad'], grouped: true } }),
    )

    expect(sections).toHaveLength(2)
    expect(sections[0].entries).toEqual([
      { label: 'Cafe', color: '#ef4444' },
      { label: 'Tea', color: '#22c55e' },
    ])
    expect(sections[1].entries.map((entry) => entry.label)).toEqual(['< 10', '10 - 20', '>= 20'])
  })
})
