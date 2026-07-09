import type { ResidentWorker } from './types'

export const ACTIVE_WORKER_STATUSES = new Set(['starting', 'running'])
export const WORKER_LOG_PREVIEW_LIMIT = 50
export const WORKER_RESOURCE_HISTORY_LIMIT = 40

export function statusClass(status: string): string {
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

export function mergeWorkerLogs(
  previous: ResidentWorker[],
  incoming: ResidentWorker[],
): ResidentWorker[] {
  const previousById = new Map(previous.map((worker) => [worker.id, worker]))
  return incoming.map((worker) => {
    const previousWorker = previousById.get(worker.id)
    if (!previousWorker || (worker.logs?.length ?? 0) > 0) return worker
    return { ...worker, logs: previousWorker.logs ?? [] }
  })
}

export function formatTime(ts: number): string {
  if (!Number.isFinite(ts)) return '--:--:--'
  return new Date(ts * 1000).toLocaleTimeString()
}

export function formatDuration(startedAt?: number | null, stoppedAt?: number | null): string {
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

export function fileNameFromPath(filePath: string): string {
  const normalized = filePath.replace(/\\/g, '/')
  return normalized.split('/').pop() || 'main.py'
}
