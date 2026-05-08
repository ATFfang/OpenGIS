/**
 * mapExport —— 把当前 MapLibre canvas 导出成 PNG/JPG。
 *
 * 关键约束：
 * 1. MapLibre canvas 必须初始化时开了 `preserveDrawingBuffer: true`，否则
 *    浏览器会在每帧 present 完后清空 drawing buffer，`canvas.toDataURL()`
 *    拿到黑图。MapEngine.init 已经做了这件事。
 * 2. DPI 提升（"高清导出"）的实现方式是临时 `map.setPixelRatio(scale)`，
 *    等 `map.once('idle')` 即瓦片全部绘完后 toBlob，再改回原值。
 *    setPixelRatio 是 MapLibre GL JS 3.x 引入的 API；2.x 需要走 resize
 *    canvas 的迂回方案。我们用 3.x，直接走官方 API。
 * 3. 导出不包含 HTML 浮层控件（放大缩小按钮、比例尺、我们自己的浮层），
 *    只包含 WebGL canvas 的内容——这正是用户想要的"干净地图"。
 * 4. JPEG 不支持透明，basemap 关闭时的黑底会照样被烧进 JPEG；PNG 保留
 *    alpha 通道，更推荐。UI 默认 PNG。
 */
import type maplibregl from 'maplibre-gl'
import { mapEngine } from '../engine/MapEngine'

export type ExportFormat = 'png' | 'jpg'

export interface ExportMapOptions {
  format?: ExportFormat
  /** JPEG 质量 0-1，只对 jpg 生效。默认 0.92。 */
  quality?: number
  /**
   * DPI 倍数（像素密度）。1 = 原始屏幕分辨率；2 = 高清导出（2x）；
   * 3 = 超高清。范围 [1, 4]，超过 4 容易触发 WebGL 纹理上限。
   * 默认 1。
   */
  dpiScale?: number
  /** 下载文件名（不含扩展名）。默认 `opengis-map-<timestamp>`。 */
  fileName?: string
  /**
   * true = 除返回 Blob 外还自动触发下载；false = 只返回 Blob 不下载。
   * 默认 true。
   */
  autoDownload?: boolean
}

export interface ExportMapResult {
  blob: Blob
  dataUrl: string
  width: number
  height: number
  format: ExportFormat
  fileName: string
}

/**
 * 把当前地图 canvas 导出成图片。
 *
 * @throws 如果 MapEngine 未初始化或 canvas 没开 preserveDrawingBuffer
 */
export async function exportMap(
  opts: ExportMapOptions = {},
): Promise<ExportMapResult> {
  const map = mapEngine.getMap()
  if (!map) {
    throw new Error('[mapExport] MapEngine not initialized')
  }
  const format: ExportFormat = opts.format ?? 'png'
  const mime = format === 'png' ? 'image/png' : 'image/jpeg'
  const quality = opts.quality ?? 0.92
  const dpiScale = clamp(opts.dpiScale ?? 1, 1, 4)
  const fileName =
    opts.fileName ??
    `opengis-map-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`
  const autoDownload = opts.autoDownload ?? true

  // ── DPI 提升：临时改 pixelRatio 并重绘，截完恢复 ────────────────
  const originalPixelRatio =
    typeof (map as any).getPixelRatio === 'function'
      ? (map as any).getPixelRatio()
      : window.devicePixelRatio || 1

  if (dpiScale !== 1 && typeof (map as any).setPixelRatio === 'function') {
    ;(map as any).setPixelRatio(originalPixelRatio * dpiScale)
    await waitForIdle(map)
  } else {
    // MapLibre 2.x 回退：只能用当前分辨率
    await waitForIdle(map)
  }

  // ── 抓 canvas ──────────────────────────────────────────────────
  const canvas = map.getCanvas()
  const width = canvas.width
  const height = canvas.height

  const blob = await canvasToBlob(canvas, mime, quality)
  const dataUrl = canvas.toDataURL(mime, quality)

  // ── 还原 pixel ratio ───────────────────────────────────────────
  if (dpiScale !== 1 && typeof (map as any).setPixelRatio === 'function') {
    ;(map as any).setPixelRatio(originalPixelRatio)
  }

  const fullFileName = `${fileName}.${format === 'jpg' ? 'jpg' : 'png'}`
  if (autoDownload) {
    triggerDownload(blob, fullFileName)
  }

  return {
    blob,
    dataUrl,
    width,
    height,
    format,
    fileName: fullFileName,
  }
}

// ─── 工具 ───────────────────────────────────────────────────────────

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

/**
 * 等地图渲染稳定下来。栅格瓦片异步下载、symbol 布局异步计算，都靠
 * 'idle' 事件汇合。有超时兜底避免永远等待。
 */
function waitForIdle(map: maplibregl.Map, timeoutMs = 5000): Promise<void> {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      map.off('idle', finish)
      resolve()
    }
    map.once('idle', finish)
    window.setTimeout(finish, timeoutMs)
  })
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  mime: string,
  quality: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob)
        else reject(new Error('[mapExport] canvas.toBlob returned null'))
      },
      mime,
      quality,
    )
  })
}

function triggerDownload(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 浏览器处理完下载后再 revoke，给点 slack
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
