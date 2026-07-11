import { create } from 'zustand'
import { useAssetStore } from '@/stores/assetStore'
import {
  flushLayoutComposer,
  loadLayoutComposer,
  persistLayoutComposer,
  type PersistedLayoutComposer,
} from '@/services/layoutPersistence'
import type {
  LayoutElement,
  LayoutElementFrame,
  LayoutElementStyle,
  LayoutElementType,
  LayoutMapView,
  LayoutPage,
} from './types'

const DEFAULT_PAGE: LayoutPage = {
  id: 'a4-landscape',
  name: 'A4 Landscape',
  widthMm: 297,
  heightMm: 210,
  background: '#ffffff',
}

const DEFAULT_ELEMENTS: LayoutElement[] = [
  {
    id: 'map-main',
    type: 'map-frame',
    label: 'Map Frame',
    frame: { x: 5, y: 6, width: 70, height: 78 },
    style: {
      variant: 'default',
      borderColor: '#111827',
      borderWidth: 1,
      backgroundColor: '#dbeafe',
      opacity: 1,
    },
    mapView: { x: 0, y: 0, scale: 1 },
    visible: true,
  },
  {
    id: 'north-arrow',
    type: 'north-arrow',
    label: 'North Arrow',
    frame: { x: 82, y: 9, width: 8, height: 12 },
    style: {
      variant: 'classic',
      backgroundColor: '#ffffff',
      backgroundOpacity: 0,
      borderColor: '#111827',
      borderWidth: 0,
      fillColor: '#111827',
      textColor: '#111827',
      opacity: 1,
      fontSize: 12,
    },
    visible: true,
  },
  {
    id: 'scale-bar',
    type: 'scale-bar',
    label: 'Scale Bar',
    frame: { x: 10, y: 88, width: 30, height: 5 },
    visible: true,
    style: {
      variant: 'alternating',
      backgroundColor: '#ffffff',
      backgroundOpacity: 0,
      borderColor: '#111827',
      borderWidth: 0,
      strokeColor: '#111827',
      fillColor: '#111827',
      textColor: '#111827',
      strokeWidth: 2,
      opacity: 1,
      fontSize: 10,
    },
    props: { label: '0        5        10 km', segments: 4 },
  },
]

export const PAGE_PRESETS: LayoutPage[] = [
  DEFAULT_PAGE,
  { id: 'a4-portrait', name: 'A4 Portrait', widthMm: 210, heightMm: 297, background: '#ffffff' },
  { id: 'letter-landscape', name: 'Letter Landscape', widthMm: 279.4, heightMm: 215.9, background: '#ffffff' },
  { id: 'screen-16-9', name: '16:9 Screen', widthMm: 320, heightMm: 180, background: '#ffffff' },
  { id: 'screen-4-3', name: '4:3 Screen', widthMm: 280, heightMm: 210, background: '#ffffff' },
  { id: 'square-1-1', name: '1:1 Square', widthMm: 220, heightMm: 220, background: '#ffffff' },
]

interface LayoutComposerState {
  page: LayoutPage
  elements: LayoutElement[]
  selectedElementId: string | null
  zoom: number
  mapScaleDenominator: number
  mapSnapshotUrl: string | null
  setPage: (page: LayoutPage) => void
  setZoom: (zoom: number) => void
  setMapScaleDenominator: (scale: number) => void
  setMapSnapshotUrl: (url: string | null) => void
  selectElement: (id: string | null) => void
  addElement: (type: LayoutElementType, options?: { id?: string; label?: string; frame?: Partial<LayoutElementFrame> }) => string
  updateElementFrame: (id: string, frame: Partial<LayoutElementFrame>) => void
  updateElementProps: (id: string, props: Record<string, unknown>) => void
  updateElementStyle: (id: string, style: Partial<LayoutElementStyle>) => void
  updateElementMapView: (id: string, mapView: Partial<LayoutMapView>) => void
  setElementVariant: (id: string, variant: NonNullable<LayoutElementStyle['variant']>) => void
  removeElement: (id: string) => void
  resetLayout: () => void
  flushToDisk: (targetWorkspacePath?: string | null) => Promise<void>
}

let elementCounter = 0
function nextElementId(type: LayoutElementType): string {
  elementCounter += 1
  return `${type}-${elementCounter}`
}

function syncElementCounter(elements: LayoutElement[]): void {
  for (const element of elements) {
    const match = /-(\d+)$/.exec(element.id)
    if (!match) continue
    elementCounter = Math.max(elementCounter, Number(match[1]))
  }
}

