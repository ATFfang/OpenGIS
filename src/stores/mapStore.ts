/**
 * 地图状态管理 Store
 *
 * 集中管理地图图层、底图和基础视图状态。
 * 这是所有地图相关状态的单一数据源。
 * MapEngine (位于 features/map/engine/) 订阅此 store
 * 并同步更改到 MapLibre 实例。
 */
import { create } from 'zustand'
import type { MapLayerDefinition, LayerStyle, BasemapSource } from '@/services/geo'
import { BUILTIN_BASEMAPS, resetLayerColorIndex } from '@/services/geo'
import type { FeatureInfo } from '@/features/map/identify/FeatureAttributePanel'
import type { BoxSelectFeatureInfo } from '@/features/map/identify/BoxSelectResultPanel'
import type { PinnedImage } from '@/features/map/PinnedImagePanel'

// ─── 视图状态定义 ──────────────────────────────────────────────

export interface ViewState {
  center: [number, number]
  zoom: number
  bearing: number
  pitch: number
}

// ─── Store 接口定义 ─────────────────────────────────────────────

interface MapStore {
  // 状态定义
  layers: MapLayerDefinition[]
  activeLayerId: string | null
  basemap: BasemapSource
  basemapVisible: boolean
  viewState: ViewState
  labelsVisible: boolean
  identifyActive: boolean
  boxSelectActive: boolean
  identifiedFeatures: FeatureInfo[]
  identifyPanelVisible: boolean
  boxSelectedFeatures: BoxSelectFeatureInfo[]
  boxSelectPanelVisible: boolean
  pinnedImages: PinnedImage[]

  // 图层操作方法
  addLayer: (layer: MapLayerDefinition) => void
  addLayers: (layers: MapLayerDefinition[]) => void
  removeLayer: (id: string) => void
  setActiveLayer: (id: string | null) => void
  setLayerVisibility: (id: string, visible: boolean) => void
  setLayerOpacity: (id: string, opacity: number) => void
  updateLayerStyle: (id: string, style: Partial<LayerStyle>) => void
  renameLayer: (id: string, name: string) => void
  reorderLayers: (fromIndex: number, toIndex: number) => void
  clearLayers: () => void

  // 底图操作方法
  setBasemap: (basemap: BasemapSource) => void
  setBasemapVisible: (visible: boolean) => void
  setLabelsVisible: (visible: boolean) => void

  // 识别操作方法
  setIdentifyActive: (active: boolean) => void
  setBoxSelectActive: (active: boolean) => void
  setIdentifiedFeatures: (features: FeatureInfo[]) => void
  clearIdentifiedFeatures: () => void
  setBoxSelectedFeatures: (features: BoxSelectFeatureInfo[]) => void
  clearBoxSelectedFeatures: () => void

  // 固定图片操作方法
  addPinnedImage: (image: PinnedImage) => void
  removePinnedImage: (id: string) => void
  renamePinnedImage: (id: string, newName: string) => void
  clearPinnedImages: () => void

  // 视图操作方法
  setViewState: (viewState: Partial<ViewState>) => void

  // 计算辅助方法
  getLayerById: (id: string) => MapLayerDefinition | undefined
  getVisibleLayers: () => MapLayerDefinition[]
}

// ─── 默认底图配置 ──────────────────────────────────────────────

const DEFAULT_BASEMAP = BUILTIN_BASEMAPS.find((b) => b.id === 'carto-voyager-nolabels')!

// ─── Store 实现 ─────────────────────────────────────────────────

