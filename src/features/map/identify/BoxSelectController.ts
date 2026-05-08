/**
 * BoxSelectController — 框选模式控制器。
 *
 * 用户按住鼠标拖拽绘制矩形框，松开后查询框内所有可见要素，
 * 将结果推送到 mapStore 的 boxSelectedFeatures（独立于单选的 identifiedFeatures），
 * 由 BoxSelectResultPanel 以列表模式展示。
 *
 * 实现方式：
 * - mousedown 记录起点
 * - mousemove 绘制半透明矩形 overlay
 * - mouseup 用 queryRenderedFeatures(bbox) 查询框内要素
 * - 禁用地图拖拽（dragPan.disable），框选结束后恢复
 */
import maplibregl from 'maplibre-gl'
import { mapEngine } from '../engine/MapEngine'
import { useMapStore } from '@/stores/mapStore'
import type { MapLayerDefinition } from '@/services/geo'

export class BoxSelectController {
  private map: maplibregl.Map
  private canvas: HTMLCanvasElement
  private container: HTMLElement

  /** Selection box overlay element */
  private boxEl: HTMLDivElement | null = null
  /** Start point of the drag (pixel coords) */
  private startPoint: { x: number; y: number } | null = null
  /** Whether we are currently dragging */
  private dragging = false

  /** Track highlighted features for cleanup */
  private highlightedFeatures: Array<{
    source: string
    sourceLayer?: string
    id: string | number
  }> = []

  /** Bound handlers for cleanup */
  private boundMouseDown: (e: MouseEvent) => void
  private boundMouseMove: (e: MouseEvent) => void
  private boundMouseUp: (e: MouseEvent) => void
  private boundKeyDown: (e: KeyboardEvent) => void

  constructor(map: maplibregl.Map) {
    this.map = map
    this.canvas = map.getCanvas()
    this.container = map.getCanvasContainer()

    // Disable default drag pan so our box-select takes priority.
    // boxZoom 也要禁用：MapLibre 默认 shift+drag = boxZoom，但即使没按 shift
    // 它也会在 mousedown 阶段尝试拦截，会和我们的 box-select 冲突
    map.dragPan.disable()
    map.boxZoom.disable()

    // Set cursor
    this.canvas.style.cursor = 'crosshair'

    this.boundMouseDown = this.onMouseDown.bind(this)
    this.boundMouseMove = this.onMouseMove.bind(this)
    this.boundMouseUp = this.onMouseUp.bind(this)
    this.boundKeyDown = this.onKeyDown.bind(this)

    this.canvas.addEventListener('mousedown', this.boundMouseDown)
    window.addEventListener('keydown', this.boundKeyDown)
  }

  destroy(): void {
    this.canvas.removeEventListener('mousedown', this.boundMouseDown)
    window.removeEventListener('mousemove', this.boundMouseMove)
    window.removeEventListener('mouseup', this.boundMouseUp)
    window.removeEventListener('keydown', this.boundKeyDown)
    this.removeBox()
    this.clearAllHighlights()
    this.canvas.style.cursor = ''
    this.map.dragPan.enable()
    this.map.boxZoom.enable()
  }

  // ─── Mouse handlers ───────────────────────────────────────────

  private onMouseDown(e: MouseEvent): void {
    // Only left button
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()

    const rect = this.canvas.getBoundingClientRect()
    this.startPoint = {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    }
    this.dragging = true

    // Create the selection box element
    this.createBox()

    // Attach move/up to window so we catch events outside canvas
    window.addEventListener('mousemove', this.boundMouseMove)
    window.addEventListener('mouseup', this.boundMouseUp)
  }

  private onMouseMove(e: MouseEvent): void {
    if (!this.dragging || !this.startPoint || !this.boxEl) return
    e.preventDefault()

    const rect = this.canvas.getBoundingClientRect()
    const currentX = e.clientX - rect.left
    const currentY = e.clientY - rect.top

    const minX = Math.min(this.startPoint.x, currentX)
    const minY = Math.min(this.startPoint.y, currentY)
    const maxX = Math.max(this.startPoint.x, currentX)
    const maxY = Math.max(this.startPoint.y, currentY)

    this.boxEl.style.left = `${minX}px`
    this.boxEl.style.top = `${minY}px`
    this.boxEl.style.width = `${maxX - minX}px`
    this.boxEl.style.height = `${maxY - minY}px`
    this.boxEl.style.display = 'block'
  }

  private onMouseUp(e: MouseEvent): void {
    if (!this.dragging || !this.startPoint) return
    e.preventDefault()

    const rect = this.canvas.getBoundingClientRect()
    const endPoint = {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    }

    // Clean up drag state
    this.dragging = false
    window.removeEventListener('mousemove', this.boundMouseMove)
    window.removeEventListener('mouseup', this.boundMouseUp)
    this.removeBox()

    // Minimum drag distance to count as a box select (avoid accidental clicks)
    const dx = Math.abs(endPoint.x - this.startPoint.x)
    const dy = Math.abs(endPoint.y - this.startPoint.y)
    if (dx < 5 && dy < 5) {
      this.startPoint = null
      return
    }

    // Query features within the bounding box
    const bbox: [maplibregl.PointLike, maplibregl.PointLike] = [
      [Math.min(this.startPoint.x, endPoint.x), Math.min(this.startPoint.y, endPoint.y)],
      [Math.max(this.startPoint.x, endPoint.x), Math.max(this.startPoint.y, endPoint.y)],
    ]

    this.queryFeaturesInBox(bbox)
    this.startPoint = null
  }