function createElement(
  type: LayoutElementType,
  options: { id?: string; label?: string; frame?: Partial<LayoutElementFrame> } = {},
): LayoutElement {
  const id = options.id ?? nextElementId(type)
  if (type === 'scale-bar') {
    return {
      id,
      type,
      label: options.label ?? 'Scale Bar',
      frame: clampFrame({ x: 10, y: 88, width: 30, height: 5, ...options.frame }),
      visible: true,
      style: {
        variant: 'alternating',
        backgroundColor: '#ffffff',
        backgroundOpacity: 0,
        borderColor: '#111827',
        borderWidth: 0,
        strokeColor: '#111827',
        fillColor: '#111827',
        textColor: '#111827',
        strokeWidth: 2,
        opacity: 1,
        fontSize: 10,
      },
      props: { label: '0        5        10 km', segments: 4 },
    }
  }
  if (type === 'north-arrow') {
    return {
      id,
      type,
      label: options.label ?? 'North Arrow',
      frame: clampFrame({ x: 84, y: 10, width: 8, height: 12, ...options.frame }),
      style: {
        variant: 'classic',
        backgroundColor: '#ffffff',
        backgroundOpacity: 0,
        borderColor: '#111827',
        borderWidth: 0,
        fillColor: '#111827',
        textColor: '#111827',
        opacity: 1,
        fontSize: 12,
      },
      visible: true,
    }
  }
  if (type === 'legend') {
    return {
      id,
      type,
      label: options.label ?? 'Legend',
      frame: clampFrame({ x: 76, y: 25, width: 18, height: 36, ...options.frame }),
      style: {
        variant: 'panel',
        backgroundColor: '#ffffff',
        backgroundOpacity: 0.94,
        borderColor: '#d1d5db',
        borderWidth: 1,
        textColor: '#111827',
        fontSize: 10,
        opacity: 1,
        padding: 8,
      },
      props: { layerIds: [], grouped: true, title: 'Legend' },
      visible: true,
    }
  }
  if (type === 'text') {
    return {
      id,
      type,
      label: options.label ?? 'Text',
      frame: clampFrame({ x: 8, y: 3, width: 50, height: 6, ...options.frame }),
      visible: true,
      style: {
        textColor: '#111827',
        fontSize: 18,
        fontWeight: 600,
        opacity: 1,
      },
      props: { text: 'Map Title' },
    }
  }
  return {
    id,
    type,
    label: options.label ?? 'Map Frame',
    frame: clampFrame({ x: 5, y: 6, width: 70, height: 78, ...options.frame }),
    style: {
      variant: 'default',
      borderColor: '#111827',
      borderWidth: 1,
      backgroundColor: '#dbeafe',
      opacity: 1,
    },
    mapView: { x: 0, y: 0, scale: 1 },
    visible: true,
  }
}

function cloneDefaultElements(): LayoutElement[] {
  return DEFAULT_ELEMENTS.map((element) => ({
    ...element,
    frame: { ...element.frame },
    style: element.style ? { ...element.style } : undefined,
    mapView: element.mapView ? { ...element.mapView } : undefined,
    props: element.props ? { ...element.props } : undefined,
  }))
}

export const useLayoutComposerStore = create<LayoutComposerState>((set) => ({
  page: DEFAULT_PAGE,
  elements: cloneDefaultElements(),
  selectedElementId: 'map-main',
  zoom: 0.9,
  mapScaleDenominator: 50000,
  mapSnapshotUrl: null,

  setPage: (page) => set({ page }),
  setZoom: (zoom) => set({ zoom: Math.max(0.25, Math.min(2, zoom)) }),
  setMapScaleDenominator: (scale) =>
    set({ mapScaleDenominator: Math.max(100, Math.round(scale)) }),
  setMapSnapshotUrl: (url) => set({ mapSnapshotUrl: url }),
  selectElement: (id) => set({ selectedElementId: id }),
  addElement: (type, options) => {
    const element = createElement(type, options)
    set((state) => {
      return {
        elements: [...state.elements, element],
        selectedElementId: element.id,
      }
    })
    return element.id
  },
  updateElementFrame: (id, frame) =>
    set((state) => ({
      elements: state.elements.map((element) =>
        element.id === id
          ? {
              ...element,
              frame: {
                ...element.frame,
                ...clampFrame({ ...element.frame, ...frame }),
              },
            }
          : element,
      ),
    })),
  updateElementProps: (id, props) =>
    set((state) => ({
      elements: state.elements.map((element) =>
        element.id === id
          ? { ...element, props: { ...(element.props ?? {}), ...props } }
          : element,
      ),
    })),
  updateElementStyle: (id, style) =>
    set((state) => ({
      elements: state.elements.map((element) =>
        element.id === id
          ? { ...element, style: { ...(element.style ?? {}), ...style } }
          : element,
      ),
    })),
  updateElementMapView: (id, mapView) =>
    set((state) => ({
      elements: state.elements.map((element) =>
        element.id === id
          ? {
              ...element,
              mapView: clampMapView({ ...(element.mapView ?? { x: 0, y: 0, scale: 1 }), ...mapView }),
            }
          : element,
      ),
    })),
  setElementVariant: (id, variant) =>
    set((state) => ({
      elements: state.elements.map((element) =>
        element.id === id
          ? { ...element, style: { ...(element.style ?? {}), variant } }
          : element,
      ),
    })),
  removeElement: (id) =>
    set((state) => {
      const elements = state.elements.filter((element) => element.id !== id)
      return {
        elements,
        selectedElementId:
          state.selectedElementId === id ? elements[0]?.id ?? null : state.selectedElementId,
      }
    }),
  resetLayout: () =>
    set({
      page: DEFAULT_PAGE,
      elements: cloneDefaultElements(),
      selectedElementId: 'map-main',
      zoom: 0.9,
      mapScaleDenominator: 50000,
    }),
  flushToDisk: async (targetWorkspacePath) => {
    const wp = targetWorkspacePath !== undefined
      ? targetWorkspacePath
      : useAssetStore.getState().workspacePath
    await flushLayoutComposer(wp, getPersistedLayout(useLayoutComposerStore.getState()))
  },
}))

