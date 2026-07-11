import { describe, expect, it } from 'vitest'

import type { ParsedVectorData } from './types'
import {
  LARGE_LAYER_THRESHOLD_BYTES,
  makeHandledVectorData,
  releaseVectorGeoJSON,
  resolveVectorGeoJSON,
  shouldHandleLayer,
} from './layerDataRegistry'

function makeVector(count: number): ParsedVectorData {
  return {
    kind: 'vector',
    geojson: {
      type: 'FeatureCollection',
      features: Array.from({ length: count }, (_, index) => ({
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [index, index] },
        properties: { id: index, name: `feature-${index}` },
      })),
    },
    geometryType: 'Point',
    featureCount: count,
    bbox: { minX: 0, minY: 0, maxX: count - 1, maxY: count - 1 },
    crs: 'EPSG:4326',
    fields: [],
  }
}

describe('layerDataRegistry', () => {
  it('only enables handle mode above the configured threshold', () => {
    expect(shouldHandleLayer(LARGE_LAYER_THRESHOLD_BYTES)).toBe(false)
    expect(shouldHandleLayer(LARGE_LAYER_THRESHOLD_BYTES + 1)).toBe(true)
  })

  it('keeps a sample in vector data and resolves the full collection while handle is alive', () => {
    const full = makeVector(20)
    const handled = makeHandledVectorData(full, {
      handleId: 'test-handle',
      sizeBytes: LARGE_LAYER_THRESHOLD_BYTES + 1,
      sampleFeatures: 3,
    })

    expect(handled.geojson.features).toHaveLength(3)
    expect(handled.featureCount).toBe(20)
    expect(handled.sampled).toBe(true)
    expect(resolveVectorGeoJSON(handled).features).toHaveLength(20)

    releaseVectorGeoJSON('test-handle')
    expect(resolveVectorGeoJSON(handled).features).toHaveLength(3)
  })
})
