/**
 * useBoxSelect —— 在 MapView 里挂框选 controller。
 *
 * 使用方式（无参，自动订阅 mapEngine 的 ready 状态）：
 *
 *   useBoxSelect()
 *
 * 设计要点见 `useIdentify`：通过订阅 `mapEngine.onReady()` 而非
 * 接收 React state，规避 React.StrictMode 双挂载导致的 mapReady 死锁。
 *
 * boxSelectActive 由 mapStore 控制，用户通过 UI 按钮切换。
 * 注意：boxSelectActive 和 identifyActive 互斥，激活一个会关闭另一个。
 */
import { useEffect } from 'react'
import { mapEngine } from '../engine/MapEngine'
import { useMapStore } from '@/stores/mapStore'
import { BoxSelectController } from './BoxSelectController'

export function useBoxSelect(): void {
  const boxSelectActive = useMapStore((s) => s.boxSelectActive)

  useEffect(() => {
    if (!boxSelectActive) return

    let controller: BoxSelectController | null = null

    const attach = () => {
      const map = mapEngine.getMap()
      if (!map || controller) return
      controller = new BoxSelectController(map)
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
  }, [boxSelectActive])
}