function clampFrame(frame: LayoutElementFrame): LayoutElementFrame {
  const width = Math.max(2, Math.min(100, frame.width))
  const height = Math.max(2, Math.min(100, frame.height))
  return {
    x: Math.max(0, Math.min(100 - width, frame.x)),
    y: Math.max(0, Math.min(100 - height, frame.y)),
    width,
    height,
  }
}

function clampMapView(view: LayoutMapView): LayoutMapView {
  const scale = Math.max(0.12, Math.min(8, view.scale))
  return {
    x: Math.max(-250, Math.min(250, view.x)),
    y: Math.max(-250, Math.min(250, view.y)),
    scale,
  }
}

let _layoutPersistReady = false
let _isApplyingPersistedLayout = false

function getPersistedLayout(state: LayoutComposerState): PersistedLayoutComposer {
  return {
    page: state.page,
    elements: state.elements,
    selectedElementId: state.selectedElementId,
    zoom: state.zoom,
    mapScaleDenominator: state.mapScaleDenominator,
    mapSnapshotUrl: state.mapSnapshotUrl,
  }
}

async function loadLayoutForWorkspace(workspacePath: string | null): Promise<void> {
  if (!workspacePath) {
    _layoutPersistReady = true
    return
  }
  try {
    const loaded = await loadLayoutComposer(workspacePath)
    if (loaded) {
      const elements = loaded.elements.length > 0 ? loaded.elements : cloneDefaultElements()
      syncElementCounter(elements)
      _isApplyingPersistedLayout = true
      useLayoutComposerStore.setState({
        page: loaded.page,
        elements,
        selectedElementId: loaded.selectedElementId,
        zoom: loaded.zoom,
        mapScaleDenominator: loaded.mapScaleDenominator,
        mapSnapshotUrl: loaded.mapSnapshotUrl,
      })
      _isApplyingPersistedLayout = false
    }
  } catch (e) {
    console.error('[layoutComposerStore] 加载制图画布状态失败:', e)
  } finally {
    _layoutPersistReady = true
    _isApplyingPersistedLayout = false
  }
}

useLayoutComposerStore.subscribe((state) => {
  if (!_layoutPersistReady || _isApplyingPersistedLayout) return
  const wp = useAssetStore.getState().workspacePath
  persistLayoutComposer(wp, getPersistedLayout(state))
})

useAssetStore.subscribe((state, prev) => {
  if (state.workspacePath === prev.workspacePath) return
  const oldWp = prev.workspacePath
  const currentLayout = getPersistedLayout(useLayoutComposerStore.getState())
  _layoutPersistReady = false
  const flushOld = oldWp
    ? flushLayoutComposer(oldWp, currentLayout)
    : Promise.resolve()
  flushOld.finally(() => {
    useLayoutComposerStore.setState({
      page: DEFAULT_PAGE,
      elements: cloneDefaultElements(),
      selectedElementId: 'map-main',
      zoom: 0.9,
      mapScaleDenominator: 50000,
      mapSnapshotUrl: null,
    })
    void loadLayoutForWorkspace(state.workspacePath)
  })
})

setTimeout(() => {
  const wp = useAssetStore.getState().workspacePath
  void loadLayoutForWorkspace(wp)
}, 100)
