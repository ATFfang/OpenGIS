import { describe, expect, it } from 'vitest'

import { rasterBBoxForMap } from './geotiffParser'

describe('rasterBBoxForMap', () => {
  it('keeps EPSG:4326 bbox unchanged after validation', () => {
    expect(
      rasterBBoxForMap(
        { minX: 121, minY: 31, maxX: 122, maxY: 32 },
        'EPSG:4326',
        'dem.tif',
      ),
    ).toEqual({ minX: 121, minY: 31, maxX: 122, maxY: 32 })
  })

  it('converts EPSG:3857 bbox to lon/lat for MapLibre image source', () => {
    const bbox = rasterBBoxForMap(
      {
        minX: 13469658.385986103,
        minY: 3632749.143384426,
        maxX: 13580977.876779376,
        maxY: 3763310.6271446524,
      },
      'EPSG:3857',
      'shanghai.tif',
    )

    expect(bbox.minX).toBeCloseTo(121, 5)
    expect(bbox.minY).toBeCloseTo(31, 5)
    expect(bbox.maxX).toBeCloseTo(122, 5)
    expect(bbox.maxY).toBeCloseTo(32, 5)
  })

  it('rejects unsupported projected CRS instead of rendering at the wrong place', () => {
    expect(() =>
      rasterBBoxForMap(
        { minX: 350000, minY: 3400000, maxX: 360000, maxY: 3410000 },
        'EPSG:32650',
        'utm.tif',
      ),
    ).toThrow(/EPSG:32650/)
  })
})
