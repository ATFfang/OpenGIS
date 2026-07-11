import type { ReactNode } from 'react'

export function WorkerPathRow({
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
