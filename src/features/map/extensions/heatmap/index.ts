/**
 * Heatmap Extension — 扩展热力图渲染
 *
 * 直接操作 MapLibre API，不走 MapEngine.syncLayer。
 * 但会在 mapStore 注册一个轻量层条目，让 LayerPanel 能显示和控制可见性。
 * MapView 的 base 同步逻辑会跳过 extension 层（通过 l.extension 字段过滤）。
 */

import type { MapExtension, ExtensionContext } from '../types'
import { registerExtension } from '../registry'
import { useMapStore } from '@/stores/mapStore'
import { mapEngine } from '@/features/map/engine/MapEngine'
import type { MapLayerDefinition, GeoJSONFeatureCollection } from '@/services/geo'

const SOURCE_ID = 'ext-heatmap-source'
const LAYER_ID = 'ext-heatmap-layer'

const HEAT_PALETTE: (number | string)[] = [
  0, 'rgba(33,102,172,0)',
  0.2, 'rgb(103,169,207)',
  0.4, 'rgb(209,229,240)',
  0.6, 'rgb(253,219,199)',
  0.8, 'rgb(239,138,98)',
  1.0, 'rgb(178,24,43)',
]

let unsubStore: (() => void) | null = null
/** 最近一次渲染参数，用于 style reload 后重建 */
let lastRenderParams: any = null

function buildHeatmapPaint(radius: unknown, intensity: unknown, opacity: unknown): any {
  return {
    'heatmap-weight': [
      'case',
      ['==', ['typeof', ['get', 'weight']], 'number'],
      ['get', 'weight'],
      1,
    ],
    'heatmap-intensity': typeof intensity === 'number' ? intensity : 1,
    'heatmap-radius': typeof radius === 'number' ? radius : 30,
    'heatmap-opacity': typeof opacity === 'number' ? opacity : 0.8,
    'heatmap-color': [
      'interpolate',
      ['linear'],
      ['heatmap-density'],
      ...HEAT_PALETTE,
    ],
  }
}

const heatmapExtension: MapExtension = {
  name: 'heatmap',
  methods: ['ext.heatmap.render', 'ext.heatmap.remove'],

  capability: {
    name: 'heatmap',
    display_name: 'Extension Heatmap',
    description:
      'Render a heatmap from GeoJSON point data with per-feature weights. ' +
      'GPU-accelerated via MapLibre native heatmap layer.',
    params: [
      { name: 'geojson', type: 'object', description: 'GeoJSON FeatureCollection (features must have a numeric "weight" property)', required: true },
      { name: 'radius', type: 'number', description: 'Kernel radius in pixels (default: 30)' },
      { name: 'intensity', type: 'number', description: 'Global intensity multiplier (default: 1)' },
      { name: 'opacity', type: 'number', description: 'Layer opacity 0-1 (default: 0.8)' },
    ],
  },

  handle(method, params, ctx) {
    if (method === 'ext.heatmap.remove') {
      removeHeatmap(ctx)
      return
    }
    renderHeatmap(params, ctx)
  },

  dispose() {
    cleanupStoreSub()
    lastRenderParams = null
  },
}

function cleanupStoreSub(): void {
  unsubStore?.()
  unsubStore = null
}

function computeGeoJSONBBox(fc: GeoJSONFeatureCollection): { minX: number; minY: number; maxX: number; maxY: number } | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const f of fc.features) {
    const geom = f.geometry
    if (!geom) continue
    const coords: number[] = []
    collectCoords(geom.coordinates, coords)
    for (let i = 0; i < coords.length; i += 2) {
      if (coords[i] < minX) minX = coords[i]
      if (coords[i] > maxX) maxX = coords[i]
      if (coords[i + 1] < minY) minY = coords[i + 1]
      if (coords[i + 1] > maxY) maxY = coords[i + 1]
    }
  }
  return Number.isFinite(minX) ? { minX, minY, maxX, maxY } : null
}

function collectCoords(obj: any, out: number[]): void {
  if (typeof obj[0] === 'number') {
    out.push(obj[0], obj[1])
    return
  }
  for (const item of obj) {
    if (Array.isArray(item)) collectCoords(item, out)
  }
}

