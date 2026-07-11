import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  Compass,
  Download,
  List,
  Map,
  RefreshCw,
  RotateCcw,
  Ruler,
  Type,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useMapStore } from '@/stores/mapStore'
import { exportLayoutAsPng } from './layoutExport'
import { captureCurrentMapSnapshot } from './mapSnapshot'
import { InspectorPanel } from './InspectorPanel'
import { LayoutElementBox } from './LayoutElementBox'
import { PAGE_PRESETS, useLayoutComposerStore } from './layoutComposerStore'
import { getLayoutAspect, getLayoutDesignWidth } from './layoutMetrics'
import type { LayoutElementType } from './types'

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
          className="h-7 shrink-0 rounded-md bg-bg-primary border border-border px-2 text-xs outline-none focus:border-accent-primary"
        >
          {PAGE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
            </option>
          ))}
        </select>

        <label className="flex shrink-0 items-center gap-1.5 text-xs text-text-secondary">
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

        <div className="relative shrink-0">
          <button
            onClick={() => setElementMenuOpen((open) => !open)}
            className="h-7 px-2 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center gap-1.5 text-xs whitespace-nowrap"
            title="添加制图元素"
          >
            <Map className="w-3.5 h-3.5" />
            <span className="hidden 2xl:inline">添加元素</span>
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
          className="h-7 w-7 shrink-0 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="缩小"
        >
          <ZoomOut className="w-3.5 h-3.5" />
        </button>
        <span className="w-11 shrink-0 text-center text-xs text-text-muted">{Math.round(zoom * 100)}%</span>
        <button
          onClick={() => setZoom(zoom + 0.1)}
          className="h-7 w-7 shrink-0 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="放大"
        >
          <ZoomIn className="w-3.5 h-3.5" />
        </button>

        <button
          onClick={refreshSnapshot}
          className="h-7 shrink-0 px-2 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center gap-1.5 text-xs whitespace-nowrap"
          title="从当前地图更新地图框"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span className="hidden 2xl:inline">更新地图</span>
        </button>
        <button
          onClick={resetLayout}
          className="h-7 w-7 shrink-0 rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary hover:bg-bg-hover flex items-center justify-center"
          title="重置画布"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="h-7 shrink-0 px-2 2xl:px-3 rounded-md bg-accent-primary text-white hover:brightness-110 disabled:opacity-60 flex items-center gap-1.5 text-xs whitespace-nowrap"
          title="导出 PNG"
        >
          <Download className="w-3.5 h-3.5" />
          <span className="hidden 2xl:inline">{exporting ? '导出中' : '导出'}</span>
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
