/**
 * LayerPanel — ArcGIS/QGIS-style layer management sidebar panel.
 *
 * Features:
 * - Layer list with visibility toggles
 * - Active layer highlighting
 * - Layer reordering (drag & drop, HTML5 native)
 * - Layer removal
 * - Opacity / fill color / stroke color / stroke width / point radius controls
 * - Zoom-to-layer
 * - Add data button (file picker + drag & drop)
 */
import { useState, useCallback, useRef } from 'react'
import {
  Eye,
  EyeOff,
  Trash2,
  ChevronDown,
  ChevronRight,
  Plus,
  GripVertical,
  FileUp,
  MapPin,
  Maximize2,
  Palette,
  Loader2,
  BarChart3,
  ArrowLeftRight,
} from 'lucide-react'
import { useT } from '@/i18n'
import { useMapStore } from '@/stores/mapStore'
import { loadGeoFiles } from '@/services/geo'
import { mapEngine } from '@/features/map/engine/MapEngine'
import type { MapLayerDefinition, LayerStyle } from '@/services/geo'
import { LayerIcon } from './LayerIcon'
import { GraduatedStylePanel } from './GraduatedStylePanel'

export function LayerPanel() {
  const t = useT()
  const layers = useMapStore((s) => s.layers)
  const activeLayerId = useMapStore((s) => s.activeLayerId)
  const addLayers = useMapStore((s) => s.addLayers)
  const removeLayer = useMapStore((s) => s.removeLayer)
  const setActiveLayer = useMapStore((s) => s.setActiveLayer)
  const setLayerVisibility = useMapStore((s) => s.setLayerVisibility)
  const clearLayers = useMapStore((s) => s.clearLayers)
  const reorderLayers = useMapStore((s) => s.reorderLayers)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  // id of the layer currently being dragged for reordering; null when idle.
  // We distinguish this from file-system drag by guarding `onDragStart` on
  // the inner handle/row vs the panel-level `onDragOver` for files.
  const [dragLayerId, setDragLayerId] = useState<string | null>(null)
  const [dragOverLayerId, setDragOverLayerId] = useState<string | null>(null)
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)

  // ─── Add Data ─────────────────────────────────────────────────

  const handleAddFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      setIsLoadingFiles(true)
      try {
        const newLayers = await loadGeoFiles(files)
        if (newLayers.length > 0) {
          addLayers(newLayers)
          // Fit to first new layer
          const first = newLayers[0]
          if (first.data.kind === 'vector') {
            const { bbox } = first.data
            mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
          } else if (first.data.kind === 'raster') {
            const { bbox } = first.data
            mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
          }
        }
      } catch (err) {
        console.error('Failed to load files:', err)
      } finally {
        setIsLoadingFiles(false)
      }
    },
    [addLayers]
  )

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      handleAddFiles(files)
      // Reset input so the same file can be re-selected
      e.target.value = ''
    },
    [handleAddFiles]
  )

  // ─── File-drop (only fires when user drags from OS, not internal DnD) ──

  const handleFileDrop = useCallback(
    (e: React.DragEvent) => {
      // Internal layer reordering sets dataTransfer.types to ['text/layer-id'];
      // a file drop from the OS adds 'Files'. Use that as the gate.
      if (!e.dataTransfer.types.includes('Files')) return
      e.preventDefault()
      setIsDragOver(false)
      const files = Array.from(e.dataTransfer.files)
      handleAddFiles(files)
    },
    [handleAddFiles]
  )

  const handleFileDragOver = useCallback((e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const handleFileDragLeave = useCallback(() => {
    setIsDragOver(false)
  }, [])

  // ─── Layer Reordering (internal DnD) ──────────────────────────

  // The list on screen is rendered top→bottom (top = last in store array),
  // so we need to translate visible-drop-target into store-array index.
  const handleReorderDrop = useCallback(
    (targetLayerId: string) => {
      if (!dragLayerId || dragLayerId === targetLayerId) return
      const fromIdx = layers.findIndex((l) => l.id === dragLayerId)
      const toIdx = layers.findIndex((l) => l.id === targetLayerId)
      if (fromIdx === -1 || toIdx === -1) return
      reorderLayers(fromIdx, toIdx)
    },
    [dragLayerId, layers, reorderLayers]
  )

  // ─── Render ───────────────────────────────────────────────────

  return (
    <div
      className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none"
      onDrop={handleFileDrop}
      onDragOver={handleFileDragOver}
      onDragLeave={handleFileDragLeave}
    >
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-2">
        <span className="text-xs font-semibold text-text-secondary flex-1">{t.layers.title}</span>

        {/* Add data button */}
        <button
          onClick={() => fileInputRef.current?.click()}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title={t.layers.addData}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>

        {/* Clear all */}
        {layers.length > 0 && (
          <button
            onClick={clearLayers}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
            title={t.layers.removeAll}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".geojson,.json,.csv,.tsv,.shp,.dbf,.shx,.prj,.cpg,.kml,.gpkg,.tif,.tiff"
          onChange={handleFileInputChange}
          className="hidden"
        />
      </div>

      {/* Layer list */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {layers.length === 0 ? (
          <LayerEmptyState isDragOver={isDragOver} />
        ) : (
          <div className="py-1">
            {/* Render in reverse order (top layer first, like GIS convention) */}
            {[...layers].reverse().map((layer) => (
              <LayerItem
                key={layer.id}
                layer={layer}
                isActive={layer.id === activeLayerId}
                isDragging={dragLayerId === layer.id}
                isDragOver={dragOverLayerId === layer.id && dragLayerId !== layer.id}
                onSelect={() => setActiveLayer(layer.id)}
                onToggleVisibility={() => setLayerVisibility(layer.id, !layer.visible)}
                onRemove={() => removeLayer(layer.id)}
                onZoomTo={() => {
                  if (layer.data.kind === 'vector') {
                    const { bbox } = layer.data
                    mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
                  }
                }}
                onDragStart={() => setDragLayerId(layer.id)}
                onDragEnd={() => {
                  setDragLayerId(null)
                  setDragOverLayerId(null)
                }}
                onDragEnterLayer={() => setDragOverLayerId(layer.id)}
                onDropLayer={() => {
                  handleReorderDrop(layer.id)
                  setDragLayerId(null)
                  setDragOverLayerId(null)
                }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Loading overlay */}
      {isLoadingFiles && (
        <div className="absolute inset-0 bg-bg-primary/60 backdrop-blur-sm flex items-center justify-center z-30">
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="w-6 h-6 text-accent-primary animate-spin" />
            <p className="text-xs text-text-secondary">{t.layers.loadingLayers}</p>
          </div>
        </div>
      )}

      {/* File drop overlay */}
      {isDragOver && (
        <div className="absolute inset-0 bg-accent-primary/10 border-2 border-dashed border-accent-primary rounded-lg flex items-center justify-center z-20 pointer-events-none">
          <div className="text-center">
            <FileUp className="w-8 h-8 text-accent-primary mx-auto mb-2" />
            <p className="text-sm font-medium text-accent-primary">{t.layers.dropFiles}</p>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Layer Item ─────────────────────────────────────────────────

interface LayerItemProps {
  layer: MapLayerDefinition
  isActive: boolean
  isDragging: boolean
  isDragOver: boolean
  onSelect: () => void
  onToggleVisibility: () => void
  onRemove: () => void
  onZoomTo: () => void
  onDragStart: () => void
  onDragEnd: () => void
  onDragEnterLayer: () => void
  onDropLayer: () => void
}

function LayerItem({
  layer,
  isActive,
  isDragging,
  isDragOver,
  onSelect,
  onToggleVisibility,
  onRemove,
  onZoomTo,
  onDragStart,
  onDragEnd,
  onDragEnterLayer,
  onDropLayer,
}: LayerItemProps) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const [showClassification, setShowClassification] = useState(false)
  const setLayerOpacity = useMapStore((s) => s.setLayerOpacity)
  const updateLayerStyle = useMapStore((s) => s.updateLayerStyle)

  const featureCount = layer.data.kind === 'vector' ? layer.data.featureCount : 0
  const geometryType = layer.data.kind === 'vector' ? layer.data.geometryType : 'Raster'
  // Determine geometry-based type (independent of renderType, so graduated/categorized still know the geometry)
  const isPointGeom = layer.data.kind === 'vector' && (layer.data.geometryType === 'Point' || layer.data.geometryType === 'MultiPoint')
  const isFillGeom = layer.data.kind === 'vector' && (layer.data.geometryType === 'Polygon' || layer.data.geometryType === 'MultiPolygon')
  const isLineGeom = layer.data.kind === 'vector' && (layer.data.geometryType === 'LineString' || layer.data.geometryType === 'MultiLineString')
  const isClassified = layer.style.renderType === 'graduated' || layer.style.renderType === 'categorized'
  // For StylePanel compatibility
  const isPointLayer = layer.style.renderType === 'circle'
  const isFillLayer = layer.style.renderType === 'fill'

  const handleRowDragStart = useCallback(
    (e: React.DragEvent) => {
      // Use a custom mime type so the panel-level file-drop handler can
      // distinguish internal reorders from OS file drops.
      e.dataTransfer.setData('text/layer-id', layer.id)
      e.dataTransfer.effectAllowed = 'move'
      onDragStart()
    },
    [layer.id, onDragStart]
  )

  const handleRowDragOver = useCallback(
    (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes('text/layer-id')) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      onDragEnterLayer()
    },
    [onDragEnterLayer]
  )

  const handleRowDrop = useCallback(
    (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes('text/layer-id')) return
      e.preventDefault()
      e.stopPropagation()
      onDropLayer()
    },
    [onDropLayer]
  )

  return (
    <div
      onDragEnd={onDragEnd}
      onDragOver={handleRowDragOver}
      onDrop={handleRowDrop}
      className={`
        group relative mx-1 rounded-md transition-colors duration-100
        ${isActive ? 'bg-accent-primary/10' : 'hover:bg-bg-hover'}
        ${isDragging ? 'opacity-40' : ''}
      `}
    >
      {/* Drop-indicator line (above row when being dragged-over) */}
      {isDragOver && (
        <div className="absolute left-0 right-0 -top-[1px] h-0.5 bg-accent-primary rounded-full z-10 pointer-events-none" />
      )}

      {/* Active indicator */}
      {isActive && (
        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-5 bg-accent-primary rounded-r-full" />
      )}

      {/* Main row */}
      <div
        className="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer"
        onClick={onSelect}
      >
        {/* Drag handle — only this element is draggable */}
        <div
          draggable
          onDragStart={handleRowDragStart}
          className="w-3 h-4 flex items-center justify-center text-text-muted/40 hover:text-text-secondary cursor-grab active:cursor-grabbing shrink-0"
          title={t.layers.dragToReorder}
        >
          <GripVertical className="w-3 h-3" />
        </div>

        {/* Expand toggle */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            setExpanded(!expanded)
          }}
          className="w-4 h-4 flex items-center justify-center text-text-muted hover:text-text-secondary shrink-0"
        >
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
        </button>

        {/* Visibility toggle */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onToggleVisibility()
          }}
          className={`w-4 h-4 flex items-center justify-center shrink-0 transition-colors ${
            layer.visible ? 'text-text-secondary' : 'text-text-muted/40'
          }`}
          title={layer.visible ? t.layers.hideLayer : t.layers.showLayer}
        >
          {layer.visible ? (
            <Eye className="w-3.5 h-3.5" />
          ) : (
            <EyeOff className="w-3.5 h-3.5" />
          )}
        </button>

        {/* Layer icon (color swatch + geometry type) */}
        <LayerIcon
          geometryType={layer.data.kind === 'vector' ? layer.data.geometryType : undefined}
          color={layer.style.color}
          className="shrink-0"
        />

        {/* Layer name */}
        <span
          className={`text-xs truncate flex-1 ${
            isActive ? 'text-text-primary font-medium' : 'text-text-secondary'
          } ${!layer.visible ? 'opacity-50' : ''}`}
          title={layer.name}
        >
          {layer.name}
        </span>

        {/* Action buttons (visible on hover) */}
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => {
              e.stopPropagation()
              onZoomTo()
            }}
            className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
            title={t.layers.zoomToLayer}
          >
            <Maximize2 className="w-3 h-3" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onRemove()
            }}
            className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
            title={t.layers.removeLayer}
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div
          draggable={false}
          className="px-2 pb-2 pl-8 animate-slide-up"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onDragStart={(e) => { e.preventDefault(); e.stopPropagation() }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {/* Info row */}
          <div className="flex items-center gap-3 text-2xs text-text-muted mb-2">
            <span>{geometryType}</span>
            <span>·</span>
            <span>{featureCount.toLocaleString()} {t.layers.features}</span>
            <span>·</span>
            <span>{layer.data.crs}</span>
          </div>

          {/* Style panel — switches between single-color and classification summary */}
          {isClassified ? (
            <ClassifiedStyleSummary
              layer={layer}
              isPointGeom={isPointGeom}
              isFillGeom={isFillGeom}
              isLineGeom={isLineGeom}
              onStyleChange={(updates) => updateLayerStyle(layer.id, updates)}
              onOpacityChange={(v) => setLayerOpacity(layer.id, v)}
              onEditClassification={() => setShowClassification(true)}
              onResetToSingle={() => {
                let defaultType: LayerStyle['renderType'] = 'fill'
                if (layer.data.kind === 'vector') {
                  const gt = layer.data.geometryType
                  if (gt === 'Point' || gt === 'MultiPoint') defaultType = 'circle'
                  else if (gt === 'LineString' || gt === 'MultiLineString') defaultType = 'line'
                }
                updateLayerStyle(layer.id, {
                  renderType: defaultType,
                  graduated: undefined,
                  categorized: undefined,
                })
              }}
            />
          ) : (
            <>
              <StylePanel
                layer={layer}
                isPointLayer={isPointLayer}
                isFillLayer={isFillLayer}
                onStyleChange={(updates) => updateLayerStyle(layer.id, updates)}
                onOpacityChange={(v) => setLayerOpacity(layer.id, v)}
              />

              {/* Classification button — only for vector layers */}
              {layer.data.kind === 'vector' && (
                <button
                  onClick={() => setShowClassification(true)}
                  className="mt-2 w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-2xs font-medium text-accent-primary bg-accent-primary/10 hover:bg-accent-primary/20 rounded-lg transition-colors"
                >
                  <BarChart3 className="w-3 h-3" />
                  {t.layers.classificationRenderer}
                </button>
              )}
            </>
          )}

          {/* Classification panel (modal) */}
          {showClassification && (
            <GraduatedStylePanel
              layer={layer}
              onClose={() => setShowClassification(false)}
            />
          )}

          {/* Source info */}
          <div className="mt-2 text-2xs text-text-muted truncate" title={layer.meta.fileName}>
            📄 {layer.meta.fileName} ({formatFileSize(layer.meta.fileSize)})
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Classified Style Summary ───────────────────────────────────

/**
 * Compact summary panel shown when a layer uses graduated/categorized rendering.
 * Displays: legend preview + common paint controls (strokeWidth, radius, opacity)
 * + buttons to edit classification or switch back to single-color.
 */
interface ClassifiedStyleSummaryProps {
  layer: MapLayerDefinition
  isPointGeom: boolean
  isFillGeom: boolean
  isLineGeom: boolean
  onStyleChange: (updates: Partial<LayerStyle>) => void
  onOpacityChange: (v: number) => void
  onEditClassification: () => void
  onResetToSingle: () => void
}

function ClassifiedStyleSummary({
  layer,
  isPointGeom,
  isFillGeom,
  isLineGeom,
  onStyleChange,
  onOpacityChange,
  onEditClassification,
  onResetToSingle,
}: ClassifiedStyleSummaryProps) {
  const t = useT()
  const { style } = layer
  const isGraduated = style.renderType === 'graduated'
  const fillOpacity = style.fillOpacity ?? style.opacity

  // Build legend items
  const legendItems: { color: string; label: string }[] = []
  if (isGraduated && style.graduated) {
    const { breaks = [], palette = [] } = style.graduated
    for (let i = 0; i <= breaks.length; i++) {
      const color = palette[i] || palette[palette.length - 1] || '#9ca3af'
      const lo = i === 0 ? '−∞' : (breaks[i - 1]?.toFixed(2) ?? '?')
      const hi = i < breaks.length ? (breaks[i]?.toFixed(2) ?? '?') : '+∞'
      legendItems.push({ color, label: `${lo} – ${hi}` })
    }
  } else if (style.categorized?.colors) {
    for (const [val, color] of Object.entries(style.categorized.colors)) {
      legendItems.push({ color, label: val })
    }
  }

  return (
    <div className="space-y-1.5">
      {/* Header with mode badge */}
      <div className="flex items-center gap-1.5 text-2xs text-text-muted mt-0.5 mb-1">
        <BarChart3 className="w-3 h-3" />
        <span>{isGraduated ? t.layers.graduated : t.layers.categorized}</span>
        <span className="text-text-muted/40">·</span>
        <span className="truncate">
          {isGraduated ? style.graduated?.field : style.categorized?.field}
        </span>
      </div>

      {/* Compact legend preview (max 6 items, then "...more") */}
      {legendItems.length > 0 && (
        <div className="space-y-0.5 mb-2">
          {legendItems.slice(0, 6).map((item, i) => (
            <div key={i} className="flex items-center gap-2">
              <div
                className="w-4 h-3 rounded-sm border border-border shrink-0"
                style={{ backgroundColor: item.color }}
              />
              <span className="text-2xs text-text-secondary font-mono tabular-nums truncate">
                {item.label}
              </span>
            </div>
          ))}
          {legendItems.length > 6 && (
            <div className="text-2xs text-text-muted italic">
              …{legendItems.length - 6} {t.layers.more}
            </div>
          )}
        </div>
      )}

      {/* Common paint controls — always available regardless of classification */}
      {/* Stroke color — fills + points */}
      {(isFillGeom || isPointGeom) && (
        <StyleRow label={t.layers.stroke}>
          <ColorSwatch
            color={style.strokeColor}
            onChange={(strokeColor) => onStyleChange({ strokeColor })}
          />
          <HexInput
            value={style.strokeColor}
            onChange={(strokeColor) => onStyleChange({ strokeColor })}
          />
        </StyleRow>
      )}

      {/* Stroke width */}
      <StyleRow label={t.layers.width}>
        <input
          type="range"
          min={0}
          max={isPointGeom ? 5 : 10}
          step={0.5}
          value={style.strokeWidth}
          onChange={(e) =>
            onStyleChange({ strokeWidth: parseFloat(e.target.value) })
          }
          className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
        />
        <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
          {style.strokeWidth.toFixed(1)}
        </span>
      </StyleRow>

      {/* Point radius — point geometry only */}
      {isPointGeom && (
        <StyleRow label={t.layers.radius}>
          <input
            type="range"
            min={1}
            max={30}
            step={0.5}
            value={style.radius ?? 5}
            onChange={(e) => onStyleChange({ radius: parseFloat(e.target.value) })}
            className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
          />
          <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
            {(style.radius ?? 5).toFixed(1)}
          </span>
        </StyleRow>
      )}

      {/* Fill-opacity — polygon geometry only */}
      {isFillGeom && (
        <StyleRow label={t.layers.fillAlpha}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={fillOpacity}
            onChange={(e) =>
              onStyleChange({ fillOpacity: parseFloat(e.target.value) })
            }
            className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
          />
          <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
            {Math.round(fillOpacity * 100)}%
          </span>
        </StyleRow>
      )}

      {/* Global opacity */}
      <StyleRow label={t.layers.opacity}>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={style.opacity}
          onChange={(e) => onOpacityChange(parseFloat(e.target.value))}
          className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
        />
        <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
          {Math.round(style.opacity * 100)}%
        </span>
      </StyleRow>

      {/* Action buttons */}
      <div className="flex items-center gap-2 mt-2">
        <button
          onClick={onEditClassification}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-2xs font-medium text-accent-primary bg-accent-primary/10 hover:bg-accent-primary/20 rounded-lg transition-colors"
        >
          <BarChart3 className="w-3 h-3" />
          {t.layers.editClassification}
        </button>
        <button
          onClick={onResetToSingle}
          className="flex items-center justify-center gap-1 px-2 py-1.5 text-2xs text-text-muted hover:text-text-secondary bg-bg-secondary hover:bg-bg-hover rounded-lg transition-colors"
          title={t.layers.switchToSingle}
        >
          <ArrowLeftRight className="w-3 h-3" />
          {t.layers.single}
        </button>
      </div>
    </div>
  )
}

// ─── Style Panel ────────────────────────────────────────────────

interface StylePanelProps {
  layer: MapLayerDefinition
  isPointLayer: boolean
  isFillLayer: boolean
  onStyleChange: (updates: Partial<LayerStyle>) => void
  onOpacityChange: (v: number) => void
}

/**
 * Fine-grained paint controls: fill color, stroke color, stroke width,
 * point radius, and opacity. Every input feeds straight into the store,
 * which MapView's layer-sync effect then pushes down to MapLibre via
 * `mapEngine.updateLayerPaint`. That's why we don't need any local
 * "apply" button — the preview is literally the live state.
 *
 * Render-type matrix (see `services/geo/types.ts`):
 *   fill   → polygon layers; show fill color + stroke color + stroke width
 *   circle → point layers;   show fill color + stroke color + stroke width + radius
 *   line   → polyline layers; show color + stroke width (fill color is reused as line color)
 *   raster → no styling controls today (will come with the raster sprint)
 */
function StylePanel({
  layer,
  isPointLayer,
  isFillLayer,
  onStyleChange,
  onOpacityChange,
}: StylePanelProps) {
  const t = useT()
  const { style } = layer
  const isRaster = style.renderType === 'raster'

  // Fill-opacity distinct-from-opacity handling: for fills MapLibre reads
  // `fill-opacity`, for everything else `opacity`. We expose a single
  // "Opacity" slider that writes to `style.opacity` (via mapStore's
  // setLayerOpacity), and a separate "Fill α" slider only for polygons.
  const fillOpacity = style.fillOpacity ?? style.opacity

  return (
    <div className="space-y-1.5">
      {/* Header */}
      <div className="flex items-center gap-1.5 text-2xs text-text-muted mt-0.5 mb-1">
        <Palette className="w-3 h-3" />
        <span>{t.layers.style}</span>
      </div>

      {/* Main color (fill for polygons/points, line for polylines) */}
      {!isRaster && (
        <StyleRow label={isFillLayer || isPointLayer ? t.layers.fill : t.layers.color}>
          <ColorSwatch
            color={style.color}
            onChange={(color) => onStyleChange({ color })}
          />
          <HexInput
            value={style.color}
            onChange={(color) => onStyleChange({ color })}
          />
        </StyleRow>
      )}

      {/* Stroke color — fills + circles */}
      {(isFillLayer || isPointLayer) && (
        <StyleRow label={t.layers.stroke}>
          <ColorSwatch
            color={style.strokeColor}
            onChange={(strokeColor) => onStyleChange({ strokeColor })}
          />
          <HexInput
            value={style.strokeColor}
            onChange={(strokeColor) => onStyleChange({ strokeColor })}
          />
        </StyleRow>
      )}

      {/* Stroke width (always unless raster) */}
      {!isRaster && (
        <StyleRow label={t.layers.width}>
          <input
            type="range"
            min={0}
            max={isPointLayer ? 5 : 10}
            step={0.5}
            value={style.strokeWidth}
            onChange={(e) =>
              onStyleChange({ strokeWidth: parseFloat(e.target.value) })
            }
            className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
          />
          <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
            {style.strokeWidth.toFixed(1)}
          </span>
        </StyleRow>
      )}

      {/* Point radius — circle only */}
      {isPointLayer && (
        <StyleRow label={t.layers.radius}>
          <input
            type="range"
            min={1}
            max={30}
            step={0.5}
            value={style.radius ?? 5}
            onChange={(e) => onStyleChange({ radius: parseFloat(e.target.value) })}
            className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
          />
          <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
            {(style.radius ?? 5).toFixed(1)}
          </span>
        </StyleRow>
      )}

      {/* Fill-opacity — fills only (separate from layer opacity) */}
      {isFillLayer && (
        <StyleRow label={t.layers.fillAlpha}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={fillOpacity}
            onChange={(e) =>
              onStyleChange({ fillOpacity: parseFloat(e.target.value) })
            }
            className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
          />
          <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
            {Math.round(fillOpacity * 100)}%
          </span>
        </StyleRow>
      )}

      {/* Global opacity (applies to everything) */}
      <StyleRow label={t.layers.opacity}>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={style.opacity}
          onChange={(e) => onOpacityChange(parseFloat(e.target.value))}
          className="flex-1 h-1 accent-accent-primary cursor-pointer min-w-0"
        />
        <span className="text-2xs text-text-muted w-10 text-right tabular-nums shrink-0">
          {Math.round(style.opacity * 100)}%
        </span>
      </StyleRow>
    </div>
  )
}

function StyleRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div
      className="flex items-center gap-2"
      draggable={false}
      onDragStart={(e) => { e.preventDefault(); e.stopPropagation() }}
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <span className="text-2xs text-text-muted w-12 shrink-0">{label}</span>
      <div className="flex-1 flex items-center gap-2 min-w-0">
        {children}
      </div>
    </div>
  )
}

/**
 * Small color swatch that opens the native color picker when clicked.
 * Using `<input type="color">` avoids a custom picker dependency and
 * hands us a big, reliable, accessible OS-native palette for free.
 */
function ColorSwatch({
  color,
  onChange,
}: {
  color: string
  onChange: (color: string) => void
}) {
  // Normalise to #rrggbb — the color input rejects rgb()/rgba()/named
  // colors silently, which produces confusing "picker won't open" bugs.
  const normalised = normaliseHex(color)
  return (
    <label
      className="w-5 h-5 rounded border border-border shrink-0 cursor-pointer relative overflow-hidden"
      style={{ backgroundColor: color }}
      title={color}
    >
      <input
        type="color"
        value={normalised}
        onChange={(e) => onChange(e.target.value)}
        className="absolute inset-0 opacity-0 cursor-pointer"
      />
    </label>
  )
}

function HexInput({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const [local, setLocal] = useState(value)
  // Keep local in sync when the upstream value changes via the picker.
  // We only commit on blur/Enter so typing doesn't spam the store with
  // partial hex strings that MapLibre would reject.
  if (value !== local && document.activeElement?.tagName !== 'INPUT') {
    // cheap way to reset without an effect dependency loop
    setTimeout(() => setLocal(value), 0)
  }
  return (
    <input
      type="text"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => {
        if (/^#[0-9a-f]{3,8}$/i.test(local)) onChange(local)
        else setLocal(value)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          if (/^#[0-9a-f]{3,8}$/i.test(local)) onChange(local)
          else setLocal(value)
          ;(e.target as HTMLInputElement).blur()
        }
      }}
      className="w-16 bg-bg-tertiary text-2xs font-mono text-text-primary px-1.5 py-0.5 rounded outline-none border border-border focus:border-accent-primary"
    />
  )
}

function normaliseHex(color: string): string {
  if (/^#[0-9a-f]{6}$/i.test(color)) return color.toLowerCase()
  if (/^#[0-9a-f]{3}$/i.test(color)) {
    // #rgb → #rrggbb
    return (
      '#' +
      color
        .slice(1)
        .split('')
        .map((c) => c + c)
        .join('')
        .toLowerCase()
    )
  }
  if (/^#[0-9a-f]{8}$/i.test(color)) {
    // #rrggbbaa → drop alpha for the color picker (we have a slider for it)
    return color.slice(0, 7).toLowerCase()
  }
  // Unknown format — fall back to a safe default so <input type="color">
  // doesn't reject it silently.
  return '#3b82f6'
}

// ─── Empty State ────────────────────────────────────────────────

function LayerEmptyState({ isDragOver }: { isDragOver: boolean }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-geo/10 flex items-center justify-center mx-auto mb-3">
          <MapPin className="w-5 h-5 text-accent-geo/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">{t.layers.noLayers}</p>
        <p className="text-2xs text-text-muted/70">
          {t.layers.noLayersHint}
        </p>
        {isDragOver && (
          <p className="text-2xs text-accent-primary mt-2">{t.layers.dropToAdd}</p>
        )}
      </div>
    </div>
  )
}

// ─── Helpers ────────────────────────────────────────────────────

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
