/**
 * BoxSelectResultPanel — A floating list panel that displays features
 * selected via box-select mode.
 *
 * Design:
 * - Dark glass aesthetic matching the project's design language
 * - List mode: each feature is a row, click to select & highlight on map
 * - Selected feature shows detailed attributes in an expandable section
 * - Supports map highlight via temporary GeoJSON overlay layer
 */
import { useState, useEffect, useMemo, useCallback } from 'react'
import maplibregl from 'maplibre-gl'
import {
  X,
  Layers,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Crosshair,
} from 'lucide-react'
import { mapEngine } from '../engine/MapEngine'

// ─── Types ──────────────────────────────────────────────────────────

export interface BoxSelectFeatureInfo {
  /** Layer display name */
  layerName: string
  /** Geometry type (Point, LineString, Polygon, etc.) */
  geometryType: string
  /** Feature properties (key-value pairs) */
  properties: Record<string, any>
  /** Optional coordinates for point features */
  coordinates?: [number, number]
  /** Source id for feature-state highlight */
  sourceId: string
  /** Source layer (for vector tile sources) */
  sourceLayer?: string
  /** Feature id for feature-state highlight */
  featureId?: string | number
  /** Render layer id (for querying) */
  renderLayerId: string
  /** Raw GeoJSON geometry for highlight layer rendering */
  geometry: GeoJSON.Geometry
}

interface BoxSelectResultPanelProps {
  /** Array of box-selected features */
  features: BoxSelectFeatureInfo[]
  /** Whether the panel is visible */
  visible: boolean
  /** Callback when panel is closed */
  onClose: () => void
}

// ─── Geometry type badge colors ─────────────────────────────────────

const GEOM_COLORS: Record<string, string> = {
  Point: 'bg-accent-primary/20 text-blue-400',
  MultiPoint: 'bg-accent-primary/20 text-blue-400',
  LineString: 'bg-accent-success/20 text-green-400',
  MultiLineString: 'bg-accent-success/20 text-green-400',
  Polygon: 'bg-accent-warning/20 text-amber-400',
  MultiPolygon: 'bg-accent-warning/20 text-amber-400',
}

// ─── Value formatting ───────────────────────────────────────────────

function formatValue(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    return v.toFixed(6).replace(/\.?0+$/, '')
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'string') {
    return v.length > 80 ? v.slice(0, 77) + '…' : v
  }
  try {
    const s = JSON.stringify(v)
    return s.length > 80 ? s.slice(0, 77) + '…' : s
  } catch {
    return String(v)
  }
}

/** Pick a short label for a feature from its properties */
function featureLabel(props: Record<string, any>, index: number): string {
  // Try common name fields
  const nameKeys = ['name', 'NAME', 'Name', 'label', 'LABEL', 'title', 'TITLE', 'id', 'ID', 'fid', 'FID']
  for (const k of nameKeys) {
    if (props[k] !== undefined && props[k] !== null && props[k] !== '') {
      const val = String(props[k])
      return val.length > 30 ? val.slice(0, 27) + '…' : val
    }
  }
  // Fallback: first non-internal string/number property
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith('_') || k === 'cluster' || k === 'cluster_id') continue
    if (typeof v === 'string' || typeof v === 'number') {
      const val = String(v)
      return val.length > 30 ? val.slice(0, 27) + '…' : val
    }
  }
  return `Feature #${index + 1}`
}

// ─── Component ──────────────────────────────────────────────────────

