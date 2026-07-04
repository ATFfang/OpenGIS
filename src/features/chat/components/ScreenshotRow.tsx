/**
 * ScreenshotRow — interactive map capture card in the chat.
 *
 * Shown when the backend calls interactive_snapshot skill.
 * User can adjust the map, then click "Capture" to take a screenshot.
 * The screenshot is saved to disk and a result marker is written
 * so the backend skill can unblock.
 */

import { memo, useState, useCallback } from 'react'
import { Camera, X, Loader2, Check, MapPin } from 'lucide-react'
import { mapEngine } from '@/features/map/engine/MapEngine'

interface ScreenshotRowProps {
  requestId: string
  savePath: string
  prompt: string
}

export const ScreenshotRow = memo(({ requestId, savePath, prompt }: ScreenshotRowProps) => {
  const [status, setStatus] = useState<'pending' | 'capturing' | 'done' | 'skipped'>('pending')

  const handleCapture = useCallback(async () => {
    setStatus('capturing')
    try {
      const map = mapEngine.getMap()
      if (!map) throw new Error('Map not initialized')

      // Wait for render
      if (!map.isStyleLoaded()) {
        await new Promise<void>((r) => map.once('style.load', () => r()))
      }
      map.triggerRepaint()
      await new Promise<void>((r) => {
        map.once('idle', r)
        setTimeout(r, 2000)
      })

      // Capture canvas
      const canvas = map.getCanvas()
      const dataUrl = canvas.toDataURL('image/png')

      // Save via Electron IPC
      const api = (window as any).electronAPI
      if (api?.writeFileBinary) {
        const base64 = dataUrl.split(',')[1]
        const binary = atob(base64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
        await api.writeFileBinary(savePath, bytes.buffer)
      }

      // Write result marker so the backend skill can unblock
      const parentDir = savePath.substring(0, savePath.lastIndexOf('/'))
      const resultPath = `${parentDir}/.snapshot_${requestId}.result`
      if (api?.writeFile) {
        await api.writeFile(resultPath, JSON.stringify({
          width: canvas.width,
          height: canvas.height,
          saved: true,
        }))
      }

      setStatus('done')
    } catch (err) {
      console.error('[ScreenshotRow] capture failed:', err)
      setStatus('pending')
    }
  }, [savePath, requestId])

  const handleSkip = useCallback(async () => {
    setStatus('skipped')
    const parentDir = savePath.substring(0, savePath.lastIndexOf('/'))
    const resultPath = `${parentDir}/.snapshot_${requestId}.result`
    const api = (window as any).electronAPI
    if (api?.writeFile) {
      await api.writeFile(resultPath, JSON.stringify({ skipped: true }))
    }
  }, [savePath, requestId])

  if (status === 'done') {
    return (
      <div className="ml-[30px] bg-accent-success/5 border border-accent-success/15 rounded-xl p-3 flex items-center gap-2">
        <Check className="w-4 h-4 text-accent-success" />
        <span className="text-[13px] text-text-primary">截图已保存</span>
      </div>
    )
  }

  if (status === 'skipped') {
    return (
      <div className="ml-[30px] bg-bg-tertiary/30 border border-border/60 rounded-xl p-3 flex items-center gap-2">
        <X className="w-4 h-4 text-text-muted" />
        <span className="text-[13px] text-text-muted">已跳过截图</span>
      </div>
    )
  }

  return (
    <div className="ml-[30px] bg-bg-tertiary/40 border border-border/60 rounded-xl overflow-hidden">
      <div className="px-3.5 py-2.5">
        <div className="flex items-center gap-2 mb-2">
          <MapPin className="w-4 h-4 text-accent-primary" />
          <span className="text-[13px] font-semibold text-text-primary">地图截图</span>
        </div>
        <p className="text-[12px] text-text-secondary leading-relaxed mb-3">
          {prompt || '请调整地图到满意位置，然后点击截图。'}
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleCapture}
            disabled={status === 'capturing'}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] font-medium bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50 transition-colors"
          >
            {status === 'capturing' ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Camera className="w-3 h-3" />
            )}
            {status === 'capturing' ? '截图中...' : '📸 截图'}
          </button>
          <button
            onClick={handleSkip}
            disabled={status === 'capturing'}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50 transition-colors"
          >
            跳过
          </button>
        </div>
      </div>
    </div>
  )
})

ScreenshotRow.displayName = 'ScreenshotRow'