export const useMapStore = create<MapStore>((set, get) => ({
  // 初始状态
  layers: [],
  activeLayerId: null,
  basemap: DEFAULT_BASEMAP,
  basemapVisible: true,
  labelsVisible: true,
  identifyActive: false,
  boxSelectActive: false,
  identifiedFeatures: [],
  identifyPanelVisible: false,
  boxSelectedFeatures: [],
  boxSelectPanelVisible: false,
  pinnedImages: [],
  viewState: {
    center: [116.4, 39.9], // Beijing
    zoom: 4,
    bearing: 0,
    pitch: 0,
  },

  // ─── 图层操作方法实现 ─────────────────────────────────────────

  addLayer: (layer) =>
    set((state) => {
      const idx = state.layers.findIndex((l) => l.id === layer.id)
      if (idx !== -1) {
        // Replace existing entry with same ID
        const layers = [...state.layers]
        if (layers[idx] !== layer) _releaseLayerResources(layers[idx], layer)
        layers[idx] = layer
        return { layers, activeLayerId: layer.id }
      }
      return { layers: [...state.layers, layer], activeLayerId: layer.id }
    }),

  addLayers: (incoming) =>
    set((state) => {
      // 与 addLayer 保持一致的去重语义：相同 id 替换而非重复追加，
      // 避免拖拽多文件 / id 碰撞时产生两条同 id 记录，进而让
      // MapView 的 diff（基于 Set）和 removeLayer 的 filter 行为错乱。
      const layers = [...state.layers]
      for (const layer of incoming) {
        const idx = layers.findIndex((l) => l.id === layer.id)
        if (idx !== -1) {
          if (layers[idx] !== layer) _releaseLayerResources(layers[idx], layer)
          layers[idx] = layer
        } else {
          layers.push(layer)
        }
      }
      return {
        layers,
        activeLayerId:
          incoming.length > 0 ? incoming[incoming.length - 1].id : state.activeLayerId,
      }
    }),

  removeLayer: (id) =>
    set((state) => {
      _releaseLayerResources(state.layers.find((l) => l.id === id))
      return {
        layers: state.layers.filter((l) => l.id !== id),
        activeLayerId: state.activeLayerId === id ? null : state.activeLayerId,
      }
    }),

  setActiveLayer: (id) =>
    set({ activeLayerId: id }),

  setLayerVisibility: (id, visible) =>
    set((state) => ({
      layers: state.layers.map((l) => (l.id === id ? { ...l, visible } : l)),
    })),

  setLayerOpacity: (id, opacity) =>
    set((state) => ({
      layers: state.layers.map((l) =>
        l.id === id ? { ...l, style: { ...l.style, opacity } } : l
      ),
    })),

  updateLayerStyle: (id, styleUpdates) =>
    set((state) => ({
      layers: state.layers.map((l) =>
        l.id === id ? { ...l, style: { ...l.style, ...styleUpdates } } : l
      ),
    })),

  renameLayer: (id, name) =>
    set((state) => ({
      layers: state.layers.map((l) => (l.id === id ? { ...l, name } : l)),
    })),

  reorderLayers: (fromIndex, toIndex) =>
    set((state) => {
      const layers = [...state.layers]
      const [moved] = layers.splice(fromIndex, 1)
      layers.splice(toIndex, 0, moved)
      return { layers }
    }),

  clearLayers: () => {
    resetLayerColorIndex()
    useMapStore.getState().layers.forEach((layer) => _releaseLayerResources(layer))
    set({ layers: [], activeLayerId: null })
  },

  // ─── 底图操作方法实现 ─────────────────────────────────────────

  setBasemap: (basemap) =>
    set({ basemap }),

  setBasemapVisible: (visible) =>
    set({ basemapVisible: visible }),

  setLabelsVisible: (visible) =>
    set({ labelsVisible: visible }),

  // ─── 识别操作方法实现 ─────────────────────────────────────────

  setIdentifyActive: (active) =>
    set({ identifyActive: active, ...(active ? { boxSelectActive: false } : {}) }),

  setBoxSelectActive: (active) =>
    set({ boxSelectActive: active, ...(active ? { identifyActive: false } : {}) }),

  setIdentifiedFeatures: (features) =>
    set({ identifiedFeatures: features, identifyPanelVisible: features.length > 0 }),

  clearIdentifiedFeatures: () =>
    set({ identifiedFeatures: [], identifyPanelVisible: false }),

  setBoxSelectedFeatures: (features) =>
    set({ boxSelectedFeatures: features, boxSelectPanelVisible: features.length > 0 }),

  clearBoxSelectedFeatures: () =>
    set({ boxSelectedFeatures: [], boxSelectPanelVisible: false }),

  // ─── 固定图片操作方法实现 ─────────────────────────────────────

  addPinnedImage: (image) =>
    set((state) => ({
      pinnedImages: [...state.pinnedImages, image],
    })),

  removePinnedImage: (id) =>
    set((state) => ({
      pinnedImages: state.pinnedImages.filter((img) => img.id !== id),
    })),

  renamePinnedImage: (id, newName) =>
    set((state) => ({
      pinnedImages: state.pinnedImages.map((img) =>
        img.id === id ? { ...img, name: newName } : img
      ),
    })),

  clearPinnedImages: () =>
    set({ pinnedImages: [] }),

  // ─── 视图操作方法实现 ─────────────────────────────────────────

  setViewState: (viewState) =>
    set((state) => ({
      viewState: { ...state.viewState, ...viewState },
    })),

  // ─── 计算辅助方法实现 ─────────────────────────────────────────

  getLayerById: (id) => get().layers.find((l) => l.id === id),

  getVisibleLayers: () => get().layers.filter((l) => l.visible),
}))

