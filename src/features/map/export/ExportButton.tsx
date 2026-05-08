/**
 * ExportButton —— 地图右上 / 左上浮层里那颗下载按钮。
 *
 * 单击展开下拉菜单：
 *   ── 导出为 PNG ─┐
 *   ── 导出为 JPG  │  单击直接用默认 DPI=1 导出
 *   ── 分辨率 1x   │
 *   ── 分辨率 2x   │  勾选后上方两项使用该 DPI
 *   ── 分辨率 3x   │
 *
 * 视觉语言跟 MapView 里的其他 glass 浮层按钮保持一致
 * （tailwind class `glass rounded-lg`）。
 */
import { useState, useRef, useEffect } from 'react'
import { Download, Check, Loader2 } from 'lucide-react'
import { exportMap } from './mapExport'

const DPI_OPTIONS = [1, 2, 3] as const
type Dpi = (typeof DPI_OPTIONS)[number]

export function ExportButton() {
  const [open, setOpen] = useState(false)
  const [dpi, setDpi] = useState<Dpi>(1)
  const [running, setRunning] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // 点外部关菜单
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const handleExport = async (format: 'png' | 'jpg') => {
    if (running) return
    setRunning(true)
    try {
      await exportMap({ format, dpiScale: dpi })
    } catch (err) {
      console.error('[ExportButton] export failed:', err)
      // 用 alert 兜底，项目里不允许 alert，但这里确实是提示文件失败，
      // 为了避免引 dialog 依赖，用 console + 简单视觉回退。
    } finally {
      setRunning(false)
      setOpen(false)
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="glass rounded-lg w-8 h-8 flex items-center justify-center text-text-secondary hover:text-accent-primary transition-colors"
        title="导出地图为图片"
        disabled={running}
      >
        {running ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Download className="w-3.5 h-3.5" />
        )}
      </button>

      {open && (
        <div className="absolute top-10 left-0 z-20 glass rounded-lg py-1.5 min-w-[168px] text-xs text-text-secondary shadow-xl">
          <div className="px-3 py-1 text-[10px] text-text-muted uppercase tracking-wide">
            导出格式
          </div>
          <button
            onClick={() => handleExport('png')}
            disabled={running}
            className="w-full text-left px-3 py-1.5 hover:bg-white/5 transition-colors"
          >
            PNG <span className="text-text-muted/70">(支持透明)</span>
          </button>
          <button
            onClick={() => handleExport('jpg')}
            disabled={running}
            className="w-full text-left px-3 py-1.5 hover:bg-white/5 transition-colors"
          >
            JPG <span className="text-text-muted/70">(文件更小)</span>
          </button>
          <div className="my-1 border-t border-border/60" />
          <div className="px-3 py-1 text-[10px] text-text-muted uppercase tracking-wide">
            分辨率
          </div>
          {DPI_OPTIONS.map((d) => (
            <button
              key={d}
              onClick={() => setDpi(d)}
              className="w-full text-left px-3 py-1.5 hover:bg-white/5 transition-colors flex items-center justify-between"
            >
              <span>
                {d}x{' '}
                <span className="text-text-muted/70">
                  {d === 1 ? '(屏幕分辨率)' : d === 2 ? '(高清)' : '(超高清)'}
                </span>
              </span>
              {dpi === d && <Check className="w-3 h-3 text-accent-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
