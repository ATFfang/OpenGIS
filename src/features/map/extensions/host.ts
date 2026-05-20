/**
 * Extension Host — 扩展安装入口
 *
 * 职责：把已注册的扩展连接到 pythonClient 的通知流。
 * 这是扩展层和 base 层唯一的耦合点：只 import pythonClient + mapEngine。
 *
 * ext.* 方法不在 rpc./chat./event. 通道上，dispatcher 不会路由它们，
 * 但 pythonClient._handleMessage 总是会 fan-out 到 notificationHandlers。
 * 所以扩展通过 onNotification 监听，和 base 完全独立。
 */

import { pythonClient } from '@/services/pythonClient'
import { mapEngine } from '@/features/map/engine/MapEngine'
import { listExtensions } from './registry'
import type { ExtensionContext } from './types'

let installed = false

/** 安装扩展监听器。幂等，多次调用只生效一次。 */
export function installExtensions(): void {
  if (installed) return
  installed = true

  pythonClient.onNotification((method, params) => {
    // ext.heatmap.render → prefix: ext.heatmap → name: heatmap
    if (!method.startsWith('ext.')) return

    const parts = method.split('.')
    if (parts.length < 2) return
    const extName = parts[1]

    const ext = listExtensions().find((e) => e.name === extName)
    if (!ext) return
    if (!ext.methods.includes(method)) return

    const ctx: ExtensionContext = { map: mapEngine.getMap() }
    try {
      const r = ext.handle(method, params, ctx)
      if (r instanceof Promise) {
        r.catch((err) => console.error(`[Extension] ${method} failed:`, err))
      }
    } catch (err) {
      console.error(`[Extension] ${method} threw:`, err)
    }
  })
}
