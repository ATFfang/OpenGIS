import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BarChart3, ChevronDown, ChevronRight, Database, Loader2, Move, Table, X } from 'lucide-react'
import appIconImg from '../../../resources/icons/app-icon.png'
import { useMapStore } from '@/stores/mapStore'
import { useAssetStore } from '@/stores/assetStore'
import { usePivotStore } from '@/stores/pivotStore'
import { loadPivotData } from './pivotData'
import { computePivotAnalysis, runPivotAgent, type PivotAgentLog } from './pivotAnalysis'
import type { PivotAgentResult, PivotData, PivotFieldDistribution, PivotFieldStat, PivotTarget } from './types'

const PANEL_WIDTH = 760
const PANEL_HEIGHT = 620

function getDefaultPanelPosition() {
  if (typeof window === 'undefined') return { x: 120, y: 64 }
  return {
    x: Math.max(8, Math.round((window.innerWidth - PANEL_WIDTH) / 2)),
    y: Math.max(8, Math.round((window.innerHeight - PANEL_HEIGHT) / 2)),
  }
}

export function DataPivotPanel() {
  const { isOpen, target, mode, close, setMode } = usePivotStore()
  const layers = useMapStore((s) => s.layers)
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const [data, setData] = useState<PivotData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [agentLoading, setAgentLoading] = useState(false)
  const [agentResult, setAgentResult] = useState<PivotAgentResult | null>(null)
  const [agentLogs, setAgentLogs] = useState<PivotAgentLog[]>([])
  const [position, setPosition] = useState(getDefaultPanelPosition)
  const panelRef = useRef<HTMLDivElement>(null)
  const positionRef = useRef(position)
  const dragFrameRef = useRef<number | null>(null)
  const dragRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    originX: number
    originY: number
    nextX: number
    nextY: number
  } | null>(null)
  const nextLogId = useRef(0)

  const pushAgentLog = useCallback((log: Omit<PivotAgentLog, 'id'>) => {
    setAgentLogs((prev) => [
      ...prev,
      { ...log, id: nextLogId.current++ },
    ].slice(-240))
  }, [])

  useEffect(() => {
    return () => {
      if (dragFrameRef.current !== null) {
        window.cancelAnimationFrame(dragFrameRef.current)
        dragFrameRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!isOpen || !target) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setData(null)
    setAgentResult(null)
    setAgentLogs([])
    nextLogId.current = 0
    const defaultPosition = getDefaultPanelPosition()
    positionRef.current = defaultPosition
    setPosition(defaultPosition)
    loadPivotData(target, layers)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isOpen, target, layers])

  useEffect(() => {
    if (!isOpen || mode !== 'agent' || !data || agentResult || agentLoading) return
    let cancelled = false
    setAgentLoading(true)
    pushAgentLog({ stream: 'info', text: '准备数据样本与字段信息。\n', ts: Date.now() })
    runPivotAgent(data, workspacePath, { onLog: pushAgentLog })
      .then((result) => {
        if (!cancelled) setAgentResult(result)
      })
      .catch((err) => {
        if (!cancelled) {
          const fallback = computePivotAnalysis(data)
          setAgentResult({
            ...fallback,
            summary: `${fallback.summary} 后台分析失败，已使用前端统计兜底：${err instanceof Error ? err.message : String(err)}`,
          })
          pushAgentLog({
            stream: 'error',
            text: `${err instanceof Error ? err.message : String(err)}\n`,
            ts: Date.now(),
          })
        }
      })
      .finally(() => {
        if (!cancelled) setAgentLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [isOpen, mode, data, agentResult, workspacePath, pushAgentLog])

  const handleHeaderPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const targetEl = e.target as HTMLElement
    if (targetEl.closest('button,input,label,[data-pivot-control]')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    positionRef.current = position
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originX: position.x,
      originY: position.y,
      nextX: position.x,
      nextY: position.y,
    }
  }, [position.x, position.y])

  const handleHeaderPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    const maxX = Math.max(8, window.innerWidth - 360)
    const maxY = Math.max(8, window.innerHeight - 120)
    drag.nextX = Math.min(maxX, Math.max(8, drag.originX + e.clientX - drag.startX))
    drag.nextY = Math.min(maxY, Math.max(8, drag.originY + e.clientY - drag.startY))
    positionRef.current = { x: drag.nextX, y: drag.nextY }

    if (dragFrameRef.current !== null) return
    dragFrameRef.current = window.requestAnimationFrame(() => {
      dragFrameRef.current = null
      const panel = panelRef.current
      if (!panel) return
      const { x, y } = positionRef.current
      panel.style.transform = `translate3d(${x}px, ${y}px, 0)`
    })
  }, [])

  const handleHeaderPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (drag?.pointerId === e.pointerId) {
      dragRef.current = null
      if (dragFrameRef.current !== null) {
        window.cancelAnimationFrame(dragFrameRef.current)
        dragFrameRef.current = null
      }
      const next = positionRef.current
      const panel = panelRef.current
      if (panel) panel.style.transform = `translate3d(${next.x}px, ${next.y}px, 0)`
      setPosition(next)
      try {
        e.currentTarget.releasePointerCapture(e.pointerId)
      } catch {
        // Pointer capture may already be released by the browser.
      }
    }
  }, [])

  if (!isOpen || !target) return null

  return (
    <div
      ref={panelRef}
      className="fixed z-40 pointer-events-auto"
      style={{
        left: 0,
        top: 0,
        transform: `translate3d(${position.x}px, ${position.y}px, 0)`,
        willChange: 'transform',
        width: `min(${PANEL_WIDTH}px, calc(100vw - 24px))`,
        height: `min(${PANEL_HEIGHT}px, calc(100vh - 24px))`,
      }}
    >
      <div className="h-full bg-bg-primary border border-border rounded-lg shadow-2xl flex flex-col overflow-hidden">
        <div
          className="h-10 shrink-0 border-b border-border bg-bg-secondary flex items-center gap-2 px-3 cursor-move select-none"
          onPointerDown={handleHeaderPointerDown}
          onPointerMove={handleHeaderPointerMove}
          onPointerUp={handleHeaderPointerUp}
          onPointerCancel={handleHeaderPointerUp}
        >
          <Move className="w-3.5 h-3.5 text-text-muted shrink-0" />
          <div className="w-6 h-6 rounded bg-accent-primary/10 flex items-center justify-center">
            <Database className="w-3.5 h-3.5 text-accent-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold text-text-primary truncate">数据透视</div>
            <div className="text-2xs text-text-muted truncate">{data?.title ?? target.name}</div>
          </div>
          <div className="flex items-center gap-1 bg-bg-tertiary rounded-md p-0.5 border border-border" data-pivot-control>
            <button
              onClick={() => setMode('data')}
              className={`h-7 px-2 rounded text-2xs flex items-center gap-1.5 transition-colors ${
                mode === 'data' ? 'bg-bg-primary text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Table className="w-3 h-3" />
              数据
            </button>
            <button
              onClick={() => setMode('agent')}
              className={`h-7 px-2 rounded text-2xs flex items-center gap-1.5 transition-colors ${
                mode === 'agent' ? 'bg-bg-primary text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary'
              }`}
              title="Agent 透视"
            >
              <img src={appIconImg} className="w-3.5 h-3.5 rounded-sm" />
              Agent
            </button>
          </div>
          <button
            onClick={close}
            data-pivot-control
            className="w-7 h-7 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {loading ? (
          <LoadingState label="正在读取数据..." />
        ) : error ? (
          <ErrorState message={error} />
        ) : !data ? (
          <EmptyState />
        ) : mode === 'agent' ? (
          <AgentPivotView loading={agentLoading} result={agentResult} logs={agentLogs} />
        ) : (
          <DataPivotView data={data} target={target} />
        )}
      </div>
    </div>
  )
}

function DataPivotView({ data, target }: { data: PivotData; target: PivotTarget }) {
  if (data.raster) {
    return (
      <div className="flex-1 min-h-0 overflow-auto p-3 space-y-3">
        <InfoGrid items={data.raster.meta} />
        <SimpleTable columns={Object.keys(data.raster.rows[0] ?? {})} rows={data.raster.rows} />
      </div>
    )
  }

  if (!data.table) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        {data.warning && (
          <div className="shrink-0 px-3 py-2 text-2xs text-accent-warning bg-accent-warning/10 border-b border-accent-warning/20">
            {data.warning}
          </div>
        )}
        <EmptyState />
      </div>
    )
  }
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {data.warning && (
        <div className="shrink-0 px-3 py-2 text-2xs text-accent-warning bg-accent-warning/10 border-b border-accent-warning/20">
          {data.warning}
        </div>
      )}
      <div className="h-8 shrink-0 border-b border-border bg-bg-secondary flex items-center gap-3 px-3 text-2xs text-text-muted">
        {target.kind === 'file' && target.size > 0 && (
          <span>{formatFileSize(target.size)}</span>
        )}
        <span>{data.table.totalRows?.toLocaleString() ?? '?'} 行</span>
        <span>{data.table.columns.length} 列</span>
        {data.table.sampled && <span className="text-accent-warning">已抽样显示</span>}
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        <SimpleTable columns={data.table.columns} rows={data.table.rows} sticky />
      </div>
    </div>
  )
}

function AgentPivotView({
  loading,
  result,
  logs,
}: {
  loading: boolean
  result: PivotAgentResult | null
  logs: PivotAgentLog[]
}) {
  const [selectedFields, setSelectedFields] = useState<string[]>([])

  useEffect(() => {
    if (!result || selectedFields.length > 0) return
    setSelectedFields(result.distributions.slice(0, 3).map((item) => item.field))
  }, [result, selectedFields.length])

  const selectedDistributions = useMemo(() => {
    if (!result) return []
    const set = new Set(selectedFields)
    return result.distributions.filter((item) => set.has(item.field))
  }, [result, selectedFields])

  if (loading) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex-1 flex flex-col items-center justify-center gap-4 px-8">
          <img src={appIconImg} className="w-10 h-10 rounded-lg shadow-sm" />
          <div className="w-64 h-1.5 rounded-full bg-bg-tertiary overflow-hidden">
            <div className="h-full w-1/2 rounded-full bg-accent-primary pivot-wait-bar" />
          </div>
          <div className="text-xs text-text-muted">Agent 正在统计数据...</div>
          <style>{`
            .pivot-wait-bar {
              animation: pivot-wait 1.2s ease-in-out infinite;
            }
            @keyframes pivot-wait {
              0% { transform: translateX(-100%); }
              100% { transform: translateX(220%); }
            }
          `}</style>
        </div>
        <ProcessOutput logs={logs} defaultOpen />
      </div>
    )
  }

  if (!result) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <EmptyState />
        <ProcessOutput logs={logs} />
      </div>
    )
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col p-3 gap-3">
      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-2 gap-3">
        <section className="border border-border rounded-lg overflow-hidden bg-bg-secondary flex flex-col">
          <SectionHeader icon={<BarChart3 className="w-3.5 h-3.5" />} title="字段概率分布" />
          <DistributionSelector
            distributions={result.distributions}
            selected={selectedFields}
            onChange={setSelectedFields}
          />
          <div className="flex-1 min-h-0 overflow-auto">
            <DistributionChart distributions={selectedDistributions} />
          </div>
        </section>
        <section className="border border-border rounded-lg overflow-hidden bg-bg-secondary flex flex-col">
          <SectionHeader icon={<Table className="w-3.5 h-3.5" />} title="字段统计值" />
          <div className="flex-1 min-h-0 overflow-auto">
            <StatsTable stats={result.stats} />
          </div>
        </section>
      </div>
      <section className="shrink-0 border border-border rounded-lg bg-bg-secondary p-3">
        <div className="flex items-start gap-2">
          <img src={appIconImg} className="w-5 h-5 rounded shrink-0 mt-0.5" />
          <div>
            <div className="text-xs font-medium text-text-primary mb-1">
              分析摘要
              <span className="ml-2 text-2xs font-normal text-text-muted">
                {result.engine === 'python' ? 'Python 后台分析' : '前端统计兜底'}
                {result.durationMs ? ` · ${result.durationMs}ms` : ''}
              </span>
            </div>
            <p className="text-xs leading-5 text-text-secondary">{result.summary}</p>
          </div>
        </div>
      </section>
      <ProcessOutput logs={logs} />
    </div>
  )
}

function ProcessOutput({
  logs,
  defaultOpen = false,
}: {
  logs: PivotAgentLog[]
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const bottomRef = useRef<HTMLSpanElement>(null)
  const content = logs.length > 0 ? logs : [{ id: -1, stream: 'info' as const, text: '暂无过程输出。\n', ts: Date.now() }]

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [open, logs.length])

  return (
    <div className="shrink-0 border-t border-border bg-bg-secondary">
      <button
        onClick={() => setOpen((value) => !value)}
        className="w-full h-8 px-3 flex items-center gap-2 text-2xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        <span className="font-medium">过程输出</span>
        <span className="text-text-muted">{logs.length} 条</span>
      </button>
      {open && (
        <div className="max-h-36 overflow-auto border-t border-border bg-bg-primary">
          <pre className="p-3 text-2xs leading-5 font-mono whitespace-pre-wrap break-words">
            {content.map((log) => (
              <span
                key={log.id}
                className={
                  log.stream === 'stderr' || log.stream === 'error'
                    ? 'text-accent-danger'
                    : log.stream === 'stdout'
                      ? 'text-text-secondary'
                      : 'text-text-muted'
                }
              >
                {formatLogTime(log.ts)} {log.text}
              </span>
            ))}
            <span ref={bottomRef} />
          </pre>
        </div>
      )}
    </div>
  )
}

function DistributionSelector({
  distributions,
  selected,
  onChange,
}: {
  distributions: PivotFieldDistribution[]
  selected: string[]
  onChange: (fields: string[]) => void
}) {
  if (distributions.length === 0) {
    return <div className="px-3 py-2 text-2xs text-text-muted border-b border-border">没有可渲染的字段分布。</div>
  }
  return (
    <div className="px-3 py-2 border-b border-border flex flex-wrap gap-1.5">
      {distributions.slice(0, 16).map((dist) => {
        const checked = selected.includes(dist.field)
        return (
          <label
            key={dist.field}
            className={`px-2 py-1 rounded border text-2xs cursor-pointer transition-colors ${
              checked
                ? 'border-accent-primary/50 bg-accent-primary/12 text-accent-primary'
                : 'border-border bg-bg-primary text-text-muted hover:text-text-secondary'
            }`}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={checked}
              onChange={() => {
                if (checked) onChange(selected.filter((field) => field !== dist.field))
                else onChange([...selected, dist.field].slice(-4))
              }}
            />
            {dist.field}
          </label>
        )
      })}
    </div>
  )
}

function DistributionChart({ distributions }: { distributions: PivotFieldDistribution[] }) {
  if (distributions.length === 0) {
    return <div className="h-48 flex items-center justify-center text-xs text-text-muted">请选择字段</div>
  }
  return (
    <div className="p-3 space-y-4">
      {distributions.map((dist) => (
        <DistributionCurve key={dist.field} dist={dist} index={distributions.indexOf(dist)} />
      ))}
    </div>
  )
}

function DistributionCurve({ dist, index }: { dist: PivotFieldDistribution; index: number }) {
  const colors = ['#3b82f6', '#10b981', '#8b5cf6', '#f59e0b']
  const color = colors[index % colors.length]

  const buckets = dist.buckets
  if (buckets.length < 2) {
    return (
      <div className="bg-bg-secondary rounded-lg border border-border/50 p-3">
        <div className="text-xs text-text-muted">{dist.field} — 数据不足，无法绘制分布</div>
      </div>
    )
  }

  // Build SVG smooth curve
  const W = 360
  const H = 80
  const pad = 4
  const maxProb = Math.max(...buckets.map((b) => b.probability), 0.001)
  const n = buckets.length

  // Generate points
  const points: [number, number][] = buckets.map((b, i) => {
    const x = pad + (i / (n - 1)) * (W - 2 * pad)
    const y = H - pad - (b.probability / maxProb) * (H - 2 * pad)
    return [x, y]
  })

  // Catmull-Rom spline → smooth SVG path
  function catmullRom(pts: [number, number][]): string {
    if (pts.length < 2) return ''
    let d = `M ${pts[0][0]},${pts[0][1]}`
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(0, i - 1)]
      const p1 = pts[i]
      const p2 = pts[i + 1]
      const p3 = pts[Math.min(pts.length - 1, i + 2)]
      const cp1x = p1[0] + (p2[0] - p0[0]) / 6
      const cp1y = p1[1] + (p2[1] - p0[1]) / 6
      const cp2x = p2[0] - (p3[0] - p1[0]) / 6
      const cp2y = p2[1] - (p3[1] - p1[1]) / 6
      d += ` C ${cp1x},${cp1y} ${cp2x},${cp2y} ${p2[0]},${p2[1]}`
    }
    return d
  }

  const curvePath = catmullRom(points)
  const fillPath = `${curvePath} L ${points[points.length - 1][0]},${H - pad} L ${points[0][0]},${H - pad} Z`

  // Peak info
  const peakBucket = buckets.reduce((a, b) => (b.probability > a.probability ? b : a), buckets[0])

  return (
    <div className="bg-bg-secondary rounded-lg border border-border/50 p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-text-primary truncate">{dist.field}</span>
        <span className="text-2xs text-text-muted">峰值 {Math.round(peakBucket.probability * 100)}% @ {peakBucket.label}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }}>
        <defs>
          <linearGradient id={`grad-${index}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* Fill area under curve */}
        <path d={fillPath} fill={`url(#grad-${index})`} />
        {/* Curve line */}
        <path d={curvePath} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" />
        {/* Baseline */}
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--border-color)" strokeWidth="0.5" />
      </svg>
      {/* X-axis labels */}
      <div className="flex justify-between mt-1">
        <span className="text-2xs text-text-muted">{buckets[0]?.label ?? ''}</span>
        <span className="text-2xs text-text-muted">{buckets[buckets.length - 1]?.label ?? ''}</span>
      </div>
    </div>
  )
}

