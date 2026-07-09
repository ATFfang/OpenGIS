/**
 * AssetStore — workspace 下的文件资产登记
 *
 * Asset = 用户 workspace 里识别到的 GIS 文件（shp/geojson/tif/csv/...）。
 * 和 Layer 的区别：
 *   - Asset 是「磁盘上有这么个文件」
 *   - Layer 是「这个文件现在渲染在地图上」
 *   - 一个 Asset 可以被加载成多个 Layer（少见但合法）
 *
 * 扫描策略：
 *   workspace 打开时 `rpc.ui.fs.list_assets` 触发扫描，结果写入 store。
 */

import { create } from 'zustand';
import { newAssetId } from '@/services/rpc/idGen';

export interface Asset {
  asset_id: string;
  /** workspace 相对路径，如 'data/points.shp'。 */
  path: string;
  /** 绝对路径，跨平台用 `/`。 */
  absolute_path: string;
  /** 文件格式（小写扩展名），如 'shp' / 'geojson' / 'tif'。 */
  format: string;
  /** 文件大小（字节）。 */
  size: number;
  /** 首次登记时间戳（ms）。 */
  registered_at: number;
}

export type AssetInit = Omit<Asset, 'asset_id' | 'registered_at'> & {
  asset_id?: string;
  registered_at?: number;
};

interface AssetState {
  assets: Record<string, Asset>;
  byAbsolutePath: Record<string, string>; // path -> asset_id 反查表

  // actions
  register: (init: AssetInit) => Asset;
  unregister: (assetId: string) => boolean;
  get: (assetId: string) => Asset | undefined;
  findByPath: (absolutePath: string) => Asset | undefined;
  list: (filterFormat?: string) => Asset[];
  clear: () => void;
}

export const useAssetStore = create<AssetState>((set, get) => ({
  assets: {},
  byAbsolutePath: {},

  register: (init) => {
    // 同一绝对路径幂等：已登记就直接返回，不生成新 id
    const existingId = get().byAbsolutePath[init.absolute_path];
    if (existingId) {
      const existing = get().assets[existingId];
      if (existing) return existing;
    }
    const asset: Asset = {
      registered_at: Date.now(),
      ...init,
      asset_id: init.asset_id ?? newAssetId(),
    };
    set((s) => ({
      assets: { ...s.assets, [asset.asset_id]: asset },
      byAbsolutePath: { ...s.byAbsolutePath, [asset.absolute_path]: asset.asset_id },
    }));
    return asset;
  },

  unregister: (assetId) => {
    const asset = get().assets[assetId];
    if (!asset) return false;
    set((s) => {
      const { [assetId]: _removed, ...restAssets } = s.assets;
      const { [asset.absolute_path]: _removedPath, ...restPaths } = s.byAbsolutePath;
      return { assets: restAssets, byAbsolutePath: restPaths };
    });
    return true;
  },

  get: (assetId) => get().assets[assetId],

  findByPath: (absolutePath) => {
    const id = get().byAbsolutePath[absolutePath];
    return id ? get().assets[id] : undefined;
  },

  list: (filterFormat) => {
    const all = Object.values(get().assets);
    if (!filterFormat) return all;
    const fmt = filterFormat.toLowerCase();
    return all.filter((a) => a.format.toLowerCase() === fmt);
  },

  clear: () => set({ assets: {}, byAbsolutePath: {} }),
}));
