import { useEffect, useRef, useCallback } from 'react'
import { Map as MapIcon, EyeOff, Hand, MousePointer, BoxSelect } from 'lucide-react'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useMapStore } from '@/stores/mapStore'
import { useSettingsStore } from '@/stores/settingsStore'  // 新增：导入settingsStore
import { BUILTIN_BASEMAPS } from '@/services/geo'  // 新增：导入底图列表
import { mapEngine } from './engine/MapEngine'
import { loadGeoFiles } from '@/services/geo'
import { useIdentify, useBoxSelect, FeatureAttributePanel, BoxSelectResultPanel } from './identify'
import { ExportButton } from './export'
import { PinnedImagePanel } from './PinnedImagePanel'

/**
 * MapView — the primary map visualization panel.
 *
 * Responsibilities:
 * - Mount the MapEngine onto a container div
 * - Subscribe to mapStore changes and sync to MapEngine
 * - Handle file drop for data loading
 * - Display overlay controls and empty state
 */
export function MapView() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const isInitialized = useRef(false)
  /**
   * 注意：之前这里有一个 `mapReady` useState，用来在 map.on('load') 回调里
   * 翻成 true，再透传给 useIdentify / useBoxSelect。但在 React.StrictMode 下
   * init effect 会 mount → cleanup → mount 两次，第一次的 setMapReady 闭包
   * 在 cleanup 里就失效了，导致后续 controller 永远挂不上。现在改为让
   * MapEngine 自己持有 ready 状态、hook 通过 mapEngine.onReady 订阅。
   */

  // Tracks which layer ids are currently on the map. Shared between the
  // init effect (initial seed) and the layer-sync effect (diff-based
  // add/remove). Must be declared before the effects that reference it.
  const prevLayerIdsRef = useRef<string[]>([])
  /**
   * 记录每个 layer 上次挂上去时的 renderType。renderType 发生变化时
   * （e.g. fill → graduated、circle → cluster），MapLibre 的 layer type
   * 不可变，必须 remove+re-add，走 update 路径会死在 "can't change type"。
   */
  const prevRenderTypeRef = useRef<Map<string, string>>(new Map())
  const pendingStyleLoadRef = useRef(false)

  const layers = useMapStore((s) => s.layers)
  const basemap = useMapStore((s) => s.basemap)
  const basemapVisible = useMapStore((s) => s.basemapVisible)
  const viewState = useMapStore((s) => s.viewState)
  const identifyActive = useMapStore((s) => s.identifyActive)
  const boxSelectActive = useMapStore((s) => s.boxSelectActive)
  const identifiedFeatures = useMapStore((s) => s.identifiedFeatures)
  const identifyPanelVisible = useMapStore((s) => s.identifyPanelVisible)
  const boxSelectedFeatures = useMapStore((s) => s.boxSelectedFeatures)
  const boxSelectPanelVisible = useMapStore((s) => s.boxSelectPanelVisible)
  const addLayers = useMapStore((s) => s.addLayers)
  const setViewState = useMapStore((s) => s.setViewState)
  const setBasemapVisible = useMapStore((s) => s.setBasemapVisible)
  const setIdentifyActive = useMapStore((s) => s.setIdentifyActive)
  const setBoxSelectActive = useMapStore((s) => s.setBoxSelectActive)
  const clearIdentifiedFeatures = useMapStore((s) => s.clearIdentifiedFeatures)
  const clearBoxSelectedFeatures = useMapStore((s) => s.clearBoxSelectedFeatures)
  const pinnedImages = useMapStore((s) => s.pinnedImages)
  const removePinnedImage = useMapStore((s) => s.removePinnedImage)
  const renamePinnedImage = useMapStore((s) => s.renamePinnedImage)

  // 新增：从 settingsStore 读取 showMapLabels 设置
  const showMapLabels = useSettingsStore((s) => s.appearance.showMapLabels)

  // 新增：应用 showMapLabels 设置（处理 raster 和 vector 底图）
  const applyShowMapLabels = useCallback((showLabels: boolean, currentBasemap: BasemapSource) => {
    const store = useMapStore.getState()
    
    if (currentBasemap.type === 'raster-tiles') {
      // Raster 底图：切换到 vector 底图变体
      const targetId = showLabels ? 'carto-voyager' : 'carto-voyager-nolabels'
      const target = BUILTIN_BASEMAPS.find((b) => b.id === targetId)
      if (target && target.id !== currentBasemap.id) {
        store.setBasemap(target)
      }
      return
    }
    
    // Vector 底图：尝试 -nolabels 变体
    const currentId = currentBasemap.id
    if (!showLabels) {
      const noLabelsId = currentId + '-nolabels'
      const noLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === noLabelsId)
      if (noLabelsBasemap) {
        store.setBasemap(noLabelsBasemap)
        return
      }
    } else if (currentId.endsWith('-nolabels')) {
      const withLabelsId = currentId.replace('-nolabels', '')
      const withLabelsBasemap = BUILTIN_BASEMAPS.find((b) => b.id === withLabelsId)
      if (withLabelsBasemap) {
        store.setBasemap(withLabelsBasemap)
        return
      }
    }
    
    // 降级：直接切换 symbol 图层
    const map = mapEngine.getMap()
    if (map && map.isStyleLoaded()) {
      mapEngine.setLabelsVisible(showLabels)
    }
  }, [])

  // ─── Initialize MapEngine ─────────────────────────────────────

  useEffect(() => {
    if (!mapContainer.current || isInitialized.current) return

    const map = mapEngine.init({
      container: mapContainer.current,
      basemap,
      center: viewState.center,
      zoom: viewState.zoom,
      bearing: viewState.bearing,
      pitch: viewState.pitch,
      onMoveEnd: (center, zoom, bearing, pitch) => {
        setViewState({ center, zoom, bearing, pitch })
      },
    })

    // After initial style loads, sync any existing layers.
    // CRITICAL: we must keep `prevLayerIdsRef` in lock-step with what's
    // actually on the map. If this callback silently adds layers without
    // updating the ref, the later layer-sync effect's diff will never see
    // them in `prevIds` and therefore never fire `removeMapLayer` when
    // they get removed from the store. (Stage 3.9 bug: "删除图层但地图
    // 还在".)
    map.on('load', () => {
      const currentLayers = useMapStore.getState().layers
      for (const layer of currentLayers) {
        mapEngine.syncLayer(layer)
        prevRenderTypeRef.current.set(layer.id, layer.style.renderType)
      }
      prevLayerIdsRef.current = currentLayers.map((l) => l.id)
      
      // 新增：应用 showMapLabels 设置（从持久化存储读取）
      // 这确保地图初始化时就能正确应用用户的 label 显示偏好
      const { appearance, basemap } = useSettingsStore.getState()
      applyShowMapLabels(appearance.showMapLabels, basemap)

      // 注意：identify / box-select controller 的挂载不再依赖此回调，
      // MapEngine 自身在 init() 里订阅 map.once('load') 翻 ready 标志，
      // useIdentify / useBoxSelect 通过 mapEngine.onReady 自行同步
    })

    // Register style-reload callback so user layers survive basemap switches.
    mapEngine.setOnStyleReload(() => {
      const currentLayers = useMapStore.getState().layers
      prevRenderTypeRef.current.clear()
      for (const layer of currentLayers) {
        mapEngine.syncLayer(layer)
        mapEngine.setLayerVisibility(layer.id, layer.visible)
        prevRenderTypeRef.current.set(layer.id, layer.style.renderType)
      }
      prevLayerIdsRef.current = currentLayers.map((l) => l.id)
      mapEngine.applyLayerOrder(currentLayers.map((l) => l.id))
    })

    isInitialized.current = true

    return () => {
      mapEngine.destroy()
      isInitialized.current = false
      // Reset so a subsequent remount starts clean.
      prevLayerIdsRef.current = []
      prevRenderTypeRef.current.clear()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Attach identify controller (订阅 mapEngine.onReady 自行处理 ready 时机)
  useIdentify()
  // Attach box-select controller
  useBoxSelect()

  // ─── Sync basemap changes ─────────────────────────────────────

  const prevBasemapRef = useRef(basemap.id)

  useEffect(() => {
    if (prevBasemapRef.current === basemap.id) return
    prevBasemapRef.current = basemap.id

    mapEngine.setBasemap(basemap)

    // Re-sync all layers after basemap change (style.load event clears them).
    // Same reason as the init effect: keep `prevLayerIdsRef` aligned with
    // what's actually on the map, otherwise the next remove will no-op.
    const map = mapEngine.getMap()
    if (map) {
      map.once('style.load', () => {
        const currentLayers = useMapStore.getState().layers
        for (const layer of currentLayers) {
          mapEngine.syncLayer(layer)
          prevRenderTypeRef.current.set(layer.id, layer.style.renderType)
        }
        prevLayerIdsRef.current = currentLayers.map((l) => l.id)
      })
    }
  }, [basemap])

  // ─── Sync basemap visibility ─────────────────────────────────

  useEffect(() => {
    const map = mapEngine.getMap()
    if (!map) return

    if (map.isStyleLoaded()) {
      mapEngine.setBasemapVisible(basemapVisible)
    } else {
      const handler = () => mapEngine.setBasemapVisible(basemapVisible)
      map.once('style.load', handler)
      return () => { map.off('style.load', handler) }
    }
  }, [basemapVisible])

  // ─── Sync identify/box-select mode cursor ────────────────────

  useEffect(() => {
    const map = mapEngine.getMap()
    if (!map) return

    const canvas = map.getCanvas()
    if (identifyActive) {
      canvas.style.cursor = 'crosshair'
    } else if (boxSelectActive) {
      canvas.style.cursor = 'crosshair'
    } else {
      canvas.style.cursor = ''
    }

    return () => {
      canvas.style.cursor = ''
    }
  }, [identifyActive, boxSelectActive])

  // ─── Sync layer changes ───────────────────────────────────────

  useEffect(() => {
    const map = mapEngine.getMap()
    if (!map) return

    if (!map.isStyleLoaded()) {
      // Style not loaded yet — defer sync until style is ready.
      // Always register a fresh callback so that layers added during
      // style loading are picked up (fixes race condition where
      // add_layer notification arrives before style.load).
      const handler = () => {
        pendingStyleLoadRef.current = false
        const latestLayers = useMapStore.getState().layers
        syncLayersToMap(latestLayers)
      }
      // Remove any previous listener to avoid duplicates, then re-add.
      map.off('style.load', handler)
      if (!pendingStyleLoadRef.current) {
        pendingStyleLoadRef.current = true
        map.once('style.load', handler)
      }
      return
    }

    syncLayersToMap(layers)

    function syncLayersToMap(currentLayers: typeof layers) {
      const currentIds = new Set(currentLayers.map((l) => l.id))
      const prevIds = new Set(prevLayerIdsRef.current)

      // Remove layers that are no longer in the store
      for (const id of prevIds) {
        if (!currentIds.has(id)) {
          console.log('[MapView] Removing layer from map:', id)
          mapEngine.removeMapLayer(id)
          prevRenderTypeRef.current.delete(id)
        }
      }

      // Add or update layers
      for (const layer of currentLayers) {
        const prevRenderType = prevRenderTypeRef.current.get(layer.id)
        if (!prevIds.has(layer.id)) {
          // New layer — add to map
          console.log('[MapView] Adding new layer to map:', layer.id, layer.name)
          mapEngine.syncLayer(layer)
          prevRenderTypeRef.current.set(layer.id, layer.style.renderType)
        } else if (prevRenderType && prevRenderType !== layer.style.renderType) {
          // renderType switched (e.g. fill → graduated, circle → cluster).
          // MapLibre layer types are immutable, so we MUST remove + re-add;
          // renderer.update() is for paint-only hot patches.
          console.log(
            '[MapView] renderType changed for',
            layer.id,
            prevRenderType,
            '→',
            layer.style.renderType,
            '— rebuild',
          )
          mapEngine.removeMapLayer(layer.id)
          mapEngine.syncLayer(layer)
          mapEngine.setLayerVisibility(layer.id, layer.visible)
          prevRenderTypeRef.current.set(layer.id, layer.style.renderType)
        } else {
          // Existing layer, same renderType — hot-patch visibility/paint.
          mapEngine.setLayerVisibility(layer.id, layer.visible)
          mapEngine.updateLayerPaint(layer)
        }
      }

      // 仅当图层顺序真正变化时才触发 moveLayer，避免不必要的 GPU 开销
      const newOrder = currentLayers.map((l) => l.id)
      const prevOrder = prevLayerIdsRef.current
      const orderChanged =
        newOrder.length !== prevOrder.length ||
        newOrder.some((id, i) => id !== prevOrder[i])
      if (orderChanged) {
        mapEngine.applyLayerOrder(newOrder)
      }

      prevLayerIdsRef.current = newOrder
    }
  }, [layers])

  // ─── Sync showMapLabels setting to map ─────────────────────────
  // 新增：订阅 settingsStore.showMapLabels 的变化，确保在设置加载后（可能在地图初始化之后）也能正确应用
  useEffect(() => {
    const map = mapEngine.getMap()
    if (!map) return

    const applyLabels = () => {
      mapEngine.setLabelsVisible(showMapLabels)
    }

    if (map.isStyleLoaded()) {
      applyLabels()
    } else {
      // 如果样式还没加载完成，等加载完成后再应用
      const handler = () => {
        applyLabels()
      }
      map.once('style.load', handler)
      return () => {
        map.off('style.load', handler)
      }
    }
  }, [showMapLabels])

  // ─── File Drop Handler ────────────────────────────────────────

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()

      const files = Array.from(e.dataTransfer.files)
      if (files.length === 0) return

      try {
        const newLayers = await loadGeoFiles(files)
        if (newLayers.length > 0) {
          addLayers(newLayers)

          // Fit to the bounds of the first new layer
          const firstLayer = newLayers[0]
          if (firstLayer.data.kind === 'vector') {
            const { bbox } = firstLayer.data
            mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY])
          }
        }
      } catch (err) {
        console.error('Failed to load dropped files:', err)
      }
    },
    [addLayers]
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  // ─── Render ───────────────────────────────────────────────────

  return (
    <div
      className="w-full h-full relative bg-bg-primary"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      <div ref={mapContainer} className="w-full h-full" />

      {/* Map overlay controls */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
        <div className="glass rounded-lg px-3 py-1.5 text-xs text-text-secondary">
          <span className="text-accent-geo font-display font-semibold">OpenGIS</span>
          <span className="mx-2 text-border">|</span>
          <span>{layers.length} layer{layers.length !== 1 ? 's' : ''}</span>
        </div>

        {/* Basemap toggle */}
        <button
          onClick={() => setBasemapVisible(!basemapVisible)}
          className={`glass rounded-lg w-8 h-8 flex items-center justify-center transition-colors ${
            basemapVisible
              ? 'text-text-secondary hover:text-accent-primary'
              : 'text-text-muted/40 hover:text-text-secondary'
          }`}
          title={basemapVisible ? 'Hide basemap' : 'Show basemap'}
        >
          {basemapVisible ? <MapIcon className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
        </button>

        {/* Identify mode toggle */}
        <button
          onClick={() => setIdentifyActive(!identifyActive)}
          className={`glass rounded-lg w-8 h-8 flex items-center justify-center transition-colors ${
            identifyActive
              ? 'text-accent-primary bg-accent-primary/15 ring-1 ring-accent-primary/40'
              : 'text-text-secondary hover:text-accent-primary'
          }`}
          title={identifyActive ? 'Switch to pan mode' : 'Switch to identify mode'}
        >
          {identifyActive ? <MousePointer className="w-3.5 h-3.5" /> : <Hand className="w-3.5 h-3.5" />}
        </button>

        {/* Box select mode toggle */}
        <button
          onClick={() => setBoxSelectActive(!boxSelectActive)}
          className={`glass rounded-lg w-8 h-8 flex items-center justify-center transition-colors ${
            boxSelectActive
              ? 'text-accent-primary bg-accent-primary/15 ring-1 ring-accent-primary/40'
              : 'text-text-secondary hover:text-accent-primary'
          }`}
          title={boxSelectActive ? 'Switch to pan mode' : 'Switch to box select mode'}
        >
          <BoxSelect className="w-3.5 h-3.5" />
        </button>

        {/* Export map (PNG/JPG) */}
        <ExportButton />
      </div>

      {/* Feature Attribute Panel (single-click identify) */}
      <FeatureAttributePanel
        features={identifiedFeatures}
        visible={identifyPanelVisible}
        onClose={clearIdentifiedFeatures}
      />

      {/* Box Select Result Panel (box-select mode) */}
      <BoxSelectResultPanel
        features={boxSelectedFeatures}
        visible={boxSelectPanelVisible}
        onClose={clearBoxSelectedFeatures}
      />

      {/* Pinned Image Panels (from "Pin to map" in chat) */}
      {pinnedImages.map((img) => (
        <PinnedImagePanel
          key={img.id}
          image={img}
          onClose={removePinnedImage}
          onRename={renamePinnedImage}
        />
      ))}
    </div>
  )
}
