import { WORKER_LOG_PREVIEW_LIMIT, formatTime } from './workerUtils'
import type { WorkerLog } from './types'

export function WorkerLogs({
  logs,
  srcFiles,
  servicePackageLabel,
  noLogsLabel,
}: {
  logs: WorkerLog[]
  srcFiles: string[]
  servicePackageLabel: string
  noLogsLabel: string
}) {
  return (
    <div className="max-h-56 overflow-auto bg-[var(--worker-surface-muted)] p-2.5 scrollbar-thin">
      {srcFiles.length > 0 && (
        <div className="mb-2 rounded-lg bg-[var(--worker-surface-soft)] px-2.5 py-2">
          <div className="mb-1.5 text-[10px] font-medium text-text-muted">{servicePackageLabel}</div>
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
        <div className="text-[11px] text-text-muted">{noLogsLabel}</div>
      ) : (
        logs.slice(-WORKER_LOG_PREVIEW_LIMIT).map((log, index) => (
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
  )
}
