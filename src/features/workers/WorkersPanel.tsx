import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Activity,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  FileCode2,
  FolderOpen,
  HardDrive,
  Pause,
  RefreshCw,
  RotateCw,
  Trash2,
} from 'lucide-react'
import { pythonClient } from '@/services/pythonClient'
import { useT } from '@/i18n'
import { useAssetStore } from '@/stores/assetStore'
import { useViewStore } from '@/stores/viewStore'

interface WorkerLog {
  ts: number
  stream: string
  text: string
}

interface WorkerResources {
  available?: boolean
  cpu_percent?: number | null
  rss_bytes?: number | null
  rss_mb?: number | null
  elapsed?: string | null
  sampled_at?: number
  error?: string
}

interface ResidentWorker {
  id: string
  name: string
  description?: string
  status: string
  pid?: number | null
  folder?: string
  script_path?: string
  last_error?: string | null
  created_at?: number
  updated_at?: number
  started_at?: number | null
  stopped_at?: number | null
  returncode?: number | null
  resources?: WorkerResources
  logs?: WorkerLog[]
  manifest?: {
    schema_version?: number
    kind?: string
    entrypoint?: string
    layers?: Array<Record<string, unknown>>
  }
  package?: {
    schema_version?: number
    entrypoint?: string
    has_readme?: boolean
    has_config?: boolean
    src_files?: string[]
  }
}

const ACTIVE_STATUSES = new Set(['starting', 'running'])
const LOG_PREVIEW_LIMIT = 50
const RESOURCE_HISTORY_LIMIT = 40

interface ResourceSample {
  ts: number
  cpu: number | null
  memory: number | null
}