export function BoxSelectResultPanel({
  features,
  visible,
  onClose,
}: BoxSelectResultPanelProps) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  // Reset selection when features change
  useEffect(() => {
    setSelectedIndex(null)
    setExpandedIndex(null)
  }, [features])

  // Clear feature-state highlights when panel is closed or features are cleared
  useEffect(() => {
    if (!visible || features.length === 0) {
      clearFeatureStateHighlights(features)
    }
  }, [visible, features])

  // Highlight selected feature on map via temporary GeoJSON layer
  useEffect(() => {
    const map = mapEngine.getMap()
    if (!map) return

    // Remove previous highlight layer/source
    removeHighlightLayer(map)

    // Add highlight for selected feature
    if (selectedIndex !== null && selectedIndex < features.length) {
      const f = features[selectedIndex]
      addHighlightLayer(map, f)
    }

    return () => {
      // Cleanup on unmount or selection change
      const m = mapEngine.getMap()
      if (m) removeHighlightLayer(m)
    }
  }, [selectedIndex, features])

  // Group features by layer
  const groupedFeatures = useMemo(() => {
    const groups = new Map<string, { features: BoxSelectFeatureInfo[]; indices: number[] }>()
    features.forEach((f, i) => {
      const existing = groups.get(f.layerName)
      if (existing) {
        existing.features.push(f)
        existing.indices.push(i)
      } else {
        groups.set(f.layerName, { features: [f], indices: [i] })
      }
    })
    return groups
  }, [features])

  // Copy value to clipboard
  const handleCopy = useCallback((key: string, value: any) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    navigator.clipboard.writeText(text).then(() => {
      setCopiedKey(key)
      setTimeout(() => setCopiedKey(null), 1500)
    })
  }, [])

  // Zoom to a feature's bounding box on the map
  const zoomToFeature = useCallback((feature: BoxSelectFeatureInfo) => {
    const map = mapEngine.getMap()
    if (!map || !feature.geometry) return

    const bounds = new maplibregl.LngLatBounds()
    const expandCoords = (coords: any) => {
      if (typeof coords[0] === 'number') {
        bounds.extend(coords as [number, number])
      } else {
        for (const c of coords) expandCoords(c)
      }
    }

    const geom = feature.geometry as any
    if (geom.coordinates) expandCoords(geom.coordinates)

    if (!bounds.isEmpty()) {
      // For point features, use flyTo instead of fitBounds
      if (feature.geometryType === 'Point' || feature.geometryType === 'MultiPoint') {
        map.flyTo({ center: bounds.getCenter(), zoom: Math.max(map.getZoom(), 14), duration: 600 })
      } else {
        map.fitBounds(bounds, { padding: 80, maxZoom: 16, duration: 600 })
      }
    }
  }, [])

  // Handle feature click — select, expand & zoom
  const handleFeatureClick = useCallback((globalIndex: number) => {
    setSelectedIndex((prev) => {
      if (prev === globalIndex) {
        // Deselect
        return null
      }
      return globalIndex
    })
    setExpandedIndex((prev) => {
      if (prev === globalIndex) return null
      return globalIndex
    })

    // Zoom to the clicked feature
    if (globalIndex < features.length) {
      zoomToFeature(features[globalIndex])
    }
  }, [features, zoomToFeature])

  // Handle close: clear feature-state highlights before calling onClose
  const handleClose = useCallback(() => {
    clearFeatureStateHighlights(features)
    onClose()
  }, [features, onClose])

  if (!visible || features.length === 0) return null

  return (
    <div className="absolute bottom-3 left-3 right-3 z-20 animate-slide-up">
      <div
        className="
          w-full max-h-[220px]
          glass rounded-xl panel-shadow
          flex flex-col overflow-hidden
          border border-border
        "
      >
        {/* ─── Header ─────────────────────────────────────────── */}
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border shrink-0">
          <div className="w-6 h-6 rounded-md bg-indigo-500/15 flex items-center justify-center">
            <Crosshair className="w-3.5 h-3.5 text-indigo-400" />
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-xs font-semibold text-text-primary">
              Box Select Results
            </h3>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-[10px] text-text-muted">
                {features.length} feature{features.length !== 1 ? 's' : ''} in{' '}
                {groupedFeatures.size} layer{groupedFeatures.size !== 1 ? 's' : ''}
              </span>
            </div>
          </div>

          <button
            onClick={handleClose}
            className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
            title="Close"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* ─── Feature List (horizontal scrollable) ─────────── */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="flex flex-wrap gap-0">
            {Array.from(groupedFeatures.entries()).map(([layerName, group]) => (
              <div key={layerName} className="w-full">
                {/* Layer group header */}
                <div className="sticky top-0 z-10 flex items-center gap-1.5 px-3 py-1 bg-bg-secondary/95 backdrop-blur-sm border-b border-border/50">
                  <Layers className="w-3 h-3 text-text-muted shrink-0" />
                  <span className="text-[11px] font-medium text-text-secondary truncate">
                    {layerName}
                  </span>
                  <span className="text-[10px] text-text-muted ml-auto shrink-0">
                    {group.features.length}
                  </span>
                </div>

                {/* Feature items — compact rows */}
                {group.features.map((feature, localIdx) => {
                  const globalIdx = group.indices[localIdx]
                  const isSelected = selectedIndex === globalIdx
                  const isExpanded = expandedIndex === globalIdx
                  const geomColor = GEOM_COLORS[feature.geometryType] ?? 'bg-bg-tertiary text-text-muted'

                  return (
                    <div key={globalIdx}>
                      {/* Feature row */}
                      <button
                        onClick={() => handleFeatureClick(globalIdx)}
                        className={`
                          w-full flex items-center gap-2 px-3 py-1.5 text-left transition-all
                          ${isSelected
                            ? 'bg-indigo-500/10 border-l-2 border-l-indigo-400'
                            : 'border-l-2 border-l-transparent hover:bg-bg-hover/50'
                          }
                        `}
                      >
                        {/* Expand chevron */}
                        <div className="w-3.5 shrink-0">
                          {isExpanded ? (
                            <ChevronDown className="w-3 h-3 text-text-muted" />
                          ) : (
                            <ChevronRight className="w-3 h-3 text-text-muted" />
                          )}
                        </div>

                        {/* Geometry type badge */}
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0 ${geomColor}`}
                        >
                          {feature.geometryType.replace('Multi', 'M-')}
                        </span>

                        {/* Feature label */}
                        <span className={`text-xs truncate flex-1 ${isSelected ? 'text-text-primary font-medium' : 'text-text-secondary'}`}>
                          {featureLabel(feature.properties, globalIdx)}
                        </span>

                        {/* Coordinates hint for points */}
                        {feature.coordinates && (
                          <span className="text-[9px] text-text-muted font-mono shrink-0">
                            {feature.coordinates[1].toFixed(2)}, {feature.coordinates[0].toFixed(2)}
                          </span>
                        )}
                      </button>

                      {/* Expanded attribute table */}
                      {isExpanded && (
                        <div className="bg-bg-tertiary/40 border-y border-border/30 max-h-[80px] overflow-y-auto scrollbar-thin">
                          <table className="w-full text-xs">
                            <tbody>
                              {Object.entries(feature.properties)
                                .filter(
                                  ([k]) =>
                                    !k.startsWith('_') &&
                                    k !== 'cluster' &&
                                    k !== 'cluster_id' &&
                                    k !== 'point_count' &&
                                    k !== 'point_count_abbreviated',
                                )
                                .map(([key, value], idx) => (
                                  <tr
                                    key={key}
                                    className={`
                                      group transition-colors hover:bg-bg-hover/50
                                      ${idx % 2 === 0 ? 'bg-transparent' : 'bg-bg-tertiary/20'}
                                    `}
                                  >
                                    <td className="pl-10 pr-2 py-0.5 text-text-muted font-medium break-all align-top w-[35%]">
                                      {key}
                                    </td>
                                    <td className="px-2 py-0.5 text-text-primary break-words align-top font-mono text-[11px]">
                                      {formatValue(value)}
                                    </td>
                                    <td className="pr-2 align-top pt-0.5 w-6">
                                      <button
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          handleCopy(key, value)
                                        }}
                                        className="w-5 h-5 rounded flex items-center justify-center text-text-muted/0 group-hover:text-text-muted hover:!text-accent-primary hover:bg-accent-primary/10 transition-all"
                                        title="Copy value"
                                      >
                                        {copiedKey === key ? (
                                          <Check className="w-3 h-3 text-accent-success" />
                                        ) : (
                                          <Copy className="w-3 h-3" />
                                        )}
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Map highlight helpers (temporary GeoJSON layer) ────────────────

const HIGHLIGHT_SOURCE_ID = '__boxselect-highlight-src'
const HIGHLIGHT_LAYER_FILL = '__boxselect-highlight-fill'
const HIGHLIGHT_LAYER_LINE = '__boxselect-highlight-line'
const HIGHLIGHT_LAYER_CIRCLE = '__boxselect-highlight-circle'

function removeHighlightLayer(map: maplibregl.Map): void {
  try {
    if (map.getLayer(HIGHLIGHT_LAYER_FILL)) map.removeLayer(HIGHLIGHT_LAYER_FILL)
    if (map.getLayer(HIGHLIGHT_LAYER_LINE)) map.removeLayer(HIGHLIGHT_LAYER_LINE)
    if (map.getLayer(HIGHLIGHT_LAYER_CIRCLE)) map.removeLayer(HIGHLIGHT_LAYER_CIRCLE)
    if (map.getSource(HIGHLIGHT_SOURCE_ID)) map.removeSource(HIGHLIGHT_SOURCE_ID)
  } catch {
    // ignore
  }
}

/** Clear feature-state hover highlights for all box-selected features */
function clearFeatureStateHighlights(features: BoxSelectFeatureInfo[]): void {
  const map = mapEngine.getMap()
  if (!map) return
  for (const f of features) {
    if (f.featureId !== undefined && f.featureId !== null) {
      try {
        // Guard: source may have been removed & re-added after style/render changes
        if (!map.getSource(f.sourceId)) continue
        map.setFeatureState(
          { source: f.sourceId, sourceLayer: f.sourceLayer, id: f.featureId },
          { hover: false },
        )
      } catch {
        /* no-op */
      }
    }
  }
}

function addHighlightLayer(map: maplibregl.Map, feature: BoxSelectFeatureInfo): void {
  if (!feature.geometry) return

  const geojson: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: feature.geometry,
        properties: {},
      },
    ],
  }

  map.addSource(HIGHLIGHT_SOURCE_ID, {
    type: 'geojson',
    data: geojson,
  })

  const geomType = feature.geometryType

  // Add appropriate highlight layers based on geometry type
  if (geomType === 'Polygon' || geomType === 'MultiPolygon') {
    map.addLayer({
      id: HIGHLIGHT_LAYER_FILL,
      type: 'fill',
      source: HIGHLIGHT_SOURCE_ID,
      paint: {
        'fill-color': '#6366f1',
        'fill-opacity': 0.25,
      },
    })
    map.addLayer({
      id: HIGHLIGHT_LAYER_LINE,
      type: 'line',
      source: HIGHLIGHT_SOURCE_ID,
      paint: {
        'line-color': '#818cf8',
        'line-width': 3,
        'line-opacity': 0.9,
      },
    })
  } else if (geomType === 'LineString' || geomType === 'MultiLineString') {
    map.addLayer({
      id: HIGHLIGHT_LAYER_LINE,
      type: 'line',
      source: HIGHLIGHT_SOURCE_ID,
      paint: {
        'line-color': '#818cf8',
        'line-width': 5,
        'line-opacity': 0.9,
      },
    })
  } else if (geomType === 'Point' || geomType === 'MultiPoint') {
    map.addLayer({
      id: HIGHLIGHT_LAYER_CIRCLE,
      type: 'circle',
      source: HIGHLIGHT_SOURCE_ID,
      paint: {
        'circle-radius': 10,
        'circle-color': '#6366f1',
        'circle-opacity': 0.4,
        'circle-stroke-color': '#818cf8',
        'circle-stroke-width': 3,
        'circle-stroke-opacity': 0.9,
      },
    })
  }
}