// ─── 持久化 & 工作区作用域隔离 ──────────────────────────────────
//
// 修复两个问题：
//   1) 图层零持久化：重启 / 重开工作区后图层全部消失。
//   2) 跨工作区泄漏：切换工作区时上一工作区的图层残留进新工作区。
//
// 图层是工作区作用域：打开工作区时从 `<workspace>/.opengis/map-layers.json`
// 加载，变更时防抖写盘，切换工作区时先 flush 上一份状态、清空、再加载新的。

import { useAssetStore } from './assetStore'
import { loadLayers, persistLayers, flushLayers } from '@/services/layerPersistence'
import { loadMapView, persistMapView, flushMapView } from '@/services/mapViewPersistence'
import { releaseVectorGeoJSON } from '@/services/geo'
import { releaseRasterBuffer } from '@/services/geo/rasterSourceRegistry'
import { releaseImageUrl } from '@/services/rpc/handlers/_image_url'

// 加载完成前不要把"空图层"写回磁盘，否则会用空数据覆盖已有持久化。
let _layerPersistReady = false
let _viewPersistReady = false
let _isApplyingPersistedView = false

function _releaseLayerResources(layer: MapLayerDefinition | undefined, replacement?: MapLayerDefinition) {
  if (!layer) return
  if (layer.data.kind === 'vector') {
    releaseVectorGeoJSON(layer.data.dataHandle)
    return
  }
  // tiles3d / pointcloud 由 deck.gl overlay 自管资源，无需在此释放缓冲/blob
  if (layer.data.kind !== 'raster') {
    return
  }
  if (
    layer.data.sourceBufferId
    && replacement?.data.kind === 'raster'
    && replacement.data.sourceBufferId === layer.data.sourceBufferId
  ) {
    return
  }
  releaseRasterBuffer(layer.data.sourceBufferId)
  const imageUrl = layer.data.imageUrl
  const ext = layer.meta.extension?.toLowerCase()
  const isOwnedGeoTiffBlob =
    layer.sourceType === 'geotiff'
    && (ext === '.tif' || ext === '.tiff')
    && typeof imageUrl === 'string'
    && imageUrl.startsWith('blob:')
  if (isOwnedGeoTiffBlob) {
    try {
      URL.revokeObjectURL(imageUrl)
    } catch {
      // Best-effort cleanup only.
    }
    return
  }
  const filePath = layer.meta.filePath
  if (typeof filePath === 'string' && typeof imageUrl === 'string' && imageUrl.startsWith('blob:')) {
    releaseImageUrl(filePath)
  }
}

function _applyLoadedLayers(layers: MapLayerDefinition[]) {
  useMapStore.setState({
    layers,
    activeLayerId: layers.length > 0 ? layers[layers.length - 1].id : null,
  })
}