export function WorkersPanel({ onOpenScriptTab }: { onOpenScriptTab?: () => void }) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const [workers, setWorkers] = useState<ResidentWorker[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resourceHistory, setResourceHistory] = useState<Record<string, ResourceSample[]>>({})
  const refreshInFlightRef = useRef(false)
  const mountedRef = useRef(true)
  const workspacePathRef = useRef(workspacePath)

  useEffect(() => {
    workspacePathRef.current = workspacePath
  }, [workspacePath])

  const refresh = useCallback(async () => {
    if (refreshInFlightRef.current) return
    refreshInFlightRef.current = true
    if (mountedRef.current) {
      setLoading(true)
      setError(null)
    }
    try {
      const result = await pythonClient.send('rpc.worker.list', {
        include_logs: false,
        workspace_path: workspacePath || undefined,
      }, 15000)
      if (!mountedRef.current || workspacePathRef.current !== workspacePath) return
      setWorkers((previous) => mergeWorkerLogs(previous, Array.isArray(result?.workers) ? result.workers : []))
    } catch (err: any) {
      if (mountedRef.current) setError(err?.message || String(err))
    } finally {
      refreshInFlightRef.current = false
      if (mountedRef.current) setLoading(false)
    }
  }, [workspacePath])

  useEffect(() => {
    mountedRef.current = true
    refresh()
    const timer = window.setInterval(refresh, 2000)
    return () => {
      mountedRef.current = false
      window.clearInterval(timer)
    }
  }, [refresh])

  const refreshWorkerLogs = useCallback(async (id: string) => {
    try {
      const result = await pythonClient.send('rpc.worker.get', {
        worker_id: id,
        include_logs: true,
        workspace_path: workspacePath || undefined,
      }, 15000)
      const worker = result?.worker
      if (!mountedRef.current || workspacePathRef.current !== workspacePath || !worker?.id) return
      setWorkers((previous) => previous.map((item) => (
        item.id === worker.id ? { ...item, ...worker } : item
      )))
    } catch (err: any) {
      if (mountedRef.current) setError(err?.message || String(err))
    }
  }, [workspacePath])

  useEffect(() => {
    if (expanded.size === 0) return
    const timer = window.setInterval(() => {
      for (const id of expanded) {
        refreshWorkerLogs(id).catch(() => {})
      }
    }, 2500)
    return () => window.clearInterval(timer)
  }, [expanded, refreshWorkerLogs])

  const activeCount = useMemo(
    () => workers.filter((worker) => ACTIVE_STATUSES.has(worker.status)).length,
    [workers],
  )

  useEffect(() => {
    setResourceHistory((prev) => {
      const liveIds = new Set(workers.map((worker) => worker.id))
      const next: Record<string, ResourceSample[]> = {}
      for (const worker of workers) {
        const resources = worker.resources
        const sampledAt = typeof resources?.sampled_at === 'number' ? resources.sampled_at : Date.now() / 1000
        const cpu = typeof resources?.cpu_percent === 'number' ? resources.cpu_percent : null
        const memory = typeof resources?.rss_mb === 'number' ? resources.rss_mb : null
        const current = prev[worker.id] ?? []
        const last = current[current.length - 1]
        const shouldAppend = !last || Math.abs(last.ts - sampledAt) > 0.25
        next[worker.id] = shouldAppend
          ? [...current, { ts: sampledAt, cpu, memory }].slice(-RESOURCE_HISTORY_LIMIT)
          : current
      }
      for (const id of Object.keys(prev)) {
        if (liveIds.has(id) && !next[id]) next[id] = prev[id]
      }
      return next
    })
  }, [workers])

  const toggleExpanded = (id: string) => {
    let shouldLoadLogs = false
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else {
        next.add(id)
        shouldLoadLogs = true
      }
      return next
    })
    if (shouldLoadLogs) refreshWorkerLogs(id).catch(() => {})
  }

  const pauseWorker = async (id: string) => {
    setBusyId(id)
    try {
      await pythonClient.send('rpc.worker.pause', {
        worker_id: id,
        reason: 'ui_pause',
        workspace_path: workspacePath || undefined,
      })
      await refresh()
    } finally {
      setBusyId(null)
    }
  }

  const restartWorker = async (id: string) => {
    setBusyId(id)
    try {
      await pythonClient.send('rpc.worker.restart', {
        worker_id: id,
        reason: 'ui_restart',
        initial_health_timeout: 1.5,
        workspace_path: workspacePath || undefined,
      })
      await refresh()
    } finally {
      setBusyId(null)
    }
  }

  const deleteWorker = async (id: string) => {
    if (!window.confirm(t.workers.deleteConfirm)) return
    setBusyId(id)
    try {
      await pythonClient.send('rpc.worker.delete', {
        worker_id: id,
        workspace_path: workspacePath || undefined,
      })
      await refresh()
    } finally {
      setBusyId(null)
    }
  }

  const openScript = async (worker: ResidentWorker) => {
    if (!worker.script_path) return
    const api = window.electronAPI
    if (!api?.readFile) {
      setError('electronAPI.readFile is unavailable')
      return
    }
    setOpeningId(worker.id)
    setError(null)
    try {
      const result = await api.readFile(worker.script_path)
      if (!result?.success || result.content == null) {
        throw new Error(result?.error || 'readFile returned no content')
      }
      useViewStore.getState().openFileAsTab(
        worker.script_path,
        fileNameFromPath(worker.script_path),
        result.content,
        'python',
      )
      onOpenScriptTab?.()
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setOpeningId(null)
    }
  }

  return (
    <div className="h-full min-h-0 flex flex-col bg-bg-primary">
      <header className="shrink-0 px-5 py-4 bg-bg-primary">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-primary/10 text-accent-primary">
            <Activity className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[18px] font-semibold text-text-primary">{t.workers.title}</h2>
              <span className="rounded-full bg-bg-secondary px-2.5 py-1 text-[11px] text-text-secondary">
                {activeCount}/2 {t.workers.running}
              </span>
            </div>
            <p className="mt-0.5 text-[12px] text-text-muted">{t.workers.emptyHint}</p>
          </div>
          <button
            onClick={refresh}
            disabled={loading}
            className="h-9 rounded-lg bg-bg-secondary px-3 text-[12px] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-50 flex items-center gap-2"
            title={t.common.refresh}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t.common.refresh}
          </button>
        </div>
      </header>

      {error && (
        <div className="mx-5 mb-3 rounded-lg bg-accent-danger/10 px-3 py-2 text-[12px] text-accent-danger">
          {error}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-5 scrollbar-thin">
        {workers.length === 0 && !loading ? (
          <div className="h-full min-h-[360px] flex flex-col items-center justify-center text-center text-text-muted">
            <Activity className="mb-3 h-10 w-10 opacity-40" />
            <p className="text-[14px] text-text-secondary">{t.workers.empty}</p>
            <p className="mt-1 max-w-[380px] text-[12px] leading-relaxed">{t.workers.emptyHint}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2 2xl:grid-cols-3">
            {workers.map((worker) => {
              const isExpanded = expanded.has(worker.id)
              const active = ACTIVE_STATUSES.has(worker.status)
              const logs = worker.logs ?? []
              const resources = worker.resources
              const cpu = typeof resources?.cpu_percent === 'number' ? resources.cpu_percent : null
              const memory = typeof resources?.rss_mb === 'number' ? resources.rss_mb : null
              const history = resourceHistory[worker.id] ?? []
              const packageInfo = worker.package
              const manifest = worker.manifest
              const srcFiles = packageInfo?.src_files ?? []

              return (
                <section
                  key={worker.id}
                  className="min-w-0 overflow-hidden rounded-xl bg-[var(--worker-surface)] shadow-sm shadow-black/10"
                >
                  <div className="p-3">
                    <div className="flex items-start gap-2.5">
                      <span className={`mt-1.5 h-2.5 w-2.5 rounded-full shrink-0 ${statusClass(worker.status)}`} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="min-w-0 truncate text-[14px] font-semibold text-text-primary">
                            {worker.name || worker.id}
                          </h3>
                          <span className="rounded-md bg-bg-tertiary px-1.5 py-0.5 text-[10px] font-mono text-text-muted">
                            {worker.status}
                          </span>
                          {(manifest?.kind || packageInfo?.entrypoint) && (
                            <span className="rounded-md bg-[var(--worker-surface-soft)] px-1.5 py-0.5 text-[10px] text-text-muted">
                              {manifest?.kind || packageInfo?.entrypoint}
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[10px] text-text-muted">{worker.id}</div>
                        {worker.description && (
                          <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-text-secondary">
                            {worker.description}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          onClick={() => openScript(worker)}
                          disabled={!worker.script_path || openingId === worker.id}
                          className="h-7 rounded-md bg-[var(--worker-surface-soft)] px-2 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-45 disabled:cursor-not-allowed flex items-center gap-1.5"
                          title={worker.script_path || t.workers.openScript}
                        >
                          <FileCode2 className="h-3 w-3" />
                          {t.workers.openScript}
                        </button>
                        <button
                          onClick={() => restartWorker(worker.id)}
                          disabled={busyId === worker.id}
                          className="h-7 w-7 rounded-md bg-[var(--worker-surface-soft)] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-45 disabled:cursor-not-allowed flex items-center justify-center"
                          title={t.workers.restart}
                        >
                          <RotateCw className={`h-3.5 w-3.5 ${busyId === worker.id ? 'animate-spin' : ''}`} />
                        </button>
                        <button
                          onClick={() => pauseWorker(worker.id)}
                          disabled={!active || busyId === worker.id}
                          className="h-7 w-7 rounded-md bg-[var(--worker-surface-soft)] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-45 disabled:cursor-not-allowed flex items-center justify-center"
                          title={t.workers.pause}
                        >
                          <Pause className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => deleteWorker(worker.id)}
                          disabled={busyId === worker.id}
                          className="h-7 w-7 rounded-md bg-accent-danger/10 text-accent-danger hover:bg-accent-danger/15 disabled:opacity-45 disabled:cursor-not-allowed flex items-center justify-center"
                          title={t.workers.delete}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                      <MetricBox
                        icon={<Cpu className="h-3.5 w-3.5 text-accent-geo" />}
                        label={t.workers.cpu}
                        value={cpu == null ? t.workers.resourceUnavailable : `${cpu.toFixed(1)}%`}
                        percent={cpu == null ? 0 : Math.min(100, Math.max(0, cpu))}
                        history={history.map((sample) => sample.cpu)}
                        colorClass="text-accent-geo"
                        barClass="bg-accent-geo/75"
                      />
                      <MetricBox
                        icon={<HardDrive className="h-3.5 w-3.5 text-accent-success" />}
                        label={t.workers.memory}
                        value={memory == null ? t.workers.resourceUnavailable : `${memory.toFixed(1)} MB`}
                        percent={memory == null ? 0 : Math.min(100, Math.max(3, memory / 8))}
                        history={history.map((sample) => sample.memory)}
                        colorClass="text-accent-success"
                        barClass="bg-accent-success/75"
                      />
                      <MetricBox
                        icon={<Clock3 className="h-3.5 w-3.5" />}
                        label={t.workers.runtime}
                        value={resources?.elapsed || formatDuration(worker.started_at, worker.stopped_at)}
                      />
                    </div>

                    <div className="mt-3 grid grid-cols-1 gap-1.5 text-[10px] text-text-muted">
                      <PathRow label={t.workers.pid} value={worker.pid == null ? '-' : String(worker.pid)} mono />
                      <PathRow label={t.workers.entrypoint} value={packageInfo?.entrypoint || manifest?.entrypoint || 'main.py'} mono />
                      <PathRow label={t.workers.srcFiles} value={srcFiles.length > 0 ? `${srcFiles.length}` : '-'} mono />
                      <PathRow icon={<FolderOpen className="h-3.5 w-3.5" />} label={t.workers.folder} value={worker.folder || '-'} />
                      <PathRow icon={<FileCode2 className="h-3.5 w-3.5" />} label={t.workers.script} value={worker.script_path || '-'} />
                      {worker.last_error && (
                        <div className="rounded-lg bg-accent-danger/10 px-2.5 py-2 text-[11px] text-accent-danger whitespace-pre-wrap">
                          {worker.last_error}
                        </div>
                      )}
                    </div>
                  </div>

                  <button
                    onClick={() => toggleExpanded(worker.id)}
                    className="flex h-8 w-full items-center justify-between bg-[var(--worker-surface-muted)] px-3 text-[11px] text-text-secondary hover:bg-bg-hover/60 hover:text-text-primary"
                  >
                    <span>{t.workers.logs} ({Math.min(logs.length, LOG_PREVIEW_LIMIT)}/{logs.length})</span>
                    {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  </button>

                  {isExpanded && (
                    <div className="max-h-56 overflow-auto bg-[var(--worker-surface-muted)] p-2.5 scrollbar-thin">
                      {srcFiles.length > 0 && (
                        <div className="mb-2 rounded-lg bg-[var(--worker-surface-soft)] px-2.5 py-2">
                          <div className="mb-1.5 text-[10px] font-medium text-text-muted">{t.workers.servicePackage}</div>
                          <div className="flex flex-wrap gap-1.5">
                            {srcFiles.map((file) => (
                              <span key={file} className="rounded-md bg-bg-tertiary px-1.5 py-0.5 font-mono text-[10px] text-text-secondary">
                                {file}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {logs.length === 0 ? (
                        <div className="text-[11px] text-text-muted">{t.workers.noLogs}</div>
                      ) : (
                        logs.slice(-LOG_PREVIEW_LIMIT).map((log, index) => (
                          <div
                            key={`${log.ts}-${index}`}
                            className={`font-mono text-[10px] leading-relaxed whitespace-pre ${log.stream === 'stderr' ? 'text-accent-danger/90' : 'text-text-secondary'}`}
                          >
                            <span className="text-text-muted/60">[{formatTime(log.ts)} {log.stream}] </span>
                            {log.text}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </section>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function MetricBox({
  icon,
  label,
  value,
  percent,
  history,
  maxValue,
  colorClass = 'text-accent-primary',
  barClass = 'bg-accent-primary/75',
}: {
  icon: ReactNode
  label: string
  value: string
  percent?: number
  history?: Array<number | null>
  maxValue?: number
  colorClass?: string
  barClass?: string
}) {
  return (
    <div className="rounded-lg bg-[var(--worker-surface-soft)] p-2.5">
      <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-1.5 truncate text-[13px] font-semibold text-text-primary">{value}</div>
      {percent != null && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--worker-surface-muted)]">
          <div className={`h-full rounded-full transition-all duration-300 ${barClass}`} style={{ width: `${percent}%` }} />
        </div>
      )}
      {history && <Sparkline values={history} maxValue={maxValue} colorClass={colorClass} />}
    </div>
  )
}

function Sparkline({
  values,
  maxValue,
  colorClass = 'text-accent-primary',
}: {
  values: Array<number | null>
  maxValue?: number
  colorClass?: string
}) {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const observedMax = valid.length > 0 ? Math.max(...valid) : 0
  const scaleMax = maxValue ?? Math.max(1, observedMax * 1.25)
  const width = 96
  const height = 26
  const points = values.slice(-RESOURCE_HISTORY_LIMIT)
  const coordinates = points.map((value, index) => {
    const x = points.length <= 1 ? width : (index / (points.length - 1)) * width
      const numeric = typeof value === 'number' && Number.isFinite(value) ? value : 0
      const y = height - Math.max(0, Math.min(1, numeric / scaleMax)) * (height - 3) - 1.5
    return { x, y }
  })
  const linePath = coordinates
    .map(({ x, y }, index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ')
  const areaPath = coordinates.length > 1
    ? `${linePath} L${coordinates[coordinates.length - 1].x.toFixed(1)},${height - 1} L${coordinates[0].x.toFixed(1)},${height - 1} Z`
    : ''

  return (
    <svg className={`mt-2 h-[26px] w-full overflow-visible ${colorClass}`} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <path d={`M0,${height - 1} L${width},${height - 1}`} stroke="currentColor" strokeOpacity="0.12" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      {points.length > 1 && (
        <>
          <path d={areaPath} fill="currentColor" fillOpacity="0.14" />
          <path d={linePath} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </>
      )}
    </svg>
  )
}

function PathRow({
  icon,
  label,
  value,
  mono,
}: {
  icon?: ReactNode
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md bg-[var(--worker-surface-muted)] px-2.5 py-1.5">
      {icon}
      <span className="shrink-0 text-text-muted">{label}</span>
      <span className={`min-w-0 flex-1 truncate text-text-secondary ${mono ? 'font-mono' : ''}`} title={value}>
        {value}
      </span>
    </div>
  )
}

function statusClass(status: string): string {
  switch (status) {
    case 'running':
      return 'bg-accent-success animate-pulse'
    case 'starting':
      return 'bg-accent-warning animate-pulse'
    case 'failed':
      return 'bg-accent-danger'
    case 'paused':
      return 'bg-accent-warning'
    default:
      return 'bg-text-muted'
  }
}

function mergeWorkerLogs(previous: ResidentWorker[], incoming: ResidentWorker[]): ResidentWorker[] {
  const previousById = new Map(previous.map((worker) => [worker.id, worker]))
  return incoming.map((worker) => {
    const previousWorker = previousById.get(worker.id)
    if (!previousWorker || (worker.logs?.length ?? 0) > 0) return worker
    return { ...worker, logs: previousWorker.logs ?? [] }
  })
}

function formatTime(ts: number): string {
  if (!Number.isFinite(ts)) return '--:--:--'
  return new Date(ts * 1000).toLocaleTimeString()
}

function formatDuration(startedAt?: number | null, stoppedAt?: number | null): string {
  if (!startedAt) return '-'
  const end = stoppedAt || Date.now() / 1000
  const seconds = Math.max(0, Math.floor(end - startedAt))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function fileNameFromPath(filePath: string): string {
  const normalized = filePath.replace(/\\/g, '/')
  return normalized.split('/').pop() || 'worker.py'
}
