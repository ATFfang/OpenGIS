import { memo, useCallback, useEffect, useState } from 'react'
import { MapPin, Maximize2, X, Check, Loader2, ImageOff } from 'lucide-react'
import type { UIMessage } from '@/types/chat'
import { useMapStore } from '@/stores/mapStore'
import type { PinnedImage } from '@/features/map/PinnedImagePanel'
import { pathToImageUrl, releaseImageUrl } from '@/services/rpc/handlers/_image_url'

interface ImageRowProps {
  message: UIMessage
}

 /**
 * ImageRow — renders an inline image (matplotlib plot etc.) emitted by
 * the backend `save_plot` tool via `rpc.ui.chat.show_image`.
 *
 * Wire-up:
 *   - `message.images[0]` is the Blob URL the chat handler created from
 *     reading the file.
 *   - `message.files[0]` is the *absolute path* on disk.
 *
 * Pin button adds a draggable floating image window on the map
 * (via mapStore.addPinnedImage), preserving the original aspect ratio.
 */
export const ImageRow = memo(({ message }: ImageRowProps) => {
  const images = message.images ?? []
  const path = message.files?.[0]
  const storedUrl = images[0]

  const [resolvedUrl, setResolvedUrl] = useState<string | null>(storedUrl)
  const [pinState, setPinState] = useState<'idle' | 'pinning' | 'pinned' | 'error'>('idle')
  const [previewOpen, setPreviewOpen] = useState(false)

  // Re-resolve from disk path on mount — blob URLs from a previous session
  // are stale, but the original file path is still valid.
  useEffect(() => {
    if (!path) return
    let cancelled = false
    let acquired = false
    pathToImageUrl(path).then((url) => {
      acquired = true
      if (cancelled) {
        releaseImageUrl(path)
        return
      }
      setResolvedUrl(url)
    })
    return () => {
      cancelled = true
      if (acquired) releaseImageUrl(path)
    }
  }, [path])

  const url = resolvedUrl

  const handlePin = useCallback(async () => {
    if (!path || !url || pinState === 'pinning') return
    setPinState('pinning')
    try {
      const fileName = path.split(/[\\/]/).pop() ?? 'image.png'
      const displayName = fileName.replace(/\.[^.]+$/, '')
      const pinnedImage: PinnedImage = {
        id: `pin_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        url,
        name: displayName,
        path,
        // Place the floating window near the top-left of the map area
        position: { x: 60, y: 60 },
      }
      useMapStore.getState().addPinnedImage(pinnedImage)
      setPinState('pinned')
      setTimeout(() => setPinState('idle'), 1800)
    } catch (err) {
      console.error('[ImageRow] pin to map failed:', err)
      setPinState('error')
      setTimeout(() => setPinState('idle'), 2400)
    }
  }, [path, url, pinState])

  if (!url) {
    return (
      <div className="ml-[30px] mt-1 mb-1.5 max-w-[420px] rounded-lg border border-border/30 bg-bg-tertiary/40 px-3 py-2 text-[12px] text-text-muted flex items-center gap-2">
        <ImageOff className="w-3.5 h-3.5" />
        <span>Loading chart preview…</span>
      </div>
    )
  }

  return (
    <>
      <div className="ml-[30px] mt-1 mb-1.5">
        <div className="relative inline-block group max-w-full">
          <img
            src={url}
            alt={message.text || 'plot'}
            className="max-w-[420px] max-h-[320px] rounded-xl border border-border/30 shadow-sm cursor-zoom-in object-contain bg-bg-tertiary/40"
            onClick={() => setPreviewOpen(true)}
          />

          {/* Toolbar: keep Pin visible so chart -> map remains discoverable. */}
          <div className="absolute top-2 right-2 flex gap-1 opacity-100 sm:opacity-95 sm:group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
            <button
              type="button"
              onClick={handlePin}
              disabled={!path || pinState === 'pinning'}
              title={path ? 'Pin to map' : 'No path available'}
              className="px-2 py-1 rounded-md bg-white/90 border border-black/10 text-[11px] text-gray-800 hover:bg-white hover:border-blue-400/40 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1 shadow-md"
            >
              {pinState === 'pinning' ? (
                <Loader2 className="w-3 h-3 animate-spin text-gray-600" />
              ) : pinState === 'pinned' ? (
                <Check className="w-3 h-3 text-green-600" />
              ) : (
                <MapPin className="w-3 h-3 text-gray-600" />
              )}
              <span>
                {pinState === 'pinned'
                  ? 'Pinned'
                  : pinState === 'error'
                    ? 'Failed'
                    : pinState === 'pinning'
                      ? 'Pinning…'
                      : 'Pin to map'}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setPreviewOpen(true)}
              title="Open fullsize"
              className="p-1 rounded-md bg-white/90 border border-black/10 text-gray-800 hover:bg-white transition-colors shadow-md"
            >
              <Maximize2 className="w-3 h-3" />
            </button>
          </div>
        </div>

        {message.text && (
          <p className="text-[12px] text-text-muted mt-1 max-w-[420px] leading-relaxed">
            {message.text}
          </p>
        )}
      </div>

      {/* Lightbox preview */}
      {previewOpen && (
        <div
          className="fixed inset-0 z-[200] bg-black/75 backdrop-blur-sm flex items-center justify-center p-8"
          onClick={() => setPreviewOpen(false)}
        >
          <button
            type="button"
            className="absolute top-4 right-4 w-9 h-9 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center"
            onClick={() => setPreviewOpen(false)}
          >
            <X className="w-4 h-4" />
          </button>
          <img
            src={url}
            alt={message.text || 'plot'}
            className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  )
})

ImageRow.displayName = 'ImageRow'
