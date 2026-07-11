/**
 * CommandBus — routes JSON-RPC notifications from the Python backend
 * into the frontend's imperative subsystems (MapEngine, stores, etc.).
 *
 * This is the consumer of pythonClient.onNotification. Every Python-side
 * display tools (add_layer, fly_to, ...) emit `map.*` notifications;
 * this bus dispatches them.
 *
 * Commands coming from Python:
 *   map.addLayer        { layer_id, name, geojson, color?, opacity? }
 *   map.removeLayer     { layer_id }
 *   map.flyTo           { center?: [lng,lat], zoom?, bbox?: [minx,miny,maxx,maxy], duration? }
 *   map.setBasemap      { basemap_id }
 *   map.updateLayerStyle{ layer_id, color?, opacity?, visible? }
 */

import { pythonClient } from './pythonClient'
import { useMapStore } from '@/stores/mapStore'
import { mapEngine } from '@/features/map/engine/MapEngine'
import {
  BUILTIN_BASEMAPS,
  getDefaultStyle,
  makeHandledVectorData,
  shouldHandleLayer,
  type MapLayerDefinition,
  type DataSourceMeta,
  type ParsedVectorData,
  type GeoJSONFeatureCollection,
  type GeoJSONFeature,
  type BBox,
  detectGeometryType,
} from './geo'

type CommandHandler = (params: any) => Promise<void> | void

const handlers: Record<string, CommandHandler> = {
  // ── Layer management ─────────────────────────────────────────────
  'map.addLayer': (params) => {
    const { layer_id, name, geojson, color, opacity } = params || {}
    if (!layer_id || !geojson) {
      console.warn('[CommandBus] map.addLayer missing layer_id or geojson', params)
      return
    }

    // Normalize whatever shape the agent sent into a proper
    // FeatureCollection so downstream code (MapEngine, LayerPanel,
    // AssetExplorer) sees the same contract as file-loaded layers.
    const fc = normalizeToFeatureCollection(geojson)
    if (!fc || fc.features.length === 0) {
      console.warn('[CommandBus] map.addLayer: empty/invalid geojson', params)
      return
    }

    const geometryType = detectGeometryType(fc)
    const bbox = computeBBox(fc)
    const displayName = typeof name === 'string' && name ? name : layer_id

    const inlineSize = estimateGeoJSONBytes(fc)
    let data: ParsedVectorData = {
      kind: 'vector',
      geojson: fc,
      geometryType,
      featureCount: fc.features.length,
      bbox,
      crs: 'EPSG:4326',
      fields: [],
    }
    if (shouldHandleLayer(inlineSize)) {
      data = makeHandledVectorData(data, {
        handleId: `vector:${layer_id}`,
        sizeBytes: inlineSize,
      })
    }

    // Agent-created layers aren't file-backed, but the UI assumes every
    // MapLayerDefinition carries a DataSourceMeta. We synthesize a
    // stable virtual meta so guards like `l.meta.fileName` never trip.
    const meta: DataSourceMeta = {
      fileName: `${displayName}.geojson`,
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: inlineSize,
    }

    const style = getDefaultStyle(geometryType)
    if (typeof color === 'string' && color) {
      style.color = color
    }
    if (typeof opacity === 'number') {
      style.opacity = opacity
    }

    const definition: MapLayerDefinition = {
      id: layer_id,
      name: displayName,
      sourceType: 'geojson',
      visible: true,
      style,
      data,
      meta,
      addedAt: Date.now(),
    }

    useMapStore.getState().addLayer(definition)
  },

  'map.removeLayer': (params) => {
    const { layer_id } = params || {}
    if (!layer_id) return
    useMapStore.getState().removeLayer(layer_id)
  },

  'map.flyTo': (params) => {
    const { center, zoom, bbox } = params || {}

    if (Array.isArray(bbox) && bbox.length === 4) {
      mapEngine.fitBounds(bbox as [number, number, number, number])
      return
    }
    if (Array.isArray(center) && center.length === 2) {
      mapEngine.flyTo(center as [number, number], zoom)
    }
  },

  'map.setBasemap': (params) => {
    const { basemap_id } = params || {}
    if (!basemap_id) return
    const basemap = BUILTIN_BASEMAPS.find((b) => b.id === basemap_id)
    if (basemap) {
      useMapStore.getState().setBasemap(basemap)
    } else {
      console.warn('[CommandBus] Unknown basemap_id:', basemap_id)
    }
  },

  'map.updateLayerStyle': (params) => {
    const { layer_id, color, opacity, visible } = params || {}
    if (!layer_id) return
    const store = useMapStore.getState()
    if (typeof visible === 'boolean') {
      store.setLayerVisibility(layer_id, visible)
    }
    if (typeof opacity === 'number') {
      store.setLayerOpacity(layer_id, opacity)
    }
    if (typeof color === 'string') {
      store.updateLayerStyle(layer_id, { color } as any)
    }
  },

  // ── User Instructions ───────────────────────────────────────────
  'user_instructions.updated': async (params) => {
    const { content } = params || {}
    if (typeof content !== 'string') return
    const { useSettingsStore } = await import('@/stores/settingsStore')
    useSettingsStore.getState().updateAgent({ customInstructions: content })
  },
}

