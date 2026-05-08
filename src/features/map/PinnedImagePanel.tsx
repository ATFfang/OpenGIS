/**
 * PinnedImagePanel — A draggable, resizable floating image window on the map.
 *
 * Design:
 * - Similar to FeatureAttributePanel but displays an image
 * - Preserves original aspect ratio
 * - Draggable by the title bar
 * - Resizable by dragging the bottom-right corner
 * - Editable title (double-click to rename)
 * - Dark glass aesthetic matching the project's design language
 * - Close button and fullscreen preview
 */
import { useState, useRef, useCallback, useEffect, memo } from 'react'
import { X, Maximize2, GripHorizontal, Pencil, Check } from 'lucide-react'
import { pathToImageUrl } from '@/services/rpc/handlers/_image_url'

// ─── Types ──────────────────────────────────────────────────────────

export interface PinnedImage {
  /** Unique id for this pinned image */
  id: string
  /** Blob URL or data URL for display */
  url: string
  /** Display name / caption */
  name: string
  /** Absolute file path on disk */
  path: string
  /** Initial position offset from top-left of map container */
  position: { x: number; y: number }
}

interface PinnedImagePanelProps {
  image: PinnedImage
  onClose: (id: string) => void
  onRename?: (id: string, newName: string) => void
}

// ─── Constants ──────────────────────────────────────────────────────

const MIN_WIDTH = 200
const MIN_HEIGHT = 150
const DEFAULT_WIDTH = 480
const DEFAULT_HEIGHT = 360

// ─── Component ──────────────────────────────────────────────────────

