import { beforeEach, describe, expect, it } from 'vitest'

import {
  makeHandledVectorData,
  resolveVectorGeoJSON,
  type MapLayerDefinition,
  type ParsedVectorData,
} from '@/services/geo'
import { useMapStore } from './mapStore'

function vectorWithFeature(name: string): ParsedVectorData {
  return {
    kind: 'vector',
    geojson: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [121.4, 31.2] },
          properties: { name },
        },
      ],
    },
    geometryType: 'Point',
    featureCount: 1,
    bbox: { minX: 121.4, minY: 31.2, maxX: 121.4, maxY: 31.2 },
    crs: 'EPSG:4326',
    fields: [{ name: 'name', type: 'string', nullCount: 0, sampleValues: [name] }],
  }
}

function layer(data: ParsedVectorData): MapLayerDefinition {
  return {
    id: 'reused-layer',
    name: 'reused-layer',
    sourceType: 'geojson',
    visible: true,
    style: {
      renderType: 'circle',
      color: '#ef4444',
      opacity: 1,
      strokeColor: '#ffffff',
      strokeWidth: 1,
    },
    data,
    meta: {
      fileName: 'reused-layer.geojson',
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: 1,
    },
    addedAt: Date.now(),
  }
}

describe('mapStore layer resource ownership', () => {
  beforeEach(() => {
    useMapStore.setState({ layers: [], activeLayerId: null })
  })

  it('keeps newly registered vector data when replacing the same handle', () => {
    const first = makeHandledVectorData(vectorWithFeature('old'), {
      handleId: 'vector:reused-layer',
      sizeBytes: 1,
    })
    useMapStore.getState().addLayer(layer(first))

    const replacement = makeHandledVectorData(vectorWithFeature('new'), {
      handleId: 'vector:reused-layer',
      sizeBytes: 1,
    })
    useMapStore.getState().addLayer(layer(replacement))

    const stored = useMapStore.getState().getLayerById('reused-layer')
    expect(stored?.data.kind).toBe('vector')
    if (!stored || stored.data.kind !== 'vector') return
    expect(resolveVectorGeoJSON(stored.data).features[0].properties?.name).toBe('new')
  })

  it('normalizes colors on direct additions and persisted-style replacement', () => {
    const definition = layer(vectorWithFeature('point'))
    definition.style.strokeColor = 'cccccc'
    definition.style.color = 'none'

    useMapStore.getState().addLayer(definition)

    expect(useMapStore.getState().getLayerById('reused-layer')?.style).toMatchObject({
      color: 'transparent',
      strokeColor: '#cccccc',
    })
  })
})
