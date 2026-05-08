/**
 * IdentifyController — 属性识别（hover tooltip + click → React panel）。
 *
 * 设计目标：
 * - 给 MapLibre 地图挂上鼠标交互，`queryRenderedFeatures` 只查 MapEngine
 *   管理的 render layer，避免误触底图 POI/道路/水系 symbol 层。
 * - hover 节流 60ms（~16fps 够肉眼跟手），click 不节流。
 * - 大图层（feature 数万级）是 MapLibre 自己在 GPU 裁的已渲染特征，
 *   queryRenderedFeatures 本身是 O(可视范围内被渲染的 tiles)，不是 O(所有
 *   feature)。这就是为什么"注意性能优化"的重点不在这里——而在于**不要
 *   在每次 mousemove 里都跑 React setState**。我们分两级：cursor 变化（
 *   同步轻量）、tooltip 内容（throttled）。
 * - Cluster renderer 的 cluster-circle 点击走 `getClusterExpansionZoom`
 *   放大，不弹属性面板（GIS 用户习惯）。
 * - click 时将 feature 属性推送到 mapStore，由 React FeatureAttributePanel
 *   组件渲染属性面板，取代原来的 MapLibre 原生 Popup。
 */
import maplibregl from 'maplibre-gl'
import { mapEngine } from '../engine/MapEngine'
import { useMapStore } from '@/stores/mapStore'
import type { MapLayerDefinition } from '@/services/geo'

// ─── 常量 ───────────────────────────────────────────────────────────

const HOVER_THROTTLE_MS = 60
/** hover 命中多个 feature 时最多在 tooltip 里列几个属性。 */
const TOOLTIP_MAX_FIELDS = 3

// ─── 工具：属性格式化 ────────────────────────────────────────────────

