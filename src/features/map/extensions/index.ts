/**
 * Map Extensions — 统一出口
 *
 * 使用方式：
 *   import { registerExtension, installExtensions, ... } from '@/features/map/extensions'
 *
 * 新增扩展：
 *   1. 在 extensions/ 下创建 [name]/index.ts，实现 MapExtension 接口
 *   2. 调用 registerExtension(myExtension) 自注册
 *   3. 在此文件底部 import './[name]' 触发注册
 */

export { registerExtension, unregisterExtension, getExtension, listExtensions, getExtensionCapabilities } from './registry'
export { installExtensions } from './host'
export type { MapExtension, ExtensionContext, ExtensionCapability } from './types'

// ── 扩展导入区（后续新增扩展在此 import）──────────────────
import './heatmap'
// import './chart'
