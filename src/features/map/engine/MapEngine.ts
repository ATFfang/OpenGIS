/**
 * MapEngine - 封装所有 MapLibre GL JS 实例操作
 *
 * 这是唯一直接操作 MapLibre map 实例的模块。
 * 其他所有模块都通过 mapStore 交互，MapEngine 负责同步 store → map。
 *
 * 职责：
 * - 初始化 / 销毁 MapLibre 地图
 * - 添加 / 移除 / 更新地图上的 source 和 layer
 * - 处理底图切换（vector-style vs raster-tiles）
 * - 视图控制（fit bounds、fly to 等）
 * - 将视图状态变化同步回 store
 *
 * 2026-04-24 改造：把原来 `syncRenderLayers` 里的 switch 彻底拆到
 * `src/features/map/renderers/` 下，每种渲染模式一个独立文件。MapEngine
 * 这里只做：
 *   - source 的新增/更新/移除（vector 类 = geojson，raster 类 = 由
 *     renderer 自己接管 addSource，因为 raster 有 image/tiles 两种 sub-type）
 *   - 统一维护 `managedSourceIds / managedLayerIds` 跟踪集合
 *   - 把 attach/update/remove 分发给 renderer
 */
import maplibregl from 'maplibre-gl'
import type { MapLayerDefinition, BasemapSource, ParsedVectorData } from '@/services/geo'
import {
  getRenderer,
  type LayerRenderer,
  type RendererContext,
  sourceIdFor,
} from '../renderers'

/** 底图样式重新加载完成后的回调函数 */
export type StyleReloadCallback = () => void

/** 地图初次 load / 销毁状态变化的订阅回调 */
export type ReadyChangeCallback = (ready: boolean) => void

// ─── 类型定义 ──────────────────────────────────────

export interface MapEngineOptions {
  container: HTMLElement
  basemap: BasemapSource
  center: [number, number]
  zoom: number
  bearing: number
  pitch: number
  onMoveEnd?: (center: [number, number], zoom: number, bearing: number, pitch: number) => void
}

// ─── MapEngine 类 ──────────────────────────────────────

export class MapEngine {
  private map: maplibregl.Map | null = null
  private currentBasemap: BasemapSource | null = null
  private managedSourceIds = new Set<string>()
  private managedLayerIds = new Set<string>()
  /** renderLayerId → defId 映射，替代 KNOWN_SUFFIXES 穷举匹配 */
  private renderLayerToDef = new Map<string, string>()
  private onMoveEnd?: MapEngineOptions['onMoveEnd']
  private labelsHidden = false
  private basemapHidden = false
  private moveEndHandler: (() => void) | null = null
  private onStyleReload?: StyleReloadCallback

  /**
   * 地图首个 style.load 是否完成。controller / hook 的挂载需要等这个标志，
   * 但我们不想让 React 组件的 useState 来承载它 —— React.StrictMode 双挂载
   * 时 effect 内闭包捕获的 setState 容易在 cleanup 后失效，导致 mapReady
   * 永远停在 false（用户表现：点了 identify/box-select 按钮，光标变成
   * crosshair，但 controller 根本没挂上、底图照样能拖）
   *
   * 因此 readiness 由 MapEngine 自己持有，并提供 onReady 订阅给 hook 用
   */
  private ready = false
  private readyListeners = new Set<ReadyChangeCallback>()

  /**
   * 注册底图样式重新加载后的回调函数
   * MapView 使用此方法在 setBasemap 后重新同步用户图层
   */
  setOnStyleReload(cb: StyleReloadCallback): void {
    this.onStyleReload = cb
  }

