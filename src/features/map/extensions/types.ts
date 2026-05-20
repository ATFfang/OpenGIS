/**
 * Extension 类型定义
 *
 * 扩展层的接口契约，不依赖任何 base 模块。
 * 每个扩展实现 MapExtension 接口，通过 registry 注册。
 */

import type { Map as MapLibreMap } from 'maplibre-gl'

/** 扩展收到渲染请求时的上下文 */
export interface ExtensionContext {
  /** MapLibre 实例，扩展可直接操作地图 */
  map: MapLibreMap | null
}

/** 扩展能力描述，用于注入 agent system prompt */
export interface ExtensionCapability {
  name: string
  display_name: string
  description: string
  params: Array<{
    name: string
    type: string
    description: string
    required?: boolean
  }>
}

/**
 * 扩展接口 —— 每个扩展渲染能力实现此接口
 *
 * 约定：
 * - name 同时用作 RPC method 前缀：ext.{name}
 * - methods 列出此扩展关注的所有 RPC 通知
 * - handle() 处理具体的渲染逻辑
 * - dispose() 清理地图上的 layer/source 等资源
 */
export interface MapExtension {
  /** 唯一标识，如 'heatmap', 'chart', 'trajectory' */
  name: string

  /** 此扩展关注的 RPC 通知列表，如 ['ext.heatmap.render'] */
  methods: string[]

  /** 能力描述，用于 agent system prompt */
  capability: ExtensionCapability

  /** 处理 RPC 通知 */
  handle(method: string, params: any, ctx: ExtensionContext): void | Promise<void>

  /** 清理资源（移除 maplibre layer/source 等） */
  dispose(): void
}