export const PinnedImagePanel = memo(({ image, onClose, onRename }: PinnedImagePanelProps) => {
  const [pos, setPos] = useState(image.position)
  const [size, setSize] = useState({ width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT })
  const [previewOpen, setPreviewOpen] = useState(false)
  const [imgLoaded, setImgLoaded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState(image.name)
  const [resolvedUrl, setResolvedUrl] = useState(image.url)

  // Re-resolve from disk path on mount — blob URLs from a previous session
  // are stale, but the original file path is still valid.
  useEffect(() => {
    if (!image.path) return
    let cancelled = false
    pathToImageUrl(image.path).then((url) => {
      if (!cancelled) setResolvedUrl(url)
    })
    return () => { cancelled = true }
  }, [image.path])

  const dragging = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const resizing = useRef(false)
  const resizeStart = useRef({ x: 0, y: 0, w: 0, h: 0 })
  const panelRef = useRef<HTMLDivElement>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)

  // ─── Drag handlers ──────────────────────────────────────────────

  const onDragMouseDown = useCallback((e: React.MouseEvent) => {
    if (editing) return // Don't drag while editing title
    e.preventDefault()
    e.stopPropagation()
    dragging.current = true
    dragOffset.current = {
      x: e.clientX - pos.x,
      y: e.clientY - pos.y,
    }

    const onMouseMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      setPos({
        x: ev.clientX - dragOffset.current.x,
        y: ev.clientY - dragOffset.current.y,
      })
    }

    const onMouseUp = () => {
      dragging.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [pos, editing])

  // ─── Resize handlers ───────────────────────────────────────────

  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resizing.current = true
    resizeStart.current = {
      x: e.clientX,
      y: e.clientY,
      w: size.width,
      h: size.height,
    }

    const onMouseMove = (ev: MouseEvent) => {
      if (!resizing.current) return
      const dx = ev.clientX - resizeStart.current.x
      const dy = ev.clientY - resizeStart.current.y
      setSize({
        width: Math.max(MIN_WIDTH, resizeStart.current.w + dx),
        height: Math.max(MIN_HEIGHT, resizeStart.current.h + dy),
      })
    }

    const onMouseUp = () => {
      resizing.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [size])

  // ─── Title editing ─────────────────────────────────────────────

  const startEditing = useCallback(() => {
    setEditName(image.name)
    setEditing(true)
    // Focus the input after React renders it
    requestAnimationFrame(() => {
      nameInputRef.current?.focus()
      nameInputRef.current?.select()
    })
  }, [image.name])

  const commitRename = useCallback(() => {
    const trimmed = editName.trim()
    if (trimmed && trimmed !== image.name) {
      onRename?.(image.id, trimmed)
    }
    setEditing(false)
  }, [editName, image.id, image.name, onRename])

  const cancelEditing = useCallback(() => {
    setEditName(image.name)
    setEditing(false)
  }, [image.name])

  const handleNameKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      commitRename()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancelEditing()
    }
  }, [commitRename, cancelEditing])

  return (
    <>
      <div
        ref={panelRef}
        className="absolute z-30 animate-slide-up"
        style={{
          left: pos.x,
          top: pos.y,
          maxWidth: 'calc(100% - 32px)',
        }}
      >
        <div
          className="
            glass rounded-xl panel-shadow
            flex flex-col overflow-hidden
            border border-border
          "
          style={{ width: size.width }}
        >
          {/* ─── Header (drag handle) ─────────────────────────── */}
          <div
            className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0 cursor-grab active:cursor-grabbing select-none"
            onMouseDown={onDragMouseDown}
          >
            <GripHorizontal className="w-3.5 h-3.5 text-text-muted shrink-0" />

            {/* Editable title */}
            {editing ? (
              <div className="flex-1 flex items-center gap-1 min-w-0" onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
                <input
                  ref={nameInputRef}
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onKeyDown={handleNameKeyDown}
                  onBlur={commitRename}
                  className="flex-1 min-w-0 text-xs font-semibold text-text-primary bg-bg-secondary/80 border border-accent-primary/40 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-accent-primary/30"
                />
                <button
                  type="button"
                  onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); commitRename() }}
                  className="w-5 h-5 rounded flex items-center justify-center text-accent-success hover:bg-accent-success/10 transition-colors"
                >
                  <Check className="w-3 h-3" />
                </button>
              </div>
            ) : (
              <h3
                className="flex-1 text-xs font-semibold text-text-primary truncate min-w-0 cursor-text group"
                onDoubleClick={(e) => { e.stopPropagation(); startEditing() }}
                title="Double-click to rename"
              >
                {image.name}
                <Pencil className="w-2.5 h-2.5 text-text-muted/40 inline ml-1.5 opacity-0 group-hover:opacity-100 transition-opacity" />
              </h3>
            )}

            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                onClick={() => setPreviewOpen(true)}
                className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
                title="Fullscreen preview"
              >
                <Maximize2 className="w-3 h-3" />
              </button>
              <button
                type="button"
                onClick={() => onClose(image.id)}
                className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
                title="Close"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* ─── Image body ───────────────────────────────────── */}
          <div className="p-2 bg-bg-tertiary/30 relative" style={{ height: size.height }}>
            {!imgLoaded && (
              <div className="flex items-center justify-center h-full text-text-muted text-xs">
                Loading…
              </div>
            )}
            <img
              src={resolvedUrl}
              alt={image.name}
              className={`
                w-full h-full rounded-lg
                object-contain cursor-zoom-in
                ${imgLoaded ? '' : 'hidden'}
              `}
              onLoad={() => setImgLoaded(true)}
              onClick={() => setPreviewOpen(true)}
              draggable={false}
            />

            {/* ─── Resize handle (bottom-right corner) ────────── */}
            <div
              className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize group"
              onMouseDown={onResizeMouseDown}
              title="Drag to resize"
            >
              {/* Three diagonal lines as resize indicator */}
              <svg
                className="w-3 h-3 absolute bottom-0.5 right-0.5 text-text-muted/40 group-hover:text-text-muted transition-colors"
                viewBox="0 0 12 12"
                fill="none"
              >
                <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.2" />
                <line x1="11" y1="5" x2="5" y2="11" stroke="currentColor" strokeWidth="1.2" />
                <line x1="11" y1="9" x2="9" y2="11" stroke="currentColor" strokeWidth="1.2" />
              </svg>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Lightbox preview ─────────────────────────────────── */}
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
            src={resolvedUrl}
            alt={image.name}
            className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  )
})

PinnedImagePanel.displayName = 'PinnedImagePanel'
