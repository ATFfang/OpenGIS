import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { pythonClient } from '@/services/pythonClient'
import { useT } from '@/i18n'
import { useAssetStore } from '@/stores/assetStore'
import { useViewStore } from '@/stores/viewStore'
import { WorkerCard } from './WorkerCard'
import type { ResidentWorker, ResourceSample } from './types'
import { ACTIVE_WORKER_STATUSES, WORKER_RESOURCE_HISTORY_LIMIT, fileNameFromPath, mergeWorkerLogs } from './workerUtils'

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
    () => workers.filter((worker) => ACTIVE_WORKER_STATUSES.has(worker.status)).length,
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
          ? [...current, { ts: sampledAt, cpu, memory }].slice(-WORKER_RESOURCE_HISTORY_LIMIT)
          : current
      }
      for (const id of Object.keys(next)) {
        if (!liveIds.has(id)) delete next[id]
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
            {workers.map((worker) => (
              <WorkerCard
                key={worker.id}
                worker={worker}
                isExpanded={expanded.has(worker.id)}
                busyId={busyId}
                openingId={openingId}
                resourceHistory={resourceHistory[worker.id] ?? []}
                onToggleExpanded={toggleExpanded}
                onOpenScript={(target) => { openScript(target).catch(() => {}) }}
                onRestart={(id) => { restartWorker(id).catch(() => {}) }}
                onPause={(id) => { pauseWorker(id).catch(() => {}) }}
                onDelete={(id) => { deleteWorker(id).catch(() => {}) }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
