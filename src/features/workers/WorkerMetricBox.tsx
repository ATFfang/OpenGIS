import type { ReactNode } from 'react'
import { ResourceSparkline } from './ResourceSparkline'

export function WorkerMetricBox({
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
      {history && <ResourceSparkline values={history} maxValue={maxValue} colorClass={colorClass} />}
    </div>
  )
}
