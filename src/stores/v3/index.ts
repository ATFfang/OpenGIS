/**
 * v3 Stores 统一导出
 *
 * 使用：
 *   import { useLayerStore, useAssetStore, useScriptStore, useProjectStore } from '@/stores/v3';
 */

// 注意：图层的单一数据源是 `src/stores/mapStore.ts`。
// 旧的 v3 layerStore 是迁移未完成的死代码，已删除。
export { useAssetStore, type Asset, type AssetInit } from './assetStore';
export {
  useScriptStore,
  type Script,
  type ScriptInit,
  type ScriptStatus,
} from './scriptStore';
export { useProjectStore, type ProjectMeta } from './projectStore';