  /**
   * 初始化 MapLibre 地图实例
   */
  init(options: MapEngineOptions): maplibregl.Map {
    if (this.map) {
      this.destroy()
    }

    const style = this.buildStyle(options.basemap)

    this.map = new maplibregl.Map({
      container: options.container,
      style,
      center: options.center,
      zoom: options.zoom,
      bearing: options.bearing,
      pitch: options.pitch,
      attributionControl: false,
      // CRITICAL: 需要显式 preserveDrawingBuffer 才能 canvas.toDataURL / toBlob
      // 成功导出整张地图 —— 否则每帧刷新完 buffer 就清了，导出来是黑屏。
      preserveDrawingBuffer: true,
    })

    this.currentBasemap = options.basemap
    this.onMoveEnd = options.onMoveEnd

    // 添加控件
    this.map.addControl(new maplibregl.NavigationControl(), 'top-right')
    this.map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left')
    this.map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-right'
    )

    // 在地图移动结束时同步视图状态回调
    this.moveEndHandler = () => {
      if (!this.map || !this.onMoveEnd) return
      const center = this.map.getCenter()
      this.onMoveEnd(
        [center.lng, center.lat],
        this.map.getZoom(),
        this.map.getBearing(),
        this.map.getPitch()
      )
    }
    this.map.on('moveend', this.moveEndHandler)

    // 维护 ready 标志：style 第一次 load 完成后置 true，destroy 时复位
    //
    // 之前用 map.once('load', ...) 注册，但实测在 React.StrictMode 下
    // 这个 once listener 会丢失（推测 maplibre 的 once 实现在某些时序
    // 下会被内部清掉），导致 ready 永远停在 false、identify/box-select
    // 永远挂不上。
    //
    // 改用 map.on('load', ...)，自己判断只翻一次。on listener 在
    // map.remove() 时会随 map 销毁，不存在泄漏。
    const map = this.map
    const onFirstLoad = () => {
      // 防御：destroy 后或换了 map 实例就不要再翻 ready
      if (this.map !== map) return
      this.setReady(true)
    }
    if (map.loaded()) {
      // 极少见，但理论上 init 之后 map 已经 loaded，立即触发
      onFirstLoad()
    } else {
      map.on('load', onFirstLoad)
    }

