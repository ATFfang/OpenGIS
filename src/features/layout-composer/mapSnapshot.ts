import { mapEngine } from '@/features/map/engine/MapEngine'

export function captureCurrentMapSnapshot(): string | null {
  const map = mapEngine.getMap()
  if (!map) return null
  try {
    map.triggerRepaint()
    return map.getCanvas().toDataURL('image/png')
  } catch (err) {
    console.warn('[layoutComposer] Failed to capture current map snapshot:', err)
    return null
  }
}
