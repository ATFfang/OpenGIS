/**
 * useIdentify —— 在 MapView 里挂属性识别 controller。
 *
 * 使用方式（无参，自动订阅 mapEngine 的 ready 状态）：
 *
 *   useIdentify()
 *
 * 设计要点：
 * - 不再依赖外部传入的 React `mapReady` 状态。原来的 `useIdentify(mapReady)`
 *   依赖 MapView 用 useState 维护的 mapReady，但在 React.StrictMode 下，
 *   init effect 的双挂载会让第一次注册的 `setMapReady` 闭包失效，导致
 *   mapReady 永远停在 false、controller 永远不挂上（线上表现：点了
 *   identify 按钮，光标变 crosshair 但点击没反应、底图照样能拖）。
 * - 现在改为订阅 `mapEngine.onReady()`：MapEngine 自己维护 ready 状态，
 *   onReady 注册时会立即用当前状态回调一次，组件无论何时挂都能正确挂上。
 *
 * identifyActive 由 mapStore 控制，用户通过 UI 按钮切换鼠标模式。
 */
import { useEffect } from 'react'
import { mapEngine } from '../engine/MapEngine'
import { useMapStore } from '@/stores/mapStore'
import { IdentifyController } from './IdentifyController'

export function useIdentify(): void {
  const identifyActive = useMapStore((s) => s.identifyActive)

  useEffect(() => {
    if (!identifyActive) return

    let controller: IdentifyController | null = null

    const attach = () => {
      const map = mapEngine.getMap()
      if (!map || controller) return
      controller = new IdentifyController(map)
    }

    const detach = () => {
      controller?.destroy()
      controller = null
    }

    const unsubscribe = mapEngine.onReady((ready) => {
      if (ready) attach()
      else detach()
    })

    return () => {
      unsubscribe()
      detach()
    }
  }, [identifyActive])
}
