import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { parseParams } from '../_util';
import { hydrateMapLayersForRpc, useMapStore } from '@/stores/mapStore';
import type { LayerStyle } from '@/services/geo';
import { SetLayerRendererSchema, SetLayerStyleSchema, SetLayerVisibilitySchema } from '../schemas';
import { applyPaintToLayerStyle, ensureFullVectorLayer } from './shared';

export const styleHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.set_layer_renderer': async (params) => {
    const parsed = parseParams(
      SetLayerRendererSchema,
      params,
      'rpc.ui.map.set_layer_renderer',
    );
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    let layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_renderer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_renderer' },
      );
    }
    if (layer.data.kind === 'vector') {
      layer = await ensureFullVectorLayer(layer);
    }

    const nextStyle: Partial<LayerStyle> = { renderType: parsed.renderer };

    switch (parsed.renderer) {
      case 'graduated':
        if (!parsed.graduated) {
          throw RpcError.invalidParams(
            'set_layer_renderer: graduated config is required when renderer=graduated',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.graduated = parsed.graduated;
        break;
      case 'categorized':
        if (!parsed.categorized) {
          throw RpcError.invalidParams(
            'set_layer_renderer: categorized config is required when renderer=categorized',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.categorized = parsed.categorized;
        break;
      case 'heatmap':
        nextStyle.heatmap = parsed.heatmap ?? {};
        break;
      case 'cluster':
        nextStyle.cluster = parsed.cluster ?? {};
        break;
      case 'extrusion':
        if (!parsed.extrusion) {
          throw RpcError.invalidParams(
            'set_layer_renderer: extrusion config is required when renderer=extrusion',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.extrusion = parsed.extrusion;
        break;
      // fill/line/circle/raster: no extra config
      default:
        break;
    }

    store.updateLayerStyle(parsed.layer_id, nextStyle);
    return {
      layer_id: parsed.layer_id,
      renderer: parsed.renderer,
    };
  },

  'rpc.ui.map.set_layer_style': async (params) => {
    const parsed = parseParams(SetLayerStyleSchema, params, 'rpc.ui.map.set_layer_style');
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_style: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_style' },
      );
    }

    // Python display.py::update_layer_style 发来的 paint 里通常只有
    // `fill-color` / `fill-opacity`，但 MapLibre paint 可能携带 stroke /
    // radius 等更多字段。这里把所有能映射到 LayerStyle 的字段都透传，
    // 避免之前只取 color/opacity 造成的样式丢失。
    const paint = (parsed.style.paint ?? {}) as Record<string, unknown>;

    // graduated / categorized 专题层的颜色由分级/分类配置决定，
    // 直接改 style.color 是无效语义且会让 style.color 与实际渲染不一致，
    // 因此对专题层屏蔽基础 color 的写入（opacity/stroke 等仍可改）。
    const isThematic =
      layer.style.renderType === 'graduated' || layer.style.renderType === 'categorized';

    const styleUpdates: Partial<LayerStyle> = {};
    applyPaintToLayerStyle(styleUpdates, paint, {
      skipColor: isThematic,
      renderType: layer.style.renderType,
    });

    if (Object.keys(styleUpdates).length > 0) {
      store.updateLayerStyle(parsed.layer_id, styleUpdates);
    }

    return { layer_id: parsed.layer_id, applied: styleUpdates };
  },

  'rpc.ui.map.set_layer_visibility': async (params) => {
    const parsed = parseParams(
      SetLayerVisibilitySchema,
      params,
      'rpc.ui.map.set_layer_visibility',
    );
    await hydrateMapLayersForRpc();
    const store = useMapStore.getState();
    if (!store.getLayerById(parsed.layer_id)) {
      throw RpcError.invalidParams(
        `set_layer_visibility: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_visibility' },
      );
    }
    store.setLayerVisibility(parsed.layer_id, parsed.visible);
    return { layer_id: parsed.layer_id, visible: parsed.visible };
  }
};