async function _loadLayersForWorkspace(workspacePath: string | null) {
  _layerPersistReady = false
  _viewPersistReady = false
  if (!workspacePath) {
    _layerPersistReady = true
    _viewPersistReady = true
    return
  }
  try {
    const [loaded, loadedView] = await Promise.all([
      loadLayers(workspacePath),
      loadMapView(workspacePath),
    ])
    _applyLoadedLayers(loaded)
    if (loadedView) {
      _isApplyingPersistedView = true
      useMapStore.getState().setViewState(loadedView)
      _isApplyingPersistedView = false
    }
  } catch (e) {
    console.error('[mapStore] 加载持久化地图状态失败:', e)
  } finally {
    _layerPersistReady = true
    _viewPersistReady = true
  }
}

/**
 * RPC handlers may run before the initial delayed workspace hydration has
 * completed after an app restart. Give read/update map tools one lazy chance
 * to restore persisted layers before they report an empty map.
 */
export async function hydrateMapLayersForRpc(): Promise<void> {
  const workspacePath = useAssetStore.getState().workspacePath
  if (!workspacePath) return
  if (useMapStore.getState().layers.length > 0) return
  if (_layerPersistReady) return
  try {
    const loaded = await loadLayers(workspacePath)
    if (loaded.length > 0 && useMapStore.getState().layers.length === 0) {
      _applyLoadedLayers(loaded)
    }
  } catch (e) {
    console.error('[mapStore] RPC 图层懒加载失败:', e)
  } finally {
    _layerPersistReady = true
  }
}

export async function flushMapStateToDisk(
  targetWorkspacePath?: string | null,
): Promise<void> {
  const wp = targetWorkspacePath ?? useAssetStore.getState().workspacePath
  const state = useMapStore.getState()
  await Promise.all([
    flushLayers(wp, state.layers),
    flushMapView(wp, state.viewState),
  ])
}

// 图层变更 → 防抖持久化到当前工作区。
useMapStore.subscribe((state, prev) => {
  if (state.layers === prev.layers) return
  if (!_layerPersistReady) return
  if (!_persistableLayersChanged(state.layers, prev.layers)) return
  const wp = useAssetStore.getState().workspacePath
  persistLayers(wp, state.layers)
})

// 视口变更 → 防抖持久化到当前工作区。
useMapStore.subscribe((state, prev) => {
  if (state.viewState === prev.viewState) return
  if (!_viewPersistReady || _isApplyingPersistedView) return
  const wp = useAssetStore.getState().workspacePath
  persistMapView(wp, state.viewState)
})

// 工作区切换 → flush 上一份状态 → 清空 → 加载新的。
useAssetStore.subscribe((state, prev) => {
  if (state.workspacePath === prev.workspacePath) return
  const oldWp = prev.workspacePath
  const currentLayers = useMapStore.getState().layers
  const currentViewState = useMapStore.getState().viewState
  _layerPersistReady = false
  _viewPersistReady = false
  const flushOld = oldWp
    ? Promise.all([
      flushLayers(oldWp, currentLayers),
      flushMapView(oldWp, currentViewState),
    ])
    : Promise.resolve()
  flushOld.finally(() => {
    // 清空上一工作区图层，避免泄漏进新工作区
    useMapStore.getState().clearLayers()
    void _loadLayersForWorkspace(state.workspacePath)
  })
})

// 若模块加载时工作区已设置，触发初始加载。
setTimeout(() => {
  const wp = useAssetStore.getState().workspacePath
  void _loadLayersForWorkspace(wp)
}, 100)

function _persistableLayersChanged(
  nextLayers: MapLayerDefinition[],
  prevLayers: MapLayerDefinition[],
): boolean {
  const next = nextLayers.filter((layer) => !layer.extension && !layer.meta?.dynamic)
  const prev = prevLayers.filter((layer) => !layer.extension && !layer.meta?.dynamic)
  if (next.length !== prev.length) return true
  return next.some((layer, index) => layer !== prev[index])
}