// ─── Helpers ───────────────────────────────────────────────────────

/**
 * Accept whatever the agent sent (FeatureCollection, single Feature, or a
 * bare geometry object) and return a FeatureCollection. Returns null for
 * unrecognized shapes so the caller can log & skip instead of crashing.
 */
function normalizeToFeatureCollection(raw: any): GeoJSONFeatureCollection | null {
  if (!raw || typeof raw !== 'object') return null

  if (raw.type === 'FeatureCollection' && Array.isArray(raw.features)) {
    return raw as GeoJSONFeatureCollection
  }

  if (raw.type === 'Feature' && raw.geometry) {
    return { type: 'FeatureCollection', features: [raw as GeoJSONFeature] }
  }

  if (typeof raw.type === 'string' && raw.coordinates) {
    // Bare geometry — wrap it.
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', geometry: raw, properties: {} }],
    }
  }

  return null
}

function computeBBox(fc: GeoJSONFeatureCollection): BBox {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  const visit = (coords: any): void => {
    if (
      Array.isArray(coords) &&
      typeof coords[0] === 'number' &&
      typeof coords[1] === 'number'
    ) {
      const [x, y] = coords
      if (x < minX) minX = x
      if (y < minY) minY = y
      if (x > maxX) maxX = x
      if (y > maxY) maxY = y
      return
    }
    if (Array.isArray(coords)) coords.forEach(visit)
  }

  for (const feature of fc.features) {
    if (feature.geometry?.coordinates) visit(feature.geometry.coordinates)
  }

  if (!isFinite(minX)) {
    // Empty or degenerate — fall back to world extent so MapEngine still renders.
    return { minX: -180, minY: -90, maxX: 180, maxY: 90 }
  }
  return { minX, minY, maxX, maxY }
}

function estimateGeoJSONBytes(fc: GeoJSONFeatureCollection): number {
  try {
    return new TextEncoder().encode(JSON.stringify(fc)).byteLength
  } catch {
    return 0
  }
}

// ─── Installation ──────────────────────────────────────────────────
let installed = false

export function installCommandBus(): void {
  if (installed) return
  installed = true

  pythonClient.onNotification((method, params) => {
    const handler = handlers[method]
    if (!handler) return  // Not our command; let other consumers (chatStore) handle it.
    try {
      const r = handler(params)
      if (r instanceof Promise) {
        r.catch((err) => console.error(`[CommandBus] ${method} failed:`, err))
      }
    } catch (err) {
      console.error(`[CommandBus] ${method} threw:`, err)
    }
  })
}
