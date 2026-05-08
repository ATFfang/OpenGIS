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
    set((state) => ({
      layers: [...state.layers, layer],
      activeLayerId: layer.id,
    })),

  addLayers: (layers) =>
    set((state) => ({
      layers: [...state.layers, ...layers],
      activeLayerId: layers.length > 0 ? layers[layers.length - 1].id : state.activeLayerId,
    })),

  removeLayer: (id) =>
    set((state) => ({
      layers: state.layers.filter((l) => l.id !== id),
      activeLayerId: state.activeLayerId === id ? null : state.activeLayerId,
    })),

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