    return this.map
  }

  /**
   * 销毁地图实例并清理资源
   */
  destroy(): void {
    if (this.map) {
      if (this.moveEndHandler) {
        this.map.off('moveend', this.moveEndHandler)
        this.moveEndHandler = null
      }
      this.map.remove()
      this.map = null
    }
    this.managedSourceIds.clear()
    this.managedLayerIds.clear()
    this.renderLayerToDef.clear()
    this.currentBasemap = null
    this.setReady(false)
  }

  /**
   * 当前 map 是否已 init 且首个 style.load 完成。
   * controller 挂载/卸载之外的代码请优先使用 onReady 订阅
   */
  isReady(): boolean {
    return this.ready
  }

  /**
   * 订阅 ready 状态变化。回调用 ready=true 表示可以挂 controller，
   * ready=false 表示 map 已销毁需要拆 controller。
   * 返回 unsubscribe 函数
   *
   * 订阅瞬间会立即用当前状态触发一次回调，避免调用方再写一段
   * "先检查 isReady() 再注册" 的样板
   */
  onReady(cb: ReadyChangeCallback): () => void {
    this.readyListeners.add(cb)
    cb(this.ready)
    return () => {
      this.readyListeners.delete(cb)
    }
  }

  private setReady(value: boolean): void {
    if (this.ready === value) return
    this.ready = value
    for (const cb of this.readyListeners) {
      try {
        cb(value)
      } catch (err) {
        console.error('[MapEngine] onReady listener threw:', err)
      }
    }
  }

  /**
   * 获取原始 MapLibre 地图实例（用于高级用法）
   */
  getMap(): maplibregl.Map | null {
    return this.map
  }

  /**
   * 追踪扩展直接添加的 MapLibre layer，使其受 setBasemapVisible /
   * setLabelsVisible 保护（跳过而不被意外切换可见性）。
   */
  trackExternalLayer(layerId: string): void {
    this.managedLayerIds.add(layerId)
  }

  /**
   * 追踪扩展直接添加的 MapLibre source，保持 managedSourceIds 一致。
   */
  trackExternalSource(sourceId: string): void {
    this.managedSourceIds.add(sourceId)
  }

  /**
   * 移除外部追踪。
   */
  untrackExternalLayer(layerId: string): void {
    this.managedLayerIds.delete(layerId)
  }

  untrackExternalSource(sourceId: string): void {
    this.managedSourceIds.delete(sourceId)
  }

  // ─── 底图可见性 ──────────────────────────────────────

  /**
   * 切换底图显示/隐藏
   * 隐藏时，地图仅显示纯色背景和用户图层
   */
  setBasemapVisible(visible: boolean): void {
    if (!this.map) return
    this.basemapHidden = !visible

    const style = this.map.getStyle()
    if (!style || !style.layers) return

    for (const layer of style.layers) {
      // 跳过用户管理的图层 — 仅切换底图图层
      if (this.managedLayerIds.has(layer.id)) continue

      this.map.setLayoutProperty(
        layer.id,
        'visibility',
        visible ? 'visible' : 'none'
      )
    }
  }

  /**
   * 获取当前底图可见性状态
   */
  getBasemapVisible(): boolean {
    return !this.basemapHidden
  }

  // ─── 标注可见性 ──────────────────────────────────────

  /**
   * 切换底图上文字/符号标注图层的可见性
   * 对于 vector-style 底图：通过 layout 属性隐藏 symbol/label 图层
   * 对于 raster 底图：标注已烘焙到瓦片中，无法单独切换，
   * 因此需要切换到带/不带标注的 vector 底图
   */
  setLabelsVisible(visible: boolean): void {
    if (!this.map) return
    this.labelsHidden = !visible

    // 对于 raster 底图，标注已烘焙到瓦片中 — 无法单独切换
    // 调用方 (MapView) 负责通过 store 切换底图，以便图层重新同步正确工作
    // 这里仅设置标志位；MapView 通过 needsBasemapSwitch 读取它
    if (this.currentBasemap?.type === 'raster-tiles') {
      return
    }

    // 对于有配对无标注变体的 vector-style 底图，
    // 调用方应通过 store 处理底图切换
    // 这里仅做降级处理：直接切换 symbol 图层
    const style = this.map.getStyle()
    if (!style || !style.layers) return

    for (const layer of style.layers) {
      // 跳过用户管理的图层
      if (this.managedLayerIds.has(layer.id)) continue

      // 通过类型或 id 模式检测标注图层
      const isLabelLayer =
        layer.type === 'symbol' ||
        layer.id.includes('label') ||
        layer.id.includes('text') ||
        layer.id.includes('name') ||
        layer.id.includes('place') ||
        layer.id.includes('poi')

      if (isLabelLayer) {
        this.map.setLayoutProperty(
          layer.id,
          'visibility',
          visible ? 'visible' : 'none'
        )
      }
    }
  }

  /**
   * 获取当前标注可见性状态
   */
  getLabelsVisible(): boolean {
    return !this.labelsHidden
  }

  // ─── 底图设置 ──────────────────────────────────────

  /**
   * 切换底图，保留所有用户图层
   */
  setBasemap(basemap: BasemapSource): void {
    if (!this.map) return
    if (this.currentBasemap?.id === basemap.id) return

    const style = this.buildStyle(basemap)
    this.currentBasemap = basemap

    // 保存当前 camera，避免 setStyle 因远程 style JSON 中的
    // center/zoom 字段重置用户视图
    const savedCenter = this.map.getCenter()
    const savedZoom = this.map.getZoom()
    const savedBearing = this.map.getBearing()
    const savedPitch = this.map.getPitch()

    this.map.setStyle(style)

    // 样式加载后恢复 camera 并重新添加用户图层
    this.map.once('style.load', () => {
      // 恢复 camera
      this.map?.jumpTo({
        center: savedCenter,
        zoom: savedZoom,
        bearing: savedBearing,
        pitch: savedPitch,
      })
      // 清空跟踪集合 — 图层将在下面重新挂载
      this.managedSourceIds.clear()
      this.managedLayerIds.clear()

      // Wait for the style to be fully rendered before re-applying
      // visibility settings — layers must exist in the new style first.
      const applyVisibility = () => {
        if (this.basemapHidden) {
          this.setBasemapVisible(false)
        }
        if (this.labelsHidden && this.currentBasemap?.type !== 'raster-tiles') {
          this.setLabelsVisible(false)
        }
      }

      if (this.map?.isStyleLoaded()) {
        applyVisibility()
      } else {
        this.map?.once('style.load', applyVisibility)
      }

      // 通知 MapView 将所有用户图层重新同步到新样式
      if (this.onStyleReload) {
        this.onStyleReload()
      }
    })
  }

  /**
   * 为给定的底图构建 MapLibre 样式对象
   */
  private buildStyle(basemap: BasemapSource): maplibregl.StyleSpecification | string {
    if (basemap.type === 'vector-style') {
      // Vector style JSON URL — MapLibre 直接加载
      return basemap.url
    }

    // Raster 瓦片 — 构建带 raster source 的最小样式
    return {
      version: 8,
      sources: {
        'basemap-raster': {
          type: 'raster',
          tiles: [basemap.url],
          tileSize: 256,
          attribution: basemap.attribution || '',
        },
      },
      layers: [
        {
          id: 'basemap-raster-layer',
          type: 'raster',
          source: 'basemap-raster',
          minzoom: 0,
          maxzoom: 19,
        },
      ],
    }
  }

  // ─── 图层管理 ──────────────────────────────────────

  /**
   * 将图层定义同步到地图
   * 分发给对应 renderer 做 attach；vector 数据的 geojson source 由 MapEngine
   * 统一管理（因为多个 renderer 切换时 source 可以复用），raster / cluster
   * 这种特殊 source 则由 renderer 自己在 attach 里创建
   */
  syncLayer(layer: MapLayerDefinition): void {
    if (!this.map) {
      console.warn('[MapEngine] syncLayer called but map is null, layer:', layer.id, layer.name)
      return
    }

    const renderer = getRenderer(layer.style.renderType)
    if (!renderer) {
      console.warn(
        '[MapEngine] syncLayer: no renderer registered for renderType',
        layer.style.renderType,
        'layer:',
        layer.id,
      )
      return
    }

    const ctx = this.buildRendererContext()

    // vector 类 renderer（fill/line/circle/heatmap/graduated/categorized/extrusion）
    // 共用 geojson source；cluster/raster 的 source 由 renderer 自己管理
    const managesOwnSource = ['cluster', 'raster'].includes(layer.style.renderType)

    if (!managesOwnSource && layer.data.kind === 'vector') {
      this.ensureGeoJSONSource(layer)
    }

    renderer.attach(layer, ctx)
  }

  /**
   * 从地图中移除图层
   *
   * 通过 id 前缀 `layer-${layer.id}-` 扫描 managedLayerIds，所以不管是哪个
   * renderer 产出的子层、子层数量是多少，都会被一并清除
   */
  removeMapLayer(layerId: string): void {
    if (!this.map) {
      console.warn('[MapEngine] removeMapLayer called but map is null, layerId:', layerId)
      return
    }

    const sourceId = sourceIdFor(layerId)
    const layerPrefix = `layer-${layerId}-`

    // 移除该 source 的所有渲染图层
    const layerIdsToRemove: string[] = []
    for (const id of this.managedLayerIds) {
      if (id.startsWith(layerPrefix)) {
        layerIdsToRemove.push(id)
      }
    }

    for (const id of layerIdsToRemove) {
      if (this.map.getLayer(id)) {
        this.map.removeLayer(id)
      }
      this.managedLayerIds.delete(id)
      this.renderLayerToDef.delete(id)
    }

    // 移除 source
    if (this.map.getSource(sourceId)) {
      this.map.removeSource(sourceId)
    }
    this.managedSourceIds.delete(sourceId)

    console.log(
      '[MapEngine] Removed layer:',
      layerId,
      'render-layers:',
      layerIdsToRemove,
    )
  }

  /**
   * 仅移除渲染图层，保留 source（用于 renderType 切换时，
   * 避免 source 重新解析导致 match 表达式在数据未就绪时失效）
   */
  removeRenderLayersOnly(layerId: string): void {
    if (!this.map) {
      console.warn('[MapEngine] removeRenderLayersOnly called but map is null, layerId:', layerId)
      return
    }

    const layerPrefix = `layer-${layerId}-`
    const layerIdsToRemove: string[] = []
    for (const id of this.managedLayerIds) {
      if (id.startsWith(layerPrefix)) {
        layerIdsToRemove.push(id)
      }
    }

    for (const id of layerIdsToRemove) {
      if (this.map.getLayer(id)) {
        this.map.removeLayer(id)
      }
      this.managedLayerIds.delete(id)
      this.renderLayerToDef.delete(id)
    }

    console.log(
      '[MapEngine] Removed render layers only (kept source):',
      layerId,
      'render-layers:',
      layerIdsToRemove,
    )
  }

  /**
   * 设置地图中图层的可见性
   */
  setLayerVisibility(layerId: string, visible: boolean): void {
    if (!this.map) return

    const layerPrefix = `layer-${layerId}-`
    for (const id of this.managedLayerIds) {
      if (id.startsWith(layerPrefix)) {
        if (this.map.getLayer(id)) {
          this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
        }
      }
    }
  }

  /**
   * 通过 renderer 更新图层 paint 属性。若 renderType 变化
   * （例如 fill→graduated），MapView 会先 removeMapLayer 再 syncLayer，
   * 不走这个 update 路径
   */
  updateLayerPaint(layer: MapLayerDefinition): void {
    if (!this.map) return
    const renderer = getRenderer(layer.style.renderType)
    if (!renderer) return
    renderer.update(layer, this.buildRendererContext())
  }

  /**
   * 重新排序渲染图层。对每个 layer def 取它的 render layer id 列表
   * （通过 renderer.listRenderLayerIds），按 bottom → top 顺序 moveLayer
   *
   * Stage 3.9 的老实现靠 hard-coded suffix 顺序（fill/line/stroke/circle）
   * 新版交给 renderer 自己决定顺序：`listRenderLayerIds` 返回的顺序即
   * 从底到顶
   */
  applyLayerOrder(orderedIds: string[]): void {
    if (!this.map) return

    // 我们不直接拿到 def（applyLayerOrder 只接收 id 数组），所以这里只能按
    // managed id 的前缀扫描；顺序仍按"先出现的 id 先 move"，在 renderer 内
    // 的子层互相之间按加入 map 的原始顺序（循环 move 会保持相对顺序）
    for (const layerId of orderedIds) {
      const prefix = `layer-${layerId}-`
      // 收集这个 def 产生的所有 render id（按 managedLayerIds 里的插入顺序）
      const subIds: string[] = []
      for (const id of this.managedLayerIds) {
        if (id.startsWith(prefix)) subIds.push(id)
      }
      for (const renderId of subIds) {
        if (this.map.getLayer(renderId)) {
          try {
            this.map.moveLayer(renderId) // no beforeId → move to top
          } catch (err) {
            console.warn('[MapEngine] moveLayer failed:', renderId, err)
          }
        }
      }
    }
  }

  // ─── 视图控制 ──────────────────────────────────────

  /**
   * 将地图视图适配到给定的边界范围（带内边距）
   */
  fitBounds(bounds: [number, number, number, number], options?: { padding?: number; animate?: boolean }): void {
    if (!this.map) return

    this.map.fitBounds(
      [
        [bounds[0], bounds[1]], // SW
        [bounds[2], bounds[3]], // NE
      ],
      {
        padding: options?.padding ?? 50,
        animate: options?.animate ?? true,
        maxZoom: 18,
      }
    )
  }

  /**
   * 飞行到指定位置
   */
  flyTo(center: [number, number], zoom?: number): void {
    if (!this.map) return
    this.map.flyTo({ center, zoom: zoom ?? this.map.getZoom() })
  }

  /**
   * 获取当前地图上由 MapEngine 管理的所有渲染图层 ID
   * IdentifyController 使用此方法获取 `map.queryRenderedFeatures({ layers: [...] })`
   * 的白名单，避免误查底图中的 symbol / POI / water 等图层
   */
  getManagedLayerIds(): string[] {
    return Array.from(this.managedLayerIds)
  }

  /**
   * 从渲染图层 ID 反推它属于哪个 MapLayerDefinition
   * 使用 renderLayerToDef Map 做 O(1) 查找，替代原先的 KNOWN_SUFFIXES 穷举
   * 兜底：若 map 中没有（如热更新过渡期），尝试按 `layer-<defId>-<suffix>`
   * 模式粗解析（取最后一个 `-` 之前的部分）
   */
  getDefIdFromRenderLayerId(renderLayerId: string): string | null {
    if (!renderLayerId.startsWith('layer-')) return null
    // 主路径：O(1) Map 查找
    const defId = this.renderLayerToDef.get(renderLayerId)
    if (defId) return defId
    // 兜底：热更新过渡期 renderLayerToDef 尚未构建，尝试粗解析
    const withoutPrefix = renderLayerId.slice('layer-'.length)
    const lastDash = withoutPrefix.lastIndexOf('-')
    if (lastDash > 0) return withoutPrefix.slice(0, lastDash)
    return null
  }



  private buildRendererContext(): RendererContext {
    const map = this.map!
    const self = this
    return {
      map,
      addRenderLayer(spec) {
        map.addLayer(spec as any)
        self.managedLayerIds.add(spec.id)
      },
      registerSourceId(sourceId) {
        self.managedSourceIds.add(sourceId)
      },
      registerRenderLayerId(defId, renderLayerId) {
        self.renderLayerToDef.set(renderLayerId, defId)
      },
    }
  }

  /**
   * 确保 geojson source 存在并同步数据。只对 vector 数据类型有效
   *
   * 注意：若上一次是 cluster renderer（source 带 cluster=true），现在切
   * 回普通 vector renderer，直接复用会继续返回聚合后的 feature。所以
   * vector renderer 发现 source 带 cluster 选项时必须 remove + 重建
   */
  private ensureGeoJSONSource(layer: MapLayerDefinition): void {
    if (!this.map) return
    if (layer.data.kind !== 'vector') return

    const sourceId = sourceIdFor(layer.id)
    const existingSource = this.map.getSource(sourceId) as
      | (maplibregl.GeoJSONSource & { _options?: { cluster?: boolean } })
      | undefined
    const vectorData = layer.data as ParsedVectorData

    if (existingSource) {
      // MapLibre 的 GeoJSONSource 实例把原始 options 存在 _options 上
      // 如果是 cluster source，vector renderer 不能直接用，要 remove 再重建
      const isCluster = Boolean(existingSource._options?.cluster)
      if (isCluster) {
        this.map.removeSource(sourceId)
        this.managedSourceIds.delete(sourceId)
        this.map.addSource(sourceId, {
          type: 'geojson',
          data: vectorData.geojson as any,
          generateId: true,
        })
        this.managedSourceIds.add(sourceId)
        return
      }
      existingSource.setData(vectorData.geojson as any)
    } else {
      this.map.addSource(sourceId, {
        type: 'geojson',
        data: vectorData.geojson as any,
        generateId: true,
      })
      this.managedSourceIds.add(sourceId)
    }
  }

  /**
   * 在渲染模式切换（如 fill → graduated，或 circle → cluster）时，
   * MapView 需要能主动把旧 renderer 的子层清掉，但保留 source（由 syncLayer
   * 的 ensureGeoJSONSource 负责重用）。单独的 cluster/raster 走 removeMapLayer
   * 全清路径；vector 类之间切换只清子层即可
   *
   * 策略简化：统一走 "先 removeMapLayer 全清（含 source）+ 再 syncLayer"
   * 的路径，代价是 source 会 re-add 一次，但 geojson 很小，可接受
   */
  removeLayerKeepingStore(layerId: string): void {
    this.removeMapLayer(layerId)
  }
}

// ─── 单例导出 ──────────────────────────────────────

export const mapEngine = new MapEngine()
