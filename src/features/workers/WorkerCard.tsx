import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  FileCode2,
  FolderOpen,
  HardDrive,
  Pause,
  RotateCw,
  Trash2,
} from 'lucide-react'
import { useT } from '@/i18n'
import { WorkerLogs } from './WorkerLogs'
import { WorkerMetricBox } from './WorkerMetricBox'
import { WorkerPathRow } from './WorkerPathRow'
import type { ResidentWorker, ResourceSample } from './types'
import {
  ACTIVE_WORKER_STATUSES,
  WORKER_LOG_PREVIEW_LIMIT,
  formatDuration,
  statusClass,
} from './workerUtils'

export function WorkerCard({
  worker,
  isExpanded,
  busyId,
  openingId,
  resourceHistory,
  onToggleExpanded,
  onOpenScript,
  onRestart,
  onPause,
  onDelete,
}: {
  worker: ResidentWorker
  isExpanded: boolean
  busyId: string | null
  openingId: string | null
  resourceHistory: ResourceSample[]
  onToggleExpanded: (id: string) => void
  onOpenScript: (worker: ResidentWorker) => void
  onRestart: (id: string) => void
  onPause: (id: string) => void
  onDelete: (id: string) => void
}) {
  const t = useT()
  const active = ACTIVE_WORKER_STATUSES.has(worker.status)
  const logs = worker.logs ?? []
  const resources = worker.resources
  const cpu = typeof resources?.cpu_percent === 'number' ? resources.cpu_percent : null
  const memory = typeof resources?.rss_mb === 'number' ? resources.rss_mb : null
  const packageInfo = worker.package
  const manifest = worker.manifest
  const srcFiles = packageInfo?.src_files ?? []

  return (
    <section className="min-w-0 overflow-hidden rounded-xl bg-[var(--worker-surface)] shadow-sm shadow-black/10">
      <div className="p-3">
        <div className="flex items-start gap-2.5">
          <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${statusClass(worker.status)}`} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 truncate text-[14px] font-semibold text-text-primary">
                {worker.name || worker.id}
              </h3>
              <span className="rounded-md bg-bg-tertiary px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
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
              onClick={() => onOpenScript(worker)}
              disabled={!worker.script_path || openingId === worker.id}
              className="flex h-7 items-center gap-1.5 rounded-md bg-[var(--worker-surface-soft)] px-2 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:cursor-not-allowed disabled:opacity-45"
              title={worker.script_path || t.workers.openScript}
            >
              <FileCode2 className="h-3 w-3" />
              {t.workers.openScript}
            </button>
            <button
              onClick={() => onRestart(worker.id)}
              disabled={busyId === worker.id}
              className="flex h-7 w-7 items-center justify-center rounded-md bg-[var(--worker-surface-soft)] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:cursor-not-allowed disabled:opacity-45"
              title={t.workers.restart}
            >
              <RotateCw className={`h-3.5 w-3.5 ${busyId === worker.id ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => onPause(worker.id)}
              disabled={!active || busyId === worker.id}
              className="flex h-7 w-7 items-center justify-center rounded-md bg-[var(--worker-surface-soft)] text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:cursor-not-allowed disabled:opacity-45"
              title={t.workers.pause}
            >
              <Pause className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => onDelete(worker.id)}
              disabled={busyId === worker.id}
              className="flex h-7 w-7 items-center justify-center rounded-md bg-accent-danger/10 text-accent-danger hover:bg-accent-danger/15 disabled:cursor-not-allowed disabled:opacity-45"
              title={t.workers.delete}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <WorkerMetricBox
            icon={<Cpu className="h-3.5 w-3.5 text-accent-geo" />}
            label={t.workers.cpu}
            value={cpu == null ? t.workers.resourceUnavailable : `${cpu.toFixed(1)}%`}
            percent={cpu == null ? 0 : Math.min(100, Math.max(0, cpu))}
            history={resourceHistory.map((sample) => sample.cpu)}
            colorClass="text-accent-geo"
            barClass="bg-accent-geo/75"
          />
          <WorkerMetricBox
            icon={<HardDrive className="h-3.5 w-3.5 text-accent-success" />}
            label={t.workers.memory}
            value={memory == null ? t.workers.resourceUnavailable : `${memory.toFixed(1)} MB`}
            percent={memory == null ? 0 : Math.min(100, Math.max(3, memory / 8))}
            history={resourceHistory.map((sample) => sample.memory)}
            colorClass="text-accent-success"
            barClass="bg-accent-success/75"
          />
          <WorkerMetricBox
            icon={<Clock3 className="h-3.5 w-3.5" />}
            label={t.workers.runtime}
            value={resources?.elapsed || formatDuration(worker.started_at, worker.stopped_at)}
          />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-1.5 text-[10px] text-text-muted">
          <WorkerPathRow label={t.workers.pid} value={worker.pid == null ? '-' : String(worker.pid)} mono />
          <WorkerPathRow label={t.workers.entrypoint} value={packageInfo?.entrypoint || manifest?.entrypoint || 'main.py'} mono />
          <WorkerPathRow label={t.workers.srcFiles} value={srcFiles.length > 0 ? `${srcFiles.length}` : '-'} mono />
          <WorkerPathRow icon={<FolderOpen className="h-3.5 w-3.5" />} label={t.workers.folder} value={worker.folder || '-'} />
          <WorkerPathRow icon={<FileCode2 className="h-3.5 w-3.5" />} label={t.workers.script} value={worker.script_path || '-'} />
          {worker.last_error && (
            <div className="whitespace-pre-wrap rounded-lg bg-accent-danger/10 px-2.5 py-2 text-[11px] text-accent-danger">
              {worker.last_error}
            </div>
          )}
        </div>
      </div>

      <button
        onClick={() => onToggleExpanded(worker.id)}
        className="flex h-8 w-full items-center justify-between bg-[var(--worker-surface-muted)] px-3 text-[11px] text-text-secondary hover:bg-bg-hover/60 hover:text-text-primary"
      >
        <span>{t.workers.logs} ({Math.min(logs.length, WORKER_LOG_PREVIEW_LIMIT)}/{logs.length})</span>
        {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      </button>

      {isExpanded && (
        <WorkerLogs
          logs={logs}
          srcFiles={srcFiles}
          servicePackageLabel={t.workers.servicePackage}
          noLogsLabel={t.workers.noLogs}
        />
      )}
    </section>
  )
}