function renderHeatmap(params: any, ctx: ExtensionContext): void {
  const { map } = ctx
  if (!map) return

  const { geojson, radius, intensity, opacity } = params || {}
  if (!geojson) {
    console.warn('[ext:heatmap] missing geojson')
    return
  }

  // MapLibre requires style to be loaded before adding sources/layers.
  if (!map.isStyleLoaded()) {
    map.once('style.load', () => renderHeatmap(params, ctx))
    return
  }

  // Clean up existing heatmap resources
  if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID)
  if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)

  // Add to MapLibre
  map.addSource(SOURCE_ID, { type: 'geojson', data: geojson })
  map.addLayer({
    id: LAYER_ID,
    type: 'heatmap',
    source: SOURCE_ID,
    paint: buildHeatmapPaint(radius, intensity, opacity),
  })

  // Register with MapEngine so setBasemapVisible / setLabelsVisible skip it
  mapEngine.trackExternalLayer(LAYER_ID)
  mapEngine.trackExternalSource(SOURCE_ID)

  // Save params for style reload re-render
  lastRenderParams = params

  // Compute actual bbox from data
  const fc = geojson as GeoJSONFeatureCollection
  const featureCount = fc.features?.length ?? 0
  const bbox = computeGeoJSONBBox(fc) ?? { minX: -180, minY: -90, maxX: 180, maxY: 90 }

  // Register in mapStore so LayerPanel shows it.
  // MapView skips layers with l.extension set — see syncLayersToMap filter.
  const layerDef: MapLayerDefinition = {
    id: LAYER_ID,
    name: 'Heatmap',
    sourceType: 'geojson',
    visible: true,
    style: {
      renderType: 'heatmap',
      color: '#ff0000',
      opacity: typeof opacity === 'number' ? opacity : 0.8,
      strokeColor: '#000000',
      strokeWidth: 0,
    },
    data: {
      kind: 'vector',
      geojson: fc,
      geometryType: 'Point',
      featureCount,
      bbox,
      crs: 'EPSG:4326',
      fields: [],
    },
    meta: {
      fileName: 'heatmap.geojson',
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: 0,
    },
    addedAt: Date.now(),
    extension: 'heatmap',
  }

  const store = useMapStore.getState()
  // Remove old entry if re-rendering
  if (store.getLayerById(LAYER_ID)) {
    store.removeLayer(LAYER_ID)
  }
  store.addLayer(layerDef)

  // Subscribe to store for visibility changes + removal detection (once)
  if (!unsubStore) {
    unsubStore = useMapStore.subscribe((state, prevState) => {
      const layer = state.layers.find((l) => l.id === LAYER_ID)
      const prevLayer = prevState.layers.find((l) => l.id === LAYER_ID)

      // Layer removed from store → clean up MapLibre resources
      if (!layer && prevLayer) {
        const m = mapEngine.getMap()
        if (m) {
          if (m.getLayer(LAYER_ID)) m.removeLayer(LAYER_ID)
          if (m.getSource(SOURCE_ID)) m.removeSource(SOURCE_ID)
        }
        mapEngine.untrackExternalLayer(LAYER_ID)
        mapEngine.untrackExternalSource(SOURCE_ID)
        cleanupStoreSub()
        lastRenderParams = null
        return
      }

      if (!layer) return
      if (prevLayer && layer.visible !== prevLayer.visible) {
        const m = mapEngine.getMap()
        if (m && m.getLayer(LAYER_ID)) {
          m.setLayoutProperty(LAYER_ID, 'visibility', layer.visible ? 'visible' : 'none')
        }
      }
    })
  }
}

function removeHeatmap(ctx?: ExtensionContext): void {
  const map = ctx?.map ?? mapEngine.getMap()
  if (map) {
    if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID)
    if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID)
  }
  mapEngine.untrackExternalLayer(LAYER_ID)
  mapEngine.untrackExternalSource(SOURCE_ID)
  // Clean up store entry
  const store = useMapStore.getState()
  if (store.getLayerById(LAYER_ID)) {
    store.removeLayer(LAYER_ID)
  }
  cleanupStoreSub()
  lastRenderParams = null
}

// Re-render after basemap switch (style.reload clears all non-basemap layers)
function attachStyleLoadListener(): void {
  const map = mapEngine.getMap()
  if (!map) return

  // Use map.on so it fires on EVERY style reload (not just the first).
  // Map.remove() cleans up all listeners, so no leak on destroy.
  map.on('style.load', () => {
    if (!lastRenderParams) return
    const m = mapEngine.getMap()
    if (!m) return

    // Re-add heatmap with saved params
    if (m.getLayer(LAYER_ID)) m.removeLayer(LAYER_ID)
    if (m.getSource(SOURCE_ID)) m.removeSource(SOURCE_ID)

    const { geojson, radius, intensity, opacity } = lastRenderParams
    m.addSource(SOURCE_ID, { type: 'geojson', data: geojson })
    m.addLayer({
      id: LAYER_ID,
      type: 'heatmap',
      source: SOURCE_ID,
      paint: buildHeatmapPaint(radius, intensity, opacity),
    })

    // Re-register with MapEngine
    mapEngine.trackExternalLayer(LAYER_ID)
    mapEngine.trackExternalSource(SOURCE_ID)

    // Restore visibility from store
    const layer = useMapStore.getState().layers.find((l) => l.id === LAYER_ID)
    if (layer && !layer.visible) {
      m.setLayoutProperty(LAYER_ID, 'visibility', 'none')
    }
  })
}

// Attach the style.load listener once the map is ready
mapEngine.onReady((ready) => {
  if (ready) attachStyleLoadListener()
})

registerExtension(heatmapExtension)
