import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { parseParams } from '../_util';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { exportMap } from '@/features/map/export';
import { BUILTIN_BASEMAPS } from '@/services/geo';
import { ExportMapSchema } from '../schemas';

export const exportHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.export_map': async (params) => {
    const parsed = parseParams(ExportMapSchema, params, 'rpc.ui.map.export_map');
    const store = useMapStore.getState();
    const map = mapEngine.getMap();

    // ── Ensure map tab is active so the canvas is visible ─────────
    // The map must be rendering for MapLibre to produce a valid
    // screenshot.  If the user is on the code/chat tab, the canvas
    // is hidden and toDataURL() returns a black image.
    try {
      const { useViewStore } = await import('@/stores/viewStore');
      useViewStore.getState().setActiveTab('map');
      useViewStore.getState().setShowCodePanel(false);
    } catch { /* non-fatal */ }

    // Wait for the map to be style-loaded and repainted.
    if (map) {
      if (!map.isStyleLoaded()) {
        await new Promise<void>((r) => map.once('style.load', () => r()));
      }
      // Force a repaint so the canvas is fresh after tab switch.
      map.triggerRepaint();
      await new Promise<void>((r) => {
        map.once('idle', r);
        setTimeout(r, 5000); // safety timeout
      });
    }

    // ── Save original state for restoration ────────────────────────
    const originalBasemapVisible = store.basemapVisible
    const originalBasemapId = store.basemap?.id
    const originalLayerVisibility = new Map<string, boolean>()
    if (parsed.visible_layers !== undefined || parsed.hide_basemap) {
      for (const layer of store.layers) {
        originalLayerVisibility.set(layer.id, layer.visible)
      }
    }

    try {
      // ── Apply basemap switch ─────────────────────────────────────
      if (parsed.basemap_id) {
        const target = BUILTIN_BASEMAPS.find((b) => b.id === parsed.basemap_id)
        if (target) {
          store.setBasemap(target)
          // Wait for new tiles to load
          if (map) await new Promise<void>((r) => { map.once('idle', r); setTimeout(r, 3000) })
        }
      }

      // ── Apply basemap visibility ─────────────────────────────────
      if (parsed.hide_basemap !== undefined) {
        store.setBasemapVisible(!parsed.hide_basemap)
        if (map) await new Promise<void>((r) => { map.once('idle', r); setTimeout(r, 1000) })
      }

      // ── Apply layer visibility ───────────────────────────────────
      if (parsed.visible_layers !== undefined) {
        const visibleSet = new Set(parsed.visible_layers)
        for (const layer of store.layers) {
          const shouldBeVisible = visibleSet.has(layer.id)
          if (layer.visible !== shouldBeVisible) {
            store.setLayerVisibility(layer.id, shouldBeVisible)
            mapEngine.setLayerVisibility(layer.id, shouldBeVisible)
          }
        }
        if (map) await new Promise<void>((r) => { map.once('idle', r); setTimeout(r, 1000) })
      }

      // ── Export ───────────────────────────────────────────────────
      const result = await exportMap({
        format: parsed.format ?? 'png',
        dpiScale: parsed.dpi_scale ?? 1,
        quality: parsed.quality ?? 0.92,
        autoDownload: false,
      });

      // ── Save to file if requested ────────────────────────────────
      if (parsed.save_path) {
        const arrayBuffer = await result.blob.arrayBuffer();
        const api = (globalThis as any).window?.electronAPI;
        if (api?.writeFileBinary) {
          try {
            await api.writeFileBinary(parsed.save_path, arrayBuffer);
            return {
              saved_to: parsed.save_path,
              width: result.width,
              height: result.height,
              format: result.format,
            };
          } catch (err) {
            throw RpcError.internal(
              `export_map: writeFileBinary failed: ${(err as Error).message}`,
              { method: 'rpc.ui.map.export_map' },
            );
          }
        }
        // Fallback: return base64
        const bytes = new Uint8Array(arrayBuffer)
        let binary = ''
        const CHUNK = 0x8000
        for (let i = 0; i < bytes.length; i += CHUNK) {
          binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)) as any)
        }
        return {
          data_url: `data:image/${result.format};base64,${btoa(binary)}`,
          width: result.width,
          height: result.height,
          format: result.format,
          save_path_requested: parsed.save_path,
        };
      }

      return {
        data_url: result.dataUrl,
        width: result.width,
        height: result.height,
        format: result.format,
        file_name: result.fileName,
      };
    } finally {
      // ── Restore original state ───────────────────────────────────
      if (parsed.basemap_id && originalBasemapId) {
        const original = BUILTIN_BASEMAPS.find((b) => b.id === originalBasemapId)
        if (original) store.setBasemap(original)
      }
      if (parsed.hide_basemap !== undefined) {
        store.setBasemapVisible(originalBasemapVisible)
      }
      if (parsed.visible_layers !== undefined) {
        for (const [layerId, wasVisible] of originalLayerVisibility) {
          if (store.getLayerById(layerId)?.visible !== wasVisible) {
            store.setLayerVisibility(layerId, wasVisible)
            mapEngine.setLayerVisibility(layerId, wasVisible)
          }
        }
      }
    }
  }
};
