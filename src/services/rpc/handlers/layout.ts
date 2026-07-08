import type { RpcHandler } from '../registry'
import { RpcError } from '../errors'
import { parseParams } from './_util'
import { useMapStore } from '@/stores/mapStore'
import { PAGE_PRESETS, useLayoutComposerStore } from '@/features/layout-composer/layoutComposerStore'
import { captureCurrentMapSnapshot } from '@/features/layout-composer/mapSnapshot'
import { exportLayoutAsPng } from '@/features/layout-composer/layoutExport'
import type { LayoutPage } from '@/features/layout-composer/types'
import {
  LayoutAddElementSchema,
  LayoutElementIdSchema,
  LayoutExportSchema,
  LayoutSetPageSchema,
  LayoutUpdateElementFrameSchema,
  LayoutUpdateElementPropsSchema,
  LayoutUpdateElementStyleSchema,
  LayoutUpdateMapViewSchema,
} from './schemas'

export const layoutHandlers: Record<string, RpcHandler> = {
  'rpc.ui.layout.get_state': () => summarizeLayout(),

  'rpc.ui.layout.set_page': (params) => {
    const parsed = parseParams(LayoutSetPageSchema, params, 'rpc.ui.layout.set_page')
    const store = useLayoutComposerStore.getState()
    let page: LayoutPage | undefined
    if (parsed.preset) {
      page = PAGE_PRESETS.find((preset) => preset.id === parsed.preset)
      if (!page) {
        throw RpcError.invalidParams(`Unknown layout page preset: ${parsed.preset}`, {
          method: 'rpc.ui.layout.set_page',
        })
      }
    } else {
      const current = store.page
      page = {
        ...current,
        id: 'custom',
        name: parsed.name ?? 'Custom',
        widthMm: parsed.width_mm ?? current.widthMm,
        heightMm: parsed.height_mm ?? current.heightMm,
        background: parsed.background ?? current.background,
      }
    }
    store.setPage({ ...page, background: parsed.background ?? page.background })
    return summarizeLayout()
  },

  'rpc.ui.layout.add_element': (params) => {
    const parsed = parseParams(LayoutAddElementSchema, params, 'rpc.ui.layout.add_element')
    const store = useLayoutComposerStore.getState()
    store.addElement(parsed.type, {
      id: parsed.id,
      label: parsed.label,
      frame: parsed.frame,
    })
    return summarizeLayout()
  },

  'rpc.ui.layout.select_element': (params) => {
    const parsed = parseParams(LayoutElementIdSchema, params, 'rpc.ui.layout.select_element')
    const store = useLayoutComposerStore.getState()
    if (!store.elements.some((element) => element.id === parsed.element_id)) {
      throw RpcError.invalidParams(`Layout element not found: ${parsed.element_id}`, {
        method: 'rpc.ui.layout.select_element',
      })
    }
    store.selectElement(parsed.element_id)
    return summarizeLayout()
  },

  'rpc.ui.layout.update_frame': (params) => {
    const parsed = parseParams(LayoutUpdateElementFrameSchema, params, 'rpc.ui.layout.update_frame')
    ensureElement(parsed.element_id, 'rpc.ui.layout.update_frame')
    useLayoutComposerStore.getState().updateElementFrame(parsed.element_id, parsed.frame)
    return summarizeLayout()
  },

  'rpc.ui.layout.update_style': (params) => {
    const parsed = parseParams(LayoutUpdateElementStyleSchema, params, 'rpc.ui.layout.update_style')
    ensureElement(parsed.element_id, 'rpc.ui.layout.update_style')
    useLayoutComposerStore.getState().updateElementStyle(parsed.element_id, parsed.style)
    return summarizeLayout()
  },

  'rpc.ui.layout.update_props': (params) => {
    const parsed = parseParams(LayoutUpdateElementPropsSchema, params, 'rpc.ui.layout.update_props')
    ensureElement(parsed.element_id, 'rpc.ui.layout.update_props')
    useLayoutComposerStore.getState().updateElementProps(parsed.element_id, parsed.props)
    return summarizeLayout()
  },

  'rpc.ui.layout.update_map_view': (params) => {
    const parsed = parseParams(LayoutUpdateMapViewSchema, params, 'rpc.ui.layout.update_map_view')
    const element = ensureElement(parsed.element_id, 'rpc.ui.layout.update_map_view')
    if (element.type !== 'map-frame') {
      throw RpcError.invalidParams(`Element is not a map-frame: ${parsed.element_id}`, {
        method: 'rpc.ui.layout.update_map_view',
      })
    }
    useLayoutComposerStore.getState().updateElementMapView(parsed.element_id, parsed.map_view)
    return summarizeLayout()
  },

  'rpc.ui.layout.remove_element': (params) => {
    const parsed = parseParams(LayoutElementIdSchema, params, 'rpc.ui.layout.remove_element')
    ensureElement(parsed.element_id, 'rpc.ui.layout.remove_element')
    useLayoutComposerStore.getState().removeElement(parsed.element_id)
    return summarizeLayout()
  },

  'rpc.ui.layout.capture_map': () => {
    const snapshot = captureCurrentMapSnapshot()
    if (snapshot) useLayoutComposerStore.getState().setMapSnapshotUrl(snapshot)
    return {
      success: Boolean(snapshot),
      has_snapshot: Boolean(snapshot),
    }
  },

  'rpc.ui.layout.export': async (params) => {
    const parsed = parseParams(LayoutExportSchema, params, 'rpc.ui.layout.export')
    const layout = useLayoutComposerStore.getState()
    const result = await exportLayoutAsPng({
      page: layout.page,
      elements: layout.elements.map((element) =>
        element.type === 'scale-bar'
          ? {
              ...element,
              props: {
                ...(element.props ?? {}),
                scaleDenominator: layout.mapScaleDenominator,
              },
            }
          : element,
      ),
      layers: useMapStore.getState().layers,
      mapSnapshotUrl: layout.mapSnapshotUrl,
      exportOptions: {
        pixelRatio: parsed.pixel_ratio ?? 2,
        fileName: parsed.file_name ?? `opengis-layout-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`,
        autoDownload: !parsed.save_path,
      },
    })
    if (parsed.save_path) {
      const api = (globalThis as any).window?.electronAPI
      if (!api?.writeFileBinary) {
        throw RpcError.internal('layout.export: electronAPI.writeFileBinary is unavailable', {
          method: 'rpc.ui.layout.export',
        })
      }
      const writeResult = await api.writeFileBinary(parsed.save_path, await result.blob.arrayBuffer())
      if (writeResult && writeResult.success === false) {
        throw RpcError.internal(
          `layout.export: writeFileBinary failed: ${writeResult.error ?? 'unknown error'}`,
          { method: 'rpc.ui.layout.export' },
        )
      }
      return {
        saved_to: parsed.save_path,
        width: result.width,
        height: result.height,
      }
    }
    return {
      file_name: result.fileName,
      width: result.width,
      height: result.height,
    }
  },
}

function ensureElement(elementId: string, method: string) {
  const element = useLayoutComposerStore.getState().elements.find((item) => item.id === elementId)
  if (!element) {
    throw RpcError.invalidParams(`Layout element not found: ${elementId}`, { method })
  }
  return element
}

function summarizeLayout() {
  const state = useLayoutComposerStore.getState()
  return {
    page: state.page,
    elements: state.elements,
    selected_element_id: state.selectedElementId,
    zoom: state.zoom,
    map_scale_denominator: state.mapScaleDenominator,
    has_map_snapshot: Boolean(state.mapSnapshotUrl),
  }
}
