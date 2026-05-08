/**
 * LayerStore (v3) — 图层是 Single Source of Truth
 *
 * 职责：
 *   - 维护所有已加载图层的元信息（不含原始要素几何，那个由 MapLibre 源自己管）
 *   - 所有 id 都在这里生成（`layer_<uuid>`）
 *   - `added_by` 区分用户拖入 vs Agent 生成
 *
 * 设计约定：
 *   - Python sidecar 不持有图层元数据副本；需要时通过 `rpc.ui.map.get_layer` 拉只读快照
 *   - 变更通过 EventBus 的 `event.layer.*` 通知 Python（Stage 3 接通）
 *
 * Stage 1 只实现内存 store，Stage 3 后才把变更 emit 到 EventBus。
 */

import { create } from 'zustand';
import type { BBox, CRS, GeometryType, LayerSource, LayerStyle } from '@/types/protocol';
import { newLayerId } from '@/services/rpc/idGen';

export interface Layer {
  layer_id: string;
  name: string;
  geometry_type: GeometryType;
  bbox: BBox;
  feature_count: number;
  crs: CRS;
  visible: boolean;
  style?: LayerStyle;
  source: LayerSource;
  source_path?: string;
  added_by: 'user' | 'agent';
  run_id?: string;
  created_at: number;
}

export type LayerInit = Omit<Layer, 'layer_id' | 'visible' | 'created_at'> & {
  visible?: boolean;
  layer_id?: string;
  created_at?: number;
};

interface LayerState {
  layers: Record<string, Layer>;
  order: string[];

  // actions
  add: (init: LayerInit) => Layer;
  remove: (layerId: string) => boolean;
  update: (layerId: string, changes: Partial<Omit<Layer, 'layer_id'>>) => Layer | null;
  setVisibility: (layerId: string, visible: boolean) => boolean;
  setStyle: (layerId: string, style: LayerStyle) => boolean;
  get: (layerId: string) => Layer | undefined;
  list: () => Layer[];
  clear: () => void;
}

export const useLayerStore = create<LayerState>((set, get) => ({
  layers: {},
  order: [],

  add: (init) => {
    const layer: Layer = {
      visible: true,
      created_at: Date.now(),
      ...init,
      layer_id: init.layer_id ?? newLayerId(),
    };
    set((s) => ({
      layers: { ...s.layers, [layer.layer_id]: layer },
      order: [...s.order, layer.layer_id],
    }));
    return layer;
  },

  remove: (layerId) => {
    const exists = layerId in get().layers;
    if (!exists) return false;
    set((s) => {
      const { [layerId]: _removed, ...rest } = s.layers;
      return {
        layers: rest,
        order: s.order.filter((id) => id !== layerId),
      };
    });
    return true;
  },

  update: (layerId, changes) => {
    let result: Layer | null = null;
    set((s) => {
      const current = s.layers[layerId];
      if (!current) return s; // No change needed
      const next: Layer = { ...current, ...changes, layer_id: current.layer_id };
      result = next;
      return { layers: { ...s.layers, [layerId]: next } };
    });
    return result;
  },

  setVisibility: (layerId, visible) => {
    return get().update(layerId, { visible }) !== null;
  },

  setStyle: (layerId, style) => {
    return get().update(layerId, { style }) !== null;
  },

  get: (layerId) => get().layers[layerId],

  list: () => get().order.map((id) => get().layers[id]).filter((x): x is Layer => !!x),

  clear: () => set({ layers: {}, order: [] }),
}));
