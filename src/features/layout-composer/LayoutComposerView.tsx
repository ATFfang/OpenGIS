import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import {
  ChevronDown,
  Compass,
  Download,
  List,
  Map,
  Move,
  RefreshCw,
  RotateCcw,
  Ruler,
  Trash2,
  Type,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import type { MapLayerDefinition } from '@/services/geo'
import { getCategorizedCache } from '@/features/map/renderers/categorizedRenderer'
import { getGraduatedCache } from '@/features/map/renderers/graduatedRenderer'
import { useMapStore } from '@/stores/mapStore'
import { exportLayoutAsPng } from './layoutExport'
import { captureCurrentMapSnapshot } from './mapSnapshot'
import { PAGE_PRESETS, useLayoutComposerStore } from './layoutComposerStore'
import { getLayoutAspect, getLayoutDesignWidth, scaleLayoutValue } from './layoutMetrics'
import type { LayoutElement, LayoutElementFrame, LayoutElementStyle, LayoutElementType, LayoutMapView, LayoutPage } from './types'

const ELEMENT_TOOLS: Array<{ type: LayoutElementType; icon: typeof Map; label: string }> = [
  { type: 'map-frame', icon: Map, label: '地图框' },
  { type: 'scale-bar', icon: Ruler, label: '比例尺' },
  { type: 'north-arrow', icon: Compass, label: '指北针' },
  { type: 'legend', icon: List, label: '图例' },
  { type: 'text', icon: Type, label: '文本' },
]

const CANVAS_PADDING = 80
const MIN_PAGE_WIDTH = 180

export function LayoutComposerView() {
  const page = useLayoutComposerStore((s) => s.page)
  const elements = useLayoutComposerStore((s) => s.elements)
  const selectedElementId = useLayoutComposerStore((s) => s.selectedElementId)
  const zoom = useLayoutComposerStore((s) => s.zoom)
  const mapScaleDenominator = useLayoutComposerStore((s) => s.mapScaleDenominator)
  const mapSnapshotUrl = useLayoutComposerStore((s) => s.mapSnapshotUrl)
  const setPage = useLayoutComposerStore((s) => s.setPage)
  const setZoom = useLayoutComposerStore((s) => s.setZoom)
  const setMapScaleDenominator = useLayoutComposerStore((s) => s.setMapScaleDenominator)
  const setMapSnapshotUrl = useLayoutComposerStore((s) => s.setMapSnapshotUrl)
  const selectElement = useLayoutComposerStore((s) => s.selectElement)
  const addElement = useLayoutComposerStore((s) => s.addElement)
  const updateElementFrame = useLayoutComposerStore((s) => s.updateElementFrame)
  const updateElementProps = useLayoutComposerStore((s) => s.updateElementProps)
  const updateElementStyle = useLayoutComposerStore((s) => s.updateElementStyle)
  const updateElementMapView = useLayoutComposerStore((s) => s.updateElementMapView)
  const setElementVariant = useLayoutComposerStore((s) => s.setElementVariant)
  const removeElement = useLayoutComposerStore((s) => s.removeElement)
  const resetLayout = useLayoutComposerStore((s) => s.resetLayout)
  const layers = useMapStore((s) => s.layers)
  const pageRef = useRef<HTMLDivElement>(null)
  const canvasViewportRef = useRef<HTMLDivElement>(null)
  const [exporting, setExporting] = useState(false)
  const [elementMenuOpen, setElementMenuOpen] = useState(false)
  const [editingMapFrameId, setEditingMapFrameId] = useState<string | null>(null)
  const [canvasViewportSize, setCanvasViewportSize] = useState({ width: 0, height: 0 })

  const selectedElement = elements.find((element) => element.id === selectedElementId) ?? null
  const pageSize = useMemo(() => {
    const aspect = getLayoutAspect(page)
    const baseWidth = getLayoutDesignWidth(page)
    const availableWidth = Math.max(MIN_PAGE_WIDTH, canvasViewportSize.width - CANVAS_PADDING)
    const availableHeight = Math.max(MIN_PAGE_WIDTH / aspect, canvasViewportSize.height - CANVAS_PADDING)
    const fittedWidth = Math.min(baseWidth, availableWidth, availableHeight * aspect)
    const width = Math.max(MIN_PAGE_WIDTH, Math.round(fittedWidth * zoom))
    return {
      width,
      height: Math.round(width / aspect),
    }
  }, [canvasViewportSize.height, canvasViewportSize.width, page, zoom])
  const layoutScale = pageSize.width / getLayoutDesignWidth(page)

  useEffect(() => {
    const viewport = canvasViewportRef.current
    if (!viewport) return
    const updateSize = () => {
      const rect = viewport.getBoundingClientRect()
      setCanvasViewportSize({
        width: rect.width,
        height: rect.height,
      })
    }
    updateSize()
    const observer = new ResizeObserver(updateSize)
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [])

  const refreshSnapshot = () => {
    const snapshot = captureCurrentMapSnapshot()
    if (snapshot) setMapSnapshotUrl(snapshot)
  }

  const clearSelection = () => {
    selectElement(null)
    setEditingMapFrameId(null)
  }

  const handleExport = async () => {
    if (exporting) return
    setExporting(true)
    try {
      await exportLayoutAsPng({
        page,
        elements: elements.map((element) =>
          element.type === 'scale-bar'
            ? {
                ...element,
                props: {
                  ...(element.props ?? {}),
                  scaleDenominator: mapScaleDenominator,
                },
              }
            : element,
        ),
        layers,
        mapSnapshotUrl,
        exportOptions: {
          pixelRatio: 2,
          fileName: `opengis-layout-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`,
        },
      })
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="h-full min-w-0 bg-bg-primary text-text-primary flex flex-col">
      <div className="h-11 border-b border-border bg-bg-secondary flex items-center px-3 gap-2 shrink-0">
        <div className="flex items-center gap-2 min-w-[180px]">
          <Map className="w-4 h-4 text-accent-primary" />
          <div>
            <div className="text-sm font-medium leading-tight">制图画布</div>
            <div className="text-[10px] text-text-muted leading-tight">Layout Composer</div>
          </div>
        </div>

        <div className="h-5 w-px bg-border mx-1" />

        <select
          value={page.id}
          onChange={(event) => {
            const next = PAGE_PRESETS.find((preset) => preset.id === event.target.value)
            if (next) setPage(next)
          }}
          className="h-7 rounded-md bg-bg-primary border border-border px-2 text-xs outline-none focus:border-accent-primary"
        >
          {PAGE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
            </option>
          ))}
        </select>

        <label className="flex items-center gap-1.5 text-xs text-text-secondary">
          <span>1:</span>
          <input
            value={mapScaleDenominator}
            onChange={(event) => setMapScaleDenominator(Number(event.target.value))}
            className="h-7 w-24 rounded-md bg-bg-primary border border-border px-2 text-xs outline-none focus:border-accent-primary"
            type="number"
            min={100}
            step={1000}
          />
        </label>

        <div className="h-5 w-px bg-border mx-1" />

        <div className="relative">
          <button
            onClick={() => setElementMenuOpen((open) => !open)}
            className="h-7 px-2 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center gap-1.5 text-xs"
            title="添加制图元素"
          >
            <Map className="w-3.5 h-3.5" />
            <span>添加元素</span>
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
          {elementMenuOpen && (
            <div className="absolute top-8 left-0 z-30 w-40 rounded-md border border-border bg-bg-secondary shadow-xl py-1">
              {ELEMENT_TOOLS.map((tool) => (
                <button
                  key={tool.type}
                  onClick={() => {
                    addElement(tool.type)
                    setElementMenuOpen(false)
                  }}
                  className="w-full h-8 px-2 flex items-center gap-2 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                >
                  <tool.icon className="w-3.5 h-3.5" />
                  <span>{tool.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex-1" />

        <button
          onClick={() => setZoom(zoom - 0.1)}
          className="h-7 w-7 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="缩小"
        >
          <ZoomOut className="w-3.5 h-3.5" />
        </button>
        <span className="w-11 text-center text-xs text-text-muted">{Math.round(zoom * 100)}%</span>
        <button
          onClick={() => setZoom(zoom + 0.1)}
          className="h-7 w-7 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="放大"
        >
          <ZoomIn className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={refreshSnapshot}
          className="h-7 px-2 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center gap-1.5 text-xs"
          title="从当前地图更新地图框"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>更新地图</span>
        </button>
        <button
          onClick={resetLayout}
          className="h-7 w-7 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="重置画布"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="h-7 px-3 rounded-md bg-accent-primary text-white hover:brightness-110 disabled:opacity-60 flex items-center gap-1.5 text-xs"
          title="导出 PNG"
        >
          <Download className="w-3.5 h-3.5" />
          <span>{exporting ? '导出中' : '导出'}</span>
        </button>
      </div>

      <div className="flex-1 min-h-0 flex">
        <div className="w-56 border-r border-border bg-bg-secondary/70 shrink-0 flex flex-col">
          <div className="px-3 py-2 border-b border-border">
            <div className="text-xs font-medium">页面</div>
            <div className="text-[11px] text-text-muted mt-1">
              {page.widthMm} x {page.heightMm} mm
            </div>
          </div>
          <div className="px-3 py-2 border-b border-border">
            <div className="text-xs font-medium mb-2">元素</div>
            <div className="space-y-1">
              {elements.map((element) => (
                <button
                  key={element.id}
                  onClick={() => selectElement(element.id)}
                  className={`w-full h-8 rounded-md px-2 flex items-center justify-between text-xs transition-colors ${
                    selectedElementId === element.id
                      ? 'bg-accent-primary/15 text-accent-primary'
                      : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                  }`}
                >
                  <span className="truncate">{element.label}</span>
                  <span className="text-[10px] text-text-muted">{element.type}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="p-3 text-[11px] text-text-muted leading-relaxed">
            地图框使用进入画布时捕获的地图快照。调整地图后点“更新地图”刷新。
          </div>
        </div>

        <div
          ref={canvasViewportRef}
          onPointerDown={clearSelection}
          className="flex-1 min-w-0 overflow-auto bg-[radial-gradient(circle_at_1px_1px,var(--color-border)_1px,transparent_0)] [background-size:20px_20px]"
        >
          <div
            className="min-h-full min-w-full flex items-center justify-center p-10"
            onPointerDown={clearSelection}
          >
            <div
              ref={pageRef}
              className="relative shadow-2xl"
              style={{
                width: pageSize.width,
                height: pageSize.height,
                background: page.background,
              }}
              onPointerDown={clearSelection}
            >
              {elements
                .filter((element) => element.visible !== false)
                .map((element) => (
                  <LayoutElementBox
                    key={element.id}
                    element={element}
                    selected={selectedElementId === element.id}
                    mapSnapshotUrl={mapSnapshotUrl}
                    layers={layers}
                    page={page}
                    mapScaleDenominator={mapScaleDenominator}
                    pageRef={pageRef}
                    layoutScale={layoutScale}
                    editingMapFrame={editingMapFrameId === element.id}
                    onSelect={() => selectElement(element.id)}
                    onMapFrameEdit={() => setEditingMapFrameId((id) => id === element.id ? null : element.id)}
                    onFrameChange={(frame) => updateElementFrame(element.id, frame)}
                    onMapViewChange={(mapView) => updateElementMapView(element.id, mapView)}
                  />
                ))}
            </div>
          </div>
        </div>

        <InspectorPanel
          element={selectedElement}
          layers={layers}
          onFrameChange={(frame) => selectedElement && updateElementFrame(selectedElement.id, frame)}
          onPropsChange={(props) => selectedElement && updateElementProps(selectedElement.id, props)}
          onStyleChange={(style) => selectedElement && updateElementStyle(selectedElement.id, style)}
          onVariantChange={(variant) => selectedElement && setElementVariant(selectedElement.id, variant)}
          onMapViewChange={(mapView) => selectedElement && updateElementMapView(selectedElement.id, mapView)}
          onSplitLegend={(layerIds) => {
            layerIds.forEach((layerId, index) => {
              const layer = layers.find((item) => item.id === layerId)
              const id = addElement('legend', {
                label: layer ? `Legend - ${layer.name}` : 'Legend',
                frame: { x: 76, y: Math.min(82, 18 + index * 18), width: 18, height: 16 },
              })
              updateElementProps(id, {
                layerIds: [layerId],
                grouped: false,
                title: layer?.name ?? 'Legend',
              })
            })
          }}
          onRemove={() => selectedElement && removeElement(selectedElement.id)}
        />
      </div>
    </div>
  )
}

function LayoutElementBox({
  element,
  selected,
  mapSnapshotUrl,
  layers,
  page,
  mapScaleDenominator,
  pageRef,
  layoutScale,
  editingMapFrame,
  onSelect,
  onMapFrameEdit,
  onFrameChange,
  onMapViewChange,
}: {
  element: LayoutElement
  selected: boolean
  mapSnapshotUrl: string | null
  layers: MapLayerDefinition[]
  page: LayoutPage
  mapScaleDenominator: number
  pageRef: RefObject<HTMLDivElement>
  layoutScale: number
  editingMapFrame: boolean
  onSelect: () => void
  onMapFrameEdit: () => void
  onFrameChange: (frame: Partial<LayoutElementFrame>) => void
  onMapViewChange: (mapView: Partial<LayoutMapView>) => void
}) {
  const dragStart = useRef<{
    pointerX: number
    pointerY: number
    frame: LayoutElementFrame
    mapView: LayoutMapView
    captureTarget: HTMLElement
    mode: 'move' | 'resize-nw' | 'resize-ne' | 'resize-sw' | 'resize-se' | 'map-pan'
  } | null>(null)

  const handlePointerDown = (
    event: React.PointerEvent<HTMLElement>,
    mode: NonNullable<typeof dragStart.current>['mode'] = 'move',
  ) => {
    event.stopPropagation()
    onSelect()
    if (element.locked) return
    const effectiveMode = editingMapFrame && element.type === 'map-frame' && mode === 'move'
      ? 'map-pan'
      : mode
    dragStart.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      frame: { ...element.frame },
      mapView: { ...(element.mapView ?? { x: 0, y: 0, scale: 1 }) },
      captureTarget: event.currentTarget,
      mode: effectiveMode,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current || !pageRef.current) return
    const pageRect = pageRef.current.getBoundingClientRect()
    const dx = ((event.clientX - dragStart.current.pointerX) / pageRect.width) * 100
    const dy = ((event.clientY - dragStart.current.pointerY) / pageRect.height) * 100
    const { frame, mode, mapView } = dragStart.current
    if (mode === 'map-pan') {
      onMapViewChange({ x: mapView.x + dx, y: mapView.y + dy })
      return
    }
    if (mode === 'move') {
      onFrameChange({ x: frame.x + dx, y: frame.y + dy })
      return
    }
    const next: Partial<LayoutElementFrame> = {}
    if (mode.includes('w')) {
      next.x = frame.x + dx
      next.width = frame.width - dx
    }
    if (mode.includes('e')) {
      next.width = frame.width + dx
    }
    if (mode.includes('n')) {
      next.y = frame.y + dy
      next.height = frame.height - dy
    }
    if (mode.includes('s')) {
      next.height = frame.height + dy
    }
    onFrameChange(next)
  }

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const captureTarget = dragStart.current?.captureTarget
    dragStart.current = null
    if (captureTarget?.hasPointerCapture(event.pointerId)) {
      captureTarget.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <div
      data-layout-selection
      onPointerDown={(event) => handlePointerDown(event)}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onDoubleClick={(event) => {
        event.stopPropagation()
        if (element.type === 'map-frame') onMapFrameEdit()
      }}
      onWheel={(event) => {
        if (!editingMapFrame || element.type !== 'map-frame') return
        event.preventDefault()
        event.stopPropagation()
        const current = element.mapView ?? { x: 0, y: 0, scale: 1 }
        onMapViewChange({ scale: current.scale + (event.deltaY < 0 ? 0.08 : -0.08) })
      }}
      className={`absolute select-none ${
        selected ? 'outline outline-1 outline-accent-primary shadow-[0_0_0_3px_rgba(59,130,246,0.16)]' : ''
      } ${
        editingMapFrame ? 'ring-2 ring-accent-primary/30' : ''
      }`}
      style={{
        left: `${element.frame.x}%`,
        top: `${element.frame.y}%`,
        width: `${element.frame.width}%`,
        height: `${element.frame.height}%`,
        cursor: editingMapFrame && element.type === 'map-frame' ? 'grab' : element.locked ? 'default' : 'move',
      }}
    >
      {renderElementContent(element, mapSnapshotUrl, layers, editingMapFrame, layoutScale, page, mapScaleDenominator)}
      {selected && !element.locked && (
        <>
          {(['nw', 'ne', 'sw', 'se'] as const).map((corner) => (
            <button
              key={corner}
              onPointerDown={(event) => handlePointerDown(event, `resize-${corner}`)}
              className={`absolute w-2.5 h-2.5 rounded-sm bg-accent-primary border border-white shadow ${
                corner === 'nw' ? '-left-1.5 -top-1.5 cursor-nwse-resize' :
                corner === 'ne' ? '-right-1.5 -top-1.5 cursor-nesw-resize' :
                corner === 'sw' ? '-left-1.5 -bottom-1.5 cursor-nesw-resize' :
                '-right-1.5 -bottom-1.5 cursor-nwse-resize'
              }`}
              title="调整大小"
            />
          ))}
          {editingMapFrame && element.type === 'map-frame' && (
            <div className="absolute left-2 top-2 h-6 px-2 rounded bg-bg-secondary/90 border border-border text-[10px] text-text-secondary flex items-center gap-1">
              <Move className="w-3 h-3" />
              拖动内部地图，滚轮缩放
            </div>
          )}
        </>
      )}
    </div>
  )
}

function renderElementContent(
  element: LayoutElement,
  mapSnapshotUrl: string | null,
  layers: MapLayerDefinition[],
  editingMapFrame = false,
  layoutScale = 1,
  page?: LayoutPage,
  mapScaleDenominator = 50000,
) {
  const style = element.style ?? {}
  const opacity = style.opacity ?? 1
  const background = colorWithOpacity(style.backgroundColor ?? '#ffffff', style.backgroundOpacity ?? 0)
  const borderWidth = scaleLayoutValue(style.borderWidth ?? 0, layoutScale)
  const padding = scaleLayoutValue(style.padding ?? 0, layoutScale)
  if (element.type === 'map-frame') {
    const mapView = element.mapView ?? { x: 0, y: 0, scale: 1 }
    const variant = style.variant ?? 'default'
    return (
      <div
        className="w-full h-full overflow-hidden"
        style={{
          background: style.backgroundColor ?? '#dbeafe',
          border: `${scaleLayoutValue(style.borderWidth ?? 1, layoutScale)}px solid ${style.borderColor ?? '#111827'}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? (variant === 'boxed' ? 4 : 0), layoutScale),
          opacity,
        }}
      >
        {mapSnapshotUrl ? (
          <img
            src={mapSnapshotUrl}
            alt="Map snapshot"
            className="w-full h-full object-cover"
            style={{
              transform: `translate(${mapView.x}%, ${mapView.y}%) scale(${mapView.scale})`,
              transformOrigin: 'center',
              transition: editingMapFrame ? 'none' : 'transform 120ms ease',
            }}
            draggable={false}
          />
        ) : (
          <div className="w-full h-full bg-sky-100 flex items-center justify-center text-slate-500 text-xs">
            Map snapshot unavailable
          </div>
        )}
      </div>
    )
  }
  if (element.type === 'scale-bar') {
    const variant = style.variant ?? 'alternating'
    const strokeWidth = scaleLayoutValue(style.strokeWidth ?? 2, layoutScale)
    const color = style.strokeColor ?? '#111827'
    const fill = style.fillColor ?? color
    const textColor = style.textColor ?? color
    const segments = Number(element.props?.segments ?? 4)
    const metrics = buildScaleBarMetrics(element, page, mapScaleDenominator)
    const labels = metrics.labels
    const fontSize = scaleLayoutValue(style.fontSize ?? 10, layoutScale)
    const tickHeight = Math.max(3, scaleLayoutValue(8, layoutScale))
    return (
      <div
        className="w-full h-full relative"
        style={{
          background,
          border: `${borderWidth}px solid ${style.borderColor ?? color}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: textColor,
          opacity,
          padding,
        }}
      >
        <div
          className="absolute left-0 right-0"
          style={{
            width: `${metrics.widthRatio * 100}%`,
            bottom: fontSize + scaleLayoutValue(4, layoutScale),
            height: '42%',
            borderBottom: variant === 'boxed' ? undefined : `${strokeWidth}px solid ${color}`,
          }}
        >
          {variant === 'alternating' && (
            <div
              className="absolute left-0 right-0 bottom-0 flex"
              style={{ height: tickHeight }}
            >
              {Array.from({ length: segments }).map((_, index) => (
                <span
                  key={index}
                  className="flex-1 border"
                  style={{
                    borderColor: color,
                    background: index % 2 === 0 ? fill : 'transparent',
                  }}
                />
              ))}
            </div>
          )}
          {variant !== 'alternating' && Array.from({ length: segments + 1 }).map((_, index) => (
            <div
              key={index}
              className="absolute"
              style={{
                bottom: -tickHeight / 2,
                height: tickHeight,
                left: `${(100 / segments) * index}%`,
                borderLeft: `${strokeWidth}px solid ${color}`,
              }}
            />
          ))}
          {variant === 'double-line' && (
            <div
              className="absolute left-0 right-0"
              style={{ bottom: '35%', borderBottom: `${strokeWidth}px solid ${color}` }}
            />
          )}
        </div>
        {labels.map((label, index) => {
          const x = labels.length === 1 ? 50 : (index / (labels.length - 1)) * 100
          return (
            <span
              key={`${label}-${index}`}
              className="absolute bottom-0 leading-none whitespace-nowrap"
              style={{
                left: `${x}%`,
                fontSize,
                transform:
                  labels.length === 1 ? 'translateX(-50%)' :
                  index === 0 ? 'none' :
                  index === labels.length - 1 ? 'translateX(-100%)' :
                  'translateX(-50%)',
              }}
            >
              {label}
            </span>
          )
        })}
      </div>
    )
  }
  if (element.type === 'north-arrow') {
    const variant = style.variant ?? 'classic'
    const fill = style.fillColor ?? '#111827'
    const textColor = style.textColor ?? fill
    return (
      <div
        className="w-full h-full flex flex-col items-center justify-center"
        style={{
          background,
          border: `${borderWidth}px solid ${style.borderColor ?? fill}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: fill,
          opacity,
          padding,
        }}
      >
        <svg viewBox="0 0 48 64" className="w-full h-[78%]" aria-hidden>
          {variant === 'compass' ? (
            <>
              <circle cx="24" cy="30" r="18" fill="none" stroke="currentColor" strokeWidth="3" />
              <path d="M24 4 L31 30 L24 56 L17 30 Z" fill="currentColor" />
              <path d="M8 30 L24 24 L40 30 L24 36 Z" fill="rgba(255,255,255,0.75)" />
            </>
          ) : variant === 'triangle' ? (
            <path d="M24 2 L44 52 L24 40 L4 52 Z" fill="currentColor" />
          ) : (
            <>
              <path d="M24 2 L44 46 L24 36 L4 46 Z" fill="currentColor" />
              <path d="M24 10 L24 36 L10 43 Z" fill="rgba(255,255,255,0.7)" />
            </>
          )}
        </svg>
        <div
          className="font-semibold leading-none"
          style={{ color: textColor, fontSize: scaleLayoutValue(style.fontSize ?? 12, layoutScale) }}
        >
          N
        </div>
      </div>
    )
  }
  if (element.type === 'legend') {
    const legendSections = buildLegendSections(layers, element)
    const legendFontSize = scaleLayoutValue(style.fontSize ?? 10, layoutScale)
    const legendPadding = scaleLayoutValue(style.padding ?? 8, layoutScale)
    const swatchSize = scaleLayoutValue(12, layoutScale)
    return (
      <div
        className="w-full h-full overflow-hidden"
        style={{
          background: colorWithOpacity(
            style.backgroundColor ?? '#ffffff',
            style.backgroundOpacity ?? (style.opacity ?? 0.94),
          ),
          border: `${scaleLayoutValue(style.borderWidth ?? 1, layoutScale)}px solid ${style.borderColor ?? '#d1d5db'}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: style.textColor ?? '#111827',
          opacity: style.opacity ?? 1,
          padding: legendPadding,
          fontSize: legendFontSize,
        }}
      >
        <div className="font-semibold" style={{ marginBottom: scaleLayoutValue(8, layoutScale) }}>Legend</div>
        <div className="flex flex-col" style={{ gap: scaleLayoutValue(6, layoutScale) }}>
          {legendSections.map((section) => (
            <div key={section.layerId} className="min-w-0">
              {section.showTitle && (
                <div className="font-medium truncate" style={{ fontSize: legendFontSize }}>
                  {section.title}
                </div>
              )}
              <div className="flex flex-col" style={{ gap: scaleLayoutValue(4, layoutScale), marginTop: section.showTitle ? scaleLayoutValue(4, layoutScale) : 0 }}>
                {section.entries.map((entry) => (
                  <div key={`${section.layerId}-${entry.label}`} className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="border border-neutral-700 shrink-0"
                      style={{ background: entry.color, width: swatchSize, height: swatchSize }}
                    />
                    <span className="truncate" style={{ fontSize: legendFontSize }}>{entry.label}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {legendSections.length === 0 && (
            <div style={{ fontSize: legendFontSize, color: '#737373' }}>选择图层生成图例</div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div
      className="w-full h-full flex items-center overflow-hidden"
      style={{
        color: style.textColor ?? '#111827',
        fontSize: scaleLayoutValue(style.fontSize ?? 18, layoutScale),
        fontWeight: style.fontWeight ?? 600,
        opacity,
      }}
    >
      {String(element.props?.text ?? 'Map Title')}
    </div>
  )
}

function InspectorPanel({
  element,
  layers,
  onFrameChange,
  onPropsChange,
  onStyleChange,
  onVariantChange,
  onMapViewChange,
  onSplitLegend,
  onRemove,
}: {
  element: LayoutElement | null
  layers: MapLayerDefinition[]
  onFrameChange: (frame: Partial<LayoutElementFrame>) => void
  onPropsChange: (props: Record<string, unknown>) => void
  onStyleChange: (style: Partial<LayoutElementStyle>) => void
  onVariantChange: (variant: NonNullable<LayoutElementStyle['variant']>) => void
  onMapViewChange: (mapView: Partial<LayoutMapView>) => void
  onSplitLegend: (layerIds: string[]) => void
  onRemove: () => void
}) {
  const style = element?.style ?? {}
  const mapView = element?.mapView ?? { x: 0, y: 0, scale: 1 }
  const variants = element ? variantsForElement(element.type) : []

  return (
    <div className="w-64 border-l border-border bg-bg-secondary/70 shrink-0">
      <div className="h-10 border-b border-border px-3 flex items-center justify-between">
        <div className="text-xs font-medium">属性</div>
        {element && (
          <button
            onClick={onRemove}
            className="w-7 h-7 rounded-md text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 flex items-center justify-center"
            title="删除元素"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {!element ? (
        <div className="p-3 text-xs text-text-muted">选择一个画布元素。</div>
      ) : (
        <div className="p-3 space-y-4 overflow-y-auto h-[calc(100%-40px)]">
          <div>
            <div className="text-xs font-medium">{element.label}</div>
            <div className="text-[11px] text-text-muted mt-0.5">{element.type}</div>
          </div>

          {variants.length > 0 && (
            <label className="block text-[11px] text-text-muted">
              样式
              <select
                value={style.variant ?? variants[0]}
                onChange={(event) => onVariantChange(event.target.value as NonNullable<LayoutElementStyle['variant']>)}
                className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
              >
                {variants.map((variant) => (
                  <option key={variant} value={variant}>{variant}</option>
                ))}
              </select>
            </label>
          )}

          <div className="grid grid-cols-2 gap-2">
            {(['x', 'y', 'width', 'height'] as const).map((key) => (
              <label key={key} className="text-[11px] text-text-muted">
                <span className="uppercase">{key}</span>
                <input
                  type="number"
                  value={Number(element.frame[key].toFixed(1))}
                  onChange={(event) => onFrameChange({ [key]: Number(event.target.value) })}
                  className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
                />
              </label>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <ColorControl
              label={element.type === 'legend' || element.type === 'map-frame' ? '背景' : '填充'}
              value={style.fillColor ?? style.backgroundColor ?? '#ffffff'}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { backgroundColor: value }
                : { fillColor: value })}
            />
            {(element.type === 'scale-bar' || element.type === 'north-arrow') && (
              <ColorControl
                label="背景"
                value={style.backgroundColor ?? '#ffffff'}
                onChange={(value) => onStyleChange({ backgroundColor: value })}
              />
            )}
            <ColorControl
              label="线/边框"
              value={style.strokeColor ?? style.borderColor ?? '#111827'}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { borderColor: value }
                : { strokeColor: value })}
            />
            <ColorControl
              label="文字"
              value={style.textColor ?? '#111827'}
              onChange={(value) => onStyleChange({ textColor: value })}
            />
            <NumberControl
              label="透明度"
              value={style.opacity ?? 1}
              min={0}
              max={1}
              step={0.05}
              onChange={(value) => onStyleChange({ opacity: value })}
            />
            {(element.type === 'scale-bar' || element.type === 'north-arrow' || element.type === 'legend') && (
              <NumberControl
                label="背景透明"
                value={style.backgroundOpacity ?? (element.type === 'legend' ? style.opacity ?? 0.94 : 0)}
                min={0}
                max={1}
                step={0.05}
                onChange={(value) => onStyleChange({ backgroundOpacity: value })}
              />
            )}
            <NumberControl
              label="线宽"
              value={style.strokeWidth ?? style.borderWidth ?? 1}
              min={0}
              max={12}
              step={0.5}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { borderWidth: value }
                : { strokeWidth: value })}
            />
            <NumberControl
              label="字号"
              value={style.fontSize ?? (element.type === 'text' ? 18 : 10)}
              min={6}
              max={48}
              step={1}
              onChange={(value) => onStyleChange({ fontSize: value })}
            />
            <NumberControl
              label="圆角"
              value={style.borderRadius ?? 0}
              min={0}
              max={24}
              step={1}
              onChange={(value) => onStyleChange({ borderRadius: value })}
            />
            <NumberControl
              label="内边距"
              value={style.padding ?? 8}
              min={0}
              max={32}
              step={1}
              onChange={(value) => onStyleChange({ padding: value })}
            />
          </div>

          {element.type === 'map-frame' && (
            <div>
              <div className="text-[11px] text-text-muted mb-2">内部地图</div>
              <div className="grid grid-cols-3 gap-2">
                <NumberControl label="X" value={mapView.x} min={-100} max={100} step={1} onChange={(value) => onMapViewChange({ x: value })} />
                <NumberControl label="Y" value={mapView.y} min={-100} max={100} step={1} onChange={(value) => onMapViewChange({ y: value })} />
                <NumberControl label="缩放" value={mapView.scale} min={0.12} max={8} step={0.05} onChange={(value) => onMapViewChange({ scale: value })} />
              </div>
            </div>
          )}

          {element.type === 'scale-bar' && (
            <div className="space-y-2">
              <label className="h-7 flex items-center gap-2 text-[11px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={element.props?.autoLabel !== false}
                  onChange={(event) => onPropsChange({ autoLabel: event.target.checked })}
                />
                自动按地图比例计算标注
              </label>
              <label className="block text-[11px] text-text-muted">
                标注
                <input
                  value={String(element.props?.label ?? '')}
                  onChange={(event) => onPropsChange({ label: event.target.value })}
                  disabled={element.props?.autoLabel !== false}
                  className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
                />
              </label>
              <NumberControl
                label="分段"
                value={Number(element.props?.segments ?? 4)}
                min={1}
                max={8}
                step={1}
                onChange={(value) => onPropsChange({ segments: Math.round(value) })}
              />
            </div>
          )}

          {element.type === 'legend' && (
            <LegendInspector
              element={element}
              layers={layers}
              onPropsChange={onPropsChange}
              onSplitLegend={onSplitLegend}
            />
          )}

          {element.type === 'text' && (
            <label className="block text-[11px] text-text-muted">
              文本
              <input
                value={String(element.props?.text ?? '')}
                onChange={(event) => onPropsChange({ text: event.target.value })}
                className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
              />
            </label>
          )}
        </div>
      )}
    </div>
  )
}

function variantsForElement(type: LayoutElementType): NonNullable<LayoutElementStyle['variant']>[] {
  if (type === 'map-frame') return ['default', 'boxed', 'minimal']
  if (type === 'scale-bar') return ['alternating', 'double-line', 'minimal']
  if (type === 'north-arrow') return ['classic', 'triangle', 'compass']
  if (type === 'legend') return ['panel', 'minimal', 'boxed']
  return []
}

function LegendInspector({
  element,
  layers,
  onPropsChange,
  onSplitLegend,
}: {
  element: LayoutElement
  layers: MapLayerDefinition[]
  onPropsChange: (props: Record<string, unknown>) => void
  onSplitLegend: (layerIds: string[]) => void
}) {
  const selectedLayerIds = Array.isArray(element.props?.layerIds)
    ? element.props.layerIds.filter((id): id is string => typeof id === 'string')
    : []
  const selected = new Set(selectedLayerIds)
  const setLayerSelected = (layerId: string, checked: boolean) => {
    const next = checked
      ? [...selectedLayerIds, layerId]
      : selectedLayerIds.filter((id) => id !== layerId)
    onPropsChange({ layerIds: next })
  }

  return (
    <div className="space-y-2">
      <label className="h-7 flex items-center gap-2 text-[11px] text-text-secondary">
        <input
          type="checkbox"
          checked={element.props?.grouped !== false}
          onChange={(event) => onPropsChange({ grouped: event.target.checked })}
        />
        组合为一个图例
      </label>
      <div className="rounded-md border border-border bg-bg-primary/70 p-2">
        <div className="text-[11px] text-text-muted mb-2">选择图层</div>
        <div className="space-y-1 max-h-36 overflow-y-auto">
          {layers.map((layer) => (
            <label key={layer.id} className="h-6 flex items-center gap-2 text-[11px] text-text-secondary">
              <input
                type="checkbox"
                checked={selected.has(layer.id)}
                onChange={(event) => setLayerSelected(layer.id, event.target.checked)}
              />
              <span className="truncate">{layer.name}</span>
              <span className="ml-auto text-[10px] text-text-muted">{layer.style.renderType}</span>
            </label>
          ))}
          {layers.length === 0 && (
            <div className="text-[11px] text-text-muted">当前没有可用图层。</div>
          )}
        </div>
      </div>
      <button
        disabled={selectedLayerIds.length === 0}
        onClick={() => onSplitLegend(selectedLayerIds)}
        className="h-7 w-full rounded-md border border-border bg-bg-primary text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50"
      >
        拆分为独立图例
      </button>
    </div>
  )
}

interface LegendSection {
  layerId: string
  title: string
  showTitle: boolean
  entries: Array<{ label: string; color: string }>
}

function buildLegendSections(layers: MapLayerDefinition[], element: LayoutElement): LegendSection[] {
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

function buildScaleBarMetrics(
  element: LayoutElement,
  page: LayoutPage | undefined,
  fallbackScaleDenominator: number,
): { labels: string[]; widthRatio: number; maxDistanceMeters: number } {
  const auto = element.props?.autoLabel !== false
  if (!auto || !page) {
    return {
      labels: parseScaleBarLabels(String(element.props?.label ?? '0        5        10 km')),
      widthRatio: 1,
      maxDistanceMeters: 0,
    }
  }

  const scaleDenominator = Number(element.props?.scaleDenominator ?? fallbackScaleDenominator)
  const widthMm = page.widthMm * (element.frame.width / 100)
  const availableMeters = Math.max(0, (widthMm * scaleDenominator) / 1000)
  const maxDistanceMeters = niceDistance(availableMeters)
  const widthRatio = availableMeters > 0 ? Math.max(0.2, Math.min(1, maxDistanceMeters / availableMeters)) : 1
  const segments = Math.max(1, Math.min(8, Number(element.props?.segments ?? 4)))
  const labels = Array.from({ length: segments + 1 }, (_, index) =>
    formatDistance((maxDistanceMeters / segments) * index),
  )
  return { labels, widthRatio, maxDistanceMeters }
}

function niceDistance(maxMeters: number): number {
  if (!Number.isFinite(maxMeters) || maxMeters <= 0) return 0
  const exponent = Math.floor(Math.log10(maxMeters))
  const base = Math.pow(10, exponent)
  for (const multiple of [5, 2, 1]) {
    const candidate = multiple * base
    if (candidate <= maxMeters) return candidate
  }
  return base / 2
}

function formatDistance(meters: number): string {
  if (meters >= 1000) {
    const km = meters / 1000
    return `${Number.isInteger(km) ? km : km.toFixed(km >= 10 ? 1 : 2)} km`
  }
  return `${Math.round(meters)} m`
}

function ColorControl({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="text-[11px] text-text-muted">
      {label}
      <div className="mt-1 h-7 rounded-md bg-bg-primary border border-border px-1 flex items-center gap-1">
        <input
          type="color"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="w-5 h-5 bg-transparent border-0 p-0"
        />
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-xs text-text-primary outline-none"
        />
      </div>
    </label>
  )
}

function parseScaleBarLabels(label: string): string[] {
  const parts = label.trim().split(/\s+/).filter(Boolean)
  if (parts.length <= 1) return parts.length === 1 ? parts : ['0', '5', '10 km']
  const last = parts[parts.length - 1]
  const previous = parts[parts.length - 2]
  if (!looksNumeric(last) && looksNumeric(previous)) {
    return [...parts.slice(0, -2), `${previous} ${last}`]
  }
  return parts
}

function looksNumeric(value: string): boolean {
  return /^[-+]?\d+(?:\.\d+)?(?:,\d{3})*$/.test(value)
}

function NumberControl({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <label className="text-[11px] text-text-muted">
      {label}
      <input
        type="number"
        value={Number(value.toFixed(step < 1 ? 2 : 0))}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}

function colorWithOpacity(color: string, opacity: number): string {
  const alpha = Math.max(0, Math.min(1, opacity))
  const hex = color.trim()
  const shortHex = /^#([0-9a-f]{3})$/i.exec(hex)
  if (shortHex) {
    const [r, g, b] = shortHex[1].split('').map((part) => parseInt(part + part, 16))
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  const fullHex = /^#([0-9a-f]{6})$/i.exec(hex)
  if (fullHex) {
    const value = fullHex[1]
    const r = parseInt(value.slice(0, 2), 16)
    const g = parseInt(value.slice(2, 4), 16)
    const b = parseInt(value.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  return alpha >= 1 ? color : color
}