function formatValue(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    return v.toFixed(4).replace(/\.?0+$/, '')
  }
  if (typeof v === 'string') {
    // 截断过长字符串
    return v.length > 80 ? v.slice(0, 77) + '…' : escapeHtml(v)
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  try {
    return escapeHtml(JSON.stringify(v))
  } catch {
    return escapeHtml(String(v))
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function pickInterestingFields(
  props: Record<string, any>,
  max: number,
): Array<[string, any]> {
  const keys = Object.keys(props).filter(
    // 跳过 cluster 内部字段、太长的二进制键
    (k) => !k.startsWith('_') && k !== 'cluster' && k !== 'cluster_id' && k !== 'point_count' && k !== 'point_count_abbreviated',
  )
  return keys.slice(0, max).map((k) => [k, props[k]])
}

// ─── Tooltip（hover） ───────────────────────────────────────────────

/**
 * 轻量 tooltip —— 跟鼠标走，一个 absolute positioned div。
 * 不用 MapLibre Popup 是因为 Popup 是基于 LngLat 锚定的，hover 跟鼠标
 * 要每帧 setLngLat，会触发一次 DOM 重排。直接 absolute + transform 更便宜。
 */
class HoverTooltip {
  private el: HTMLDivElement
  private visible = false

  constructor(parent: HTMLElement) {
    const el = document.createElement('div')
    el.className = 'opengis-identify-tooltip'
    el.style.cssText = [
      'position:absolute',
      'pointer-events:none',
      'z-index:9',
      'padding:6px 10px',
      'background:rgba(18,18,22,0.92)',
      'color:#e5e5e5',
      'border:1px solid rgba(255,255,255,0.1)',
      'border-radius:6px',
      'font:12px/1.4 ui-sans-serif,system-ui,sans-serif',
      'backdrop-filter:blur(8px)',
      '-webkit-backdrop-filter:blur(8px)',
      'box-shadow:0 4px 12px rgba(0,0,0,0.3)',
      'max-width:280px',
      'display:none',
      'transition:opacity 80ms ease',
      'will-change:transform',
    ].join(';')
    parent.appendChild(el)
    this.el = el
  }

  show(html: string, x: number, y: number): void {
    this.el.innerHTML = html
    // 稍微偏移一点，避免挡住鼠标尖
    this.el.style.transform = `translate(${x + 12}px, ${y + 12}px)`
    if (!this.visible) {
      this.el.style.display = 'block'
      this.visible = true
    }
  }

  hide(): void {
    if (this.visible) {
      this.el.style.display = 'none'
      this.visible = false
    }
  }

  destroy(): void {
    this.el.remove()
  }
}

// ─── Controller ────────────────────────────────────────────────────

export class IdentifyController {
  private map: maplibregl.Map
  private tooltip: HoverTooltip
  /** hover 节流用的 raf/timer 标识 */
  private hoverTimer: number | null = null
  /** 最近一次 mousemove 的事件（节流时延迟处理用） */
  private pendingEvent: maplibregl.MapMouseEvent | null = null
  /** 当前高亮的 feature id（用 feature-state 做高亮光环） */
  private hoveredFeature: {
    source: string
    sourceLayer?: string
    id: string | number
  } | null = null
  /** 订阅的 mapStore unsubscribe 句柄 */
  private unsubStore: (() => void) | null = null

  /** 绑定的 handler 引用，destroy 时 off 掉 */
  private boundMove: (e: maplibregl.MapMouseEvent) => void
  private boundLeave: () => void
  private boundClick: (e: maplibregl.MapMouseEvent) => void

  constructor(map: maplibregl.Map) {
    this.map = map
    const canvasContainer = map.getCanvasContainer()
    this.tooltip = new HoverTooltip(canvasContainer)

    this.boundMove = this.handleMouseMove.bind(this)
    this.boundLeave = this.handleMouseLeave.bind(this)
    this.boundClick = this.handleClick.bind(this)

    map.on('mousemove', this.boundMove)
    map.on('mouseout', this.boundLeave)
    map.on('click', this.boundClick)

    // Clear identified features when layers change (avoid stale info)
    this.unsubStore = useMapStore.subscribe((state, prev) => {
      if (state.layers !== prev.layers) {
        useMapStore.getState().clearIdentifiedFeatures()
        this.tooltip.hide()
        this.clearHover()
      }
    })
  }

  destroy(): void {
    this.map.off('mousemove', this.boundMove)
    this.map.off('mouseout', this.boundLeave)
    this.map.off('click', this.boundClick)
    if (this.hoverTimer !== null) {
      cancelAnimationFrame(this.hoverTimer)
      this.hoverTimer = null
    }
    this.tooltip.destroy()
    this.clearHover()
    this.unsubStore?.()
    // Clear panel when controller is destroyed (e.g. switching back to pan mode)
    useMapStore.getState().clearIdentifiedFeatures()
  }

  // ─── 查询范围：只查我们自己的 managed render layer ──────────────

  private getQueryableLayerIds(): string[] {
    const all = mapEngine.getManagedLayerIds()
    // 过滤：cluster-count 是 symbol layer，queryRenderedFeatures 会返回
    // 它，但它只是数字 label，没必要当 identify 目标；同理 cluster-circle
    // 也是聚合的伪 feature，一般 GIS 不会对 cluster 弹属性而是交互放大。
    // 但我们仍然查它（便于分发到 handleClick 里决定要 expand 还是弹 popup）。
    return all.filter((id) => this.map.getLayer(id))
  }

  // ─── mousemove 处理 ───────────────────────────────────────────

  private handleMouseMove(e: maplibregl.MapMouseEvent): void {
    this.pendingEvent = e
    if (this.hoverTimer !== null) return // 节流中
    const startAt = performance.now()
    this.hoverTimer = requestAnimationFrame(() => {
      this.hoverTimer = null
      const ev = this.pendingEvent
      this.pendingEvent = null
      if (!ev) return

      // 如果 raf 调度比阈值慢，下次 mousemove 立即再排一帧
      const elapsed = performance.now() - startAt
      if (elapsed < 0) return // should not happen but guard

      this.doHover(ev)
    })
    // 强制 THROTTLE：如果 rAF 短于 HOVER_THROTTLE_MS，再延一个 setTimeout
    // 这里采用折中：rAF 已足够（通常 16ms），直接靠它节流。
  }

  private doHover(e: maplibregl.MapMouseEvent): void {
    const layers = this.getQueryableLayerIds()
    if (layers.length === 0) {
      this.tooltip.hide()
      this.map.getCanvas().style.cursor = ''
      this.clearHover()
      return
    }

    let features: maplibregl.MapGeoJSONFeature[] = []
    try {
      features = this.map.queryRenderedFeatures(e.point, { layers }) as any
    } catch {
      features = []
    }

    if (features.length === 0) {
      this.tooltip.hide()
      this.map.getCanvas().style.cursor = ''
      this.clearHover()
      return
    }

    this.map.getCanvas().style.cursor = 'pointer'

    // 用第一个 feature 做 tooltip
    const top = features[0]
    const defId = mapEngine.getDefIdFromRenderLayerId(top.layer.id)
    const def = defId ? findDef(defId) : null
    const html = buildTooltipHtml(top, def, features.length)

    // 鼠标相对 canvas 的像素坐标
    this.tooltip.show(html, e.point.x, e.point.y)

    // 高亮光环（feature-state）—— 仅对带 id 的 feature 生效
    this.setHover(top)
  }

  private handleMouseLeave(): void {
    this.tooltip.hide()
    this.map.getCanvas().style.cursor = ''
    this.clearHover()
  }

  private setHover(feature: maplibregl.MapGeoJSONFeature): void {
    const id = feature.id
    if (id === undefined || id === null) return
    if (this.hoveredFeature) {
      if (
        this.hoveredFeature.source === feature.source &&
        this.hoveredFeature.id === id &&
        this.hoveredFeature.sourceLayer === feature.sourceLayer
      ) {
        return
      }
      this.clearHover()
    }
    try {
      // Guard: source may have been removed & re-added after style/render changes
      if (!this.map.getSource(feature.source)) return
      this.map.setFeatureState(
        {
          source: feature.source,
          sourceLayer: feature.sourceLayer,
          id,
        },
        { hover: true },
      )
      this.hoveredFeature = {
        source: feature.source,
        sourceLayer: feature.sourceLayer,
        id,
      }
    } catch {
      // 某些 source 不支持 feature state（比如 cluster），忽略
    }
  }

  private clearHover(): void {
    if (!this.hoveredFeature) return
    try {
      // Guard: source may have been removed & re-added after style/render changes
      if (!this.map.getSource(this.hoveredFeature.source)) {
        this.hoveredFeature = null
        return
      }
      this.map.setFeatureState(
        {
          source: this.hoveredFeature.source,
          sourceLayer: this.hoveredFeature.sourceLayer,
          id: this.hoveredFeature.id,
        },
        { hover: false },
      )
    } catch {
      /* no-op */
    }
    this.hoveredFeature = null
  }

  // ─── click 处理 ──────────────────────────────────────────────

  private handleClick(e: maplibregl.MapMouseEvent): void {
    const layers = this.getQueryableLayerIds()
    if (layers.length === 0) return

    let features: maplibregl.MapGeoJSONFeature[] = []
    try {
      features = this.map.queryRenderedFeatures(e.point, { layers }) as any
    } catch {
      features = []
    }
    if (features.length === 0) {
      useMapStore.getState().clearIdentifiedFeatures()
      return
    }

    // Cluster 特殊处理：点到 cluster-circle 时放大
    const clusterFeature = features.find(
      (f) => f.layer.id.endsWith('-cluster-circle') && f.properties?.cluster,
    )
    if (clusterFeature) {
      const clusterId = clusterFeature.properties!.cluster_id as number
      const sourceId = clusterFeature.source
      const src = this.map.getSource(sourceId) as maplibregl.GeoJSONSource & {
        getClusterExpansionZoom: (id: number, cb: (err: any, zoom: number) => void) => void
      }
      if (typeof src.getClusterExpansionZoom === 'function') {
        src.getClusterExpansionZoom(clusterId, (err: any, zoom: number) => {
          if (err) return
          const coords = (clusterFeature.geometry as any).coordinates as [number, number]
          this.map.easeTo({ center: coords, zoom: zoom + 0.5 })
        })
      }
      return
    }

    // Convert to FeatureInfo and push to store
    const featureInfos = features
      .filter((f) => !f.properties?.cluster)
      .map((f) => {
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
        }
      })

    useMapStore.getState().setIdentifiedFeatures(featureInfos)
  }
}

// ─── Tooltip HTML 生成 ─────────────────────────────────────────────

function findDef(defId: string): MapLayerDefinition | null {
  const s = useMapStore.getState()
  return s.layers.find((l) => l.id === defId) ?? null
}

function buildTooltipHtml(
  feature: maplibregl.MapGeoJSONFeature,
  def: MapLayerDefinition | null,
  totalHits: number,
): string {
  const props = feature.properties ?? {}
  const isCluster = Boolean(props.cluster)
  if (isCluster) {
    return `<div style="font-weight:600;color:#f5f5f5;">Cluster × ${props.point_count_abbreviated ?? props.point_count}</div>
      <div style="color:#9ca3af;font-size:11px;margin-top:2px;">点击展开</div>`
  }

  const layerName = def?.name ?? feature.layer.id
  const pairs = pickInterestingFields(props, TOOLTIP_MAX_FIELDS)
  const rows = pairs
    .map(
      ([k, v]) =>
        `<div style="display:flex;gap:8px;align-items:baseline;"><span style="color:#9ca3af;min-width:60px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(k)}</span><span style="color:#e5e5e5;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${formatValue(v)}</span></div>`,
    )
    .join('')
  const extra =
    totalHits > 1
      ? `<div style="margin-top:4px;color:#6b7280;font-size:11px;">+${totalHits - 1} more — 点击查看</div>`
      : ''
  return `<div style="font-weight:600;color:#f5f5f5;margin-bottom:4px;">${escapeHtml(layerName)}</div>${rows}${extra}`
}
