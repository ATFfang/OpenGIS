/**
 * v3 Stores 统一导出
 *
 * 使用：
 *   import { useLayerStore, useAssetStore, useScriptStore, useProjectStore } from '@/stores/v3';
 */

export { useLayerStore, type Layer, type LayerInit } from './layerStore';
export { useAssetStore, type Asset, type AssetInit } from './assetStore';
export {
  useScriptStore,
  type Script,
  type ScriptInit,
  type ScriptStatus,
} from './scriptStore';
export { useProjectStore, type ProjectMeta } from './projectStore';
