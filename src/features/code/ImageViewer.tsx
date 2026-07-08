/**
 * ImageViewer — displays image files in a tab.
 * Uses pathToImageUrl to convert local file paths to Blob URLs
 * via Electron IPC (file:// URLs don't work in renderer).
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { ZoomIn, ZoomOut, RotateCcw, Download, Loader2 } from 'lucide-react'
import type { ViewTab } from '@/stores/viewStore'
import { pathToImageUrl, releaseImageUrl } from '@/services/rpc/handlers/_image_url'

interface ImageViewerProps {
  tab: ViewTab
}

export function ImageViewer({ tab }: ImageViewerProps) {
  const [scale, setScale] = useState(1)
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const dragStart = useRef({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  // Resolve local file path to blob URL
  useEffect(() => {
    if (!tab.filePath) return
    const filePath = tab.filePath
    let cancelled = false
    let acquired = false
    pathToImageUrl(filePath).then((url) => {
      acquired = true
      if (cancelled) {
        releaseImageUrl(filePath)
        return
      }
      setImageUrl(url)
    }).catch(() => {
      if (!cancelled) setImageUrl(null)
    })
    return () => {
      cancelled = true
      if (acquired) releaseImageUrl(filePath)
    }
  }, [tab.filePath])

  // Reset view when tab changes
  useEffect(() => {
    setScale(1)
    setPosition({ x: 0, y: 0 })
  }, [tab.filePath])

  const handleZoomIn = useCallback(() => {
    setScale((s) => Math.min(s * 1.25, 5))
  }, [])

  const handleZoomOut = useCallback(() => {
    setScale((s) => Math.max(s / 1.25, 0.1))
  }, [])

  const handleReset = useCallback(() => {
    setScale(1)
    setPosition({ x: 0, y: 0 })
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale((s) => Math.max(0.1, Math.min(5, s * delta)))
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button === 0) {
      setIsDragging(true)
      dragStart.current = { x: e.clientX - position.x, y: e.clientY - position.y }
    }
  }, [position])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (isDragging) {
      setPosition({
        x: e.clientX - dragStart.current.x,
        y: e.clientY - dragStart.current.y,
      })
    }
  }, [isDragging])

  const handleMouseUp = useCallback(() => {
    setIsDragging(false)
  }, [])

  const handleDownload = useCallback(() => {
    if (!imageUrl) return
    const a = document.createElement('a')
    a.href = imageUrl
    a.download = tab.title
    a.click()
  }, [imageUrl, tab.title])

  return (
    <div className="flex flex-col h-full bg-bg-tertiary">
      {/* Toolbar */}
      <div className="h-9 border-b border-border bg-bg-secondary flex items-center px-3 shrink-0 gap-1">
        <span className="text-xs text-text-secondary truncate flex-1">{tab.title}</span>
        <button onClick={handleZoomIn} className="p-1 text-text-muted hover:text-text-primary" title="Zoom in">
          <ZoomIn className="w-3.5 h-3.5" />
        </button>
        <button onClick={handleZoomOut} className="p-1 text-text-muted hover:text-text-primary" title="Zoom out">
          <ZoomOut className="w-3.5 h-3.5" />
        </button>
        <button onClick={handleReset} className="p-1 text-text-muted hover:text-text-primary" title="Reset zoom">
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
        <button onClick={handleDownload} className="p-1 text-text-muted hover:text-text-primary" title="Download" disabled={!imageUrl}>
          <Download className="w-3.5 h-3.5" />
        </button>
        <span className="text-[10px] text-text-muted/60 ml-1 font-mono">{Math.round(scale * 100)}%</span>
      </div>

      {/* Image container */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden bg-bg-primary cursor-grab active:cursor-grabbing"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {!imageUrl ? (
          <div className="w-full h-full flex items-center justify-center">
            <Loader2 className="w-6 h-6 text-text-muted animate-spin" />
          </div>
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{
              transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
              transformOrigin: 'center center',
              transition: isDragging ? 'none' : 'transform 0.1s ease-out',
            }}
          >
            <img
              src={imageUrl}
              alt={tab.title}
              className="max-w-full max-h-full object-contain select-none"
              draggable={false}
            />
          </div>
        )}
      </div>
    </div>
  )
}
