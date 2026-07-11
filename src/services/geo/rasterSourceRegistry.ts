/**
 * Session-local raster source registry.
 *
 * Dragged / picker-loaded File objects often do not expose a stable absolute
 * path in Electron renderer code. Without a path, raster style editing cannot
 * re-read the TIFF from disk. Keep the original ArrayBuffer behind a lightweight
 * handle so the current session can re-render color ramps without bloating
 * persisted map-layer JSON.
 */

const rasterBuffers = new Map<string, ArrayBuffer>()
let nextRasterBufferId = 1

export function registerRasterBuffer(buffer: ArrayBuffer): string {
  const id = `raster-buffer:${nextRasterBufferId++}`
  rasterBuffers.set(id, buffer.slice(0))
  return id
}

export function getRasterBuffer(id: string | undefined): ArrayBuffer | undefined {
  if (!id) return undefined
  const buffer = rasterBuffers.get(id)
  return buffer ? buffer.slice(0) : undefined
}

export function releaseRasterBuffer(id: string | undefined): void {
  if (!id) return
  rasterBuffers.delete(id)
}
