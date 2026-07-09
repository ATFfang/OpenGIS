import { useRef, type RefObject } from 'react'
import { Move } from 'lucide-react'
import type { MapLayerDefinition } from '@/services/geo'
import type { LayoutElement, LayoutElementFrame, LayoutMapView, LayoutPage } from './types'
import { renderElementContent } from './LayoutElementContent'

export function LayoutElementBox({
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
