/**
 * Extension Registry — 扩展注册中心
 *
 * 纯注册/查询逻辑，不依赖 base 模块。
 * 扩展通过 registerExtension() 自注册，通过 listExtensions() 查询。
 */

import type { MapExtension, ExtensionCapability } from './types'

const extensions = new Map<string, MapExtension>()

/** 注册一个扩展。同名扩展会被跳过并打印警告。 */
export function registerExtension(ext: MapExtension): void {
  if (extensions.has(ext.name)) {
    console.warn(`[Extension] "${ext.name}" already registered, skipping`)
    return
  }
  extensions.set(ext.name, ext)
}

/** 注销一个扩展并调用其 dispose()。 */
export function unregisterExtension(name: string): void {
  const ext = extensions.get(name)
  if (ext) {
    ext.dispose()
    extensions.delete(name)
  }
}

/** 按名称查询扩展 */
export function getExtension(name: string): MapExtension | undefined {
  return extensions.get(name)
}

/** 列出所有已注册扩展 */
export function listExtensions(): MapExtension[] {
  return [...extensions.values()]
}

/** 获取所有扩展能力描述，用于注入 agent system prompt */
export function getExtensionCapabilities(): ExtensionCapability[] {
  return listExtensions().map((ext) => ext.capability)
}