function StatsTable({ stats }: { stats: PivotFieldStat[] }) {
  return (
    <table className="w-full text-2xs border-collapse">
      <thead className="sticky top-0 bg-bg-tertiary z-10">
        <tr className="text-left text-text-muted">
          {['字段', '类型', '非空', '空值', '唯一值', '最小', '最大', '均值'].map((header) => (
            <th key={header} className="px-2 py-1.5 border-b border-border font-medium whitespace-nowrap">{header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {stats.map((stat) => (
          <tr
            key={stat.field}
            className="hover:bg-bg-hover"
            style={{ contentVisibility: 'auto', containIntrinsicSize: '28px' } as React.CSSProperties}
          >
            <td className="px-2 py-1.5 border-b border-border text-text-primary max-w-[160px] truncate" title={stat.field}>{stat.field}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-muted">{stat.type}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary tabular-nums">{stat.count.toLocaleString()}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary tabular-nums">{stat.nullCount.toLocaleString()}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary tabular-nums">{stat.uniqueCount.toLocaleString()}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary truncate max-w-[100px]">{formatCell(stat.min)}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary truncate max-w-[100px]">{formatCell(stat.max)}</td>
            <td className="px-2 py-1.5 border-b border-border text-text-secondary tabular-nums">{formatNumber(stat.mean)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function SimpleTable({
  columns,
  rows,
  sticky = false,
}: {
  columns: string[]
  rows: Record<string, unknown>[]
  sticky?: boolean
}) {
  if (columns.length === 0) {
    return <div className="h-full flex items-center justify-center text-xs text-text-muted">没有可展示的数据表。</div>
  }
  return (
    <table className="w-max min-w-full text-xs border-collapse bg-bg-primary">
      <thead className={sticky ? 'sticky top-0 z-10' : ''}>
        <tr>
          <th className="sticky left-0 z-20 bg-bg-secondary px-2 py-1.5 border-b border-r border-border text-right text-text-muted font-medium">#</th>
          {columns.map((column) => (
            <th key={column} className="bg-bg-secondary px-2 py-1.5 border-b border-border text-left text-text-primary font-medium whitespace-nowrap">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr
            key={rowIndex}
            className="hover:bg-bg-hover"
            style={{ contentVisibility: 'auto', containIntrinsicSize: '28px' } as React.CSSProperties}
          >
            <td className="sticky left-0 bg-bg-secondary px-2 py-1.5 border-b border-r border-border text-right text-text-muted tabular-nums">
              {rowIndex + 1}
            </td>
            {columns.map((column) => (
              <td key={column} className="px-2 py-1.5 border-b border-border text-text-secondary whitespace-nowrap max-w-[280px] truncate" title={String(row[column] ?? '')}>
                {formatCell(row[column])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function InfoGrid({ items }: { items: Array<{ label: string; value: string }> }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
      {items.map((item) => (
        <div key={item.label} className="bg-bg-secondary border border-border rounded-md px-2 py-2">
          <div className="text-2xs text-text-muted mb-1">{item.label}</div>
          <div className="text-xs text-text-primary truncate" title={item.value}>{item.value}</div>
        </div>
      ))}
    </div>
  )
}

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="h-8 border-b border-border flex items-center gap-2 px-3 text-xs font-medium text-text-primary">
      <span className="text-accent-primary">{icon}</span>
      {title}
    </div>
  )
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <Loader2 className="w-6 h-6 text-accent-primary animate-spin mx-auto mb-2" />
        <div className="text-xs text-text-muted">{label}</div>
      </div>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-md text-center">
        <div className="text-sm font-medium text-text-primary mb-2">无法打开数据透视</div>
        <div className="text-xs leading-5 text-text-muted whitespace-pre-wrap">{message}</div>
      </div>
    </div>
  )
}

function EmptyState() {
  return <div className="flex-1 flex items-center justify-center text-xs text-text-muted">暂无可透视数据。</div>
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '·'
  if (typeof value === 'number') return formatNumber(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function formatNumber(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '·'
  if (Math.abs(value) >= 1000 || Math.abs(value) < 0.01) return value.toExponential(2)
  return Number(value.toFixed(4)).toString()
}

function formatLogTime(ts: number): string {
  const date = new Date(ts)
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  const ss = String(date.getSeconds()).padStart(2, '0')
  return `[${hh}:${mm}:${ss}]`
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