  private onKeyDown(e: KeyboardEvent): void {
    // Escape cancels current drag
    if (e.key === 'Escape' && this.dragging) {
      this.dragging = false
      window.removeEventListener('mousemove', this.boundMouseMove)
      window.removeEventListener('mouseup', this.boundMouseUp)
      this.removeBox()
      this.startPoint = null
    }
  }

  // ─── Box element management ───────────────────────────────────

  private createBox(): void {
    if (this.boxEl) return
    const el = document.createElement('div')
    el.style.cssText = [
      'position:absolute',
      'pointer-events:none',
      'z-index:10',
      'border:2px solid rgba(99,102,241,0.8)',
      'background:rgba(99,102,241,0.12)',
      'border-radius:3px',
      'display:none',
      'box-shadow:0 0 0 1px rgba(99,102,241,0.3),inset 0 0 12px rgba(99,102,241,0.08)',
      'transition:none',
    ].join(';')
    this.container.appendChild(el)
    this.boxEl = el
  }

  private removeBox(): void {
    if (this.boxEl) {
      this.boxEl.remove()
      this.boxEl = null
    }
  }

  // ─── Feature query ────────────────────────────────────────────

  private queryFeaturesInBox(bbox: [maplibregl.PointLike, maplibregl.PointLike]): void {
    const layers = this.getQueryableLayerIds()
    if (layers.length === 0) return

    let features: maplibregl.MapGeoJSONFeature[] = []
    try {
      features = this.map.queryRenderedFeatures(bbox, { layers }) as any
    } catch {
      features = []
    }

    if (features.length === 0) {
      useMapStore.getState().clearBoxSelectedFeatures()
      return
    }

    // Deduplicate by feature id + source (queryRenderedFeatures can return
    // the same feature from multiple tiles)
    const seen = new Set<string>()
    const unique = features.filter((f) => {
      const key = `${f.source}::${f.sourceLayer ?? ''}::${f.id ?? JSON.stringify(f.properties)}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })

    // Filter out cluster features
    const nonCluster = unique.filter((f) => !f.properties?.cluster)

    if (nonCluster.length === 0) {
      useMapStore.getState().clearBoxSelectedFeatures()
      return
    }

    // Convert to BoxSelectFeatureInfo (includes source/featureId for highlight)
    const featureInfos = nonCluster.map((f) => {
      const defId = mapEngine.getDefIdFromRenderLayerId(f.layer.id)
      const def = defId ? findDef(defId) : null
      const layerName = def?.name ?? f.layer.id
      const geomType = (f.geometry as any)?.type ?? 'Unknown'
      const coords =
        geomType === 'Point'
          ? ((f.geometry as any).coordinates as [number, number])
          : undefined

      return {
        layerName,
        geometryType: geomType,
        properties: f.properties ?? {},
        coordinates: coords,
        sourceId: f.source,
        sourceLayer: f.sourceLayer,
        featureId: f.id,
        renderLayerId: f.layer.id,
        // Keep raw geometry for highlight layer rendering
        geometry: f.geometry as GeoJSON.Geometry,
      }
    })

    // Highlight all selected features via feature-state
    this.clearAllHighlights()
    for (const info of featureInfos) {
      if (info.featureId !== undefined && info.featureId !== null) {
        try {
          // Guard: verify source still exists (may have been rebuilt)
          if (!this.map.getSource(info.sourceId)) continue
          this.map.setFeatureState(
            {
              source: info.sourceId,
              sourceLayer: info.sourceLayer,
              id: info.featureId,
            },
            { hover: true },
          )
          this.highlightedFeatures.push({
            source: info.sourceId,
            sourceLayer: info.sourceLayer,
            id: info.featureId,
          })
        } catch {
          // Some sources don't support feature-state, ignore
        }
      }
    }

    useMapStore.getState().setBoxSelectedFeatures(featureInfos)
  }

  private getQueryableLayerIds(): string[] {
    const all = mapEngine.getManagedLayerIds()
    return all.filter((id) => this.map.getLayer(id))
  }

  /** Clear feature-state highlights for all previously highlighted features */
  clearAllHighlights(): void {
    for (const f of this.highlightedFeatures) {
      try {
        // Guard: source may have been removed & re-added (e.g. renderType
        // switch, basemap change, or visibility toggle that triggers a
        // full layer rebuild). If the source no longer exists, skip.
        if (!this.map.getSource(f.source)) continue
        this.map.setFeatureState(
          { source: f.source, sourceLayer: f.sourceLayer, id: f.id },
          { hover: false },
        )
      } catch {
        /* no-op — source may exist but feature id is stale */
      }
    }
    this.highlightedFeatures = []
  }
}

// ─── Helper ─────────────────────────────────────────────────────────

function findDef(defId: string): MapLayerDefinition | null {
  const s = useMapStore.getState()
  return s.layers.find((l) => l.id === defId) ?? null
}
