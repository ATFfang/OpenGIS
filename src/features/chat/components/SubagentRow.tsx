import { memo, useEffect, useState } from 'react'
import { Bot, Check, Loader2, X, Users, ChevronDown, ChevronRight } from 'lucide-react'
import type { SubagentData, SubagentTask } from '@/types/chat'

interface SubagentRowProps {
  data?: SubagentData
}

/**
 * SubagentRow — opencode-style "a sub-agent is running" card.
 *
 * Design intent: we surface ONLY the delegation status (task titles + their
 * running/done/failed state), never the child agent's internal reasoning,
 * code or output. Context isolation is the whole point of a sub-agent, so
 * its mess stays out of the main chat — we just show that work is happening.
 *
 * - run_subagent  → one task (a "context firewall").
 * - run_subagents → a parallel fan-out; tasks light up one-by-one as the
 *   backend streams incremental `subagent_update`s.
 */
export const SubagentRow = memo(({ data }: SubagentRowProps) => {
  const [expanded, setExpanded] = useState(true)

  if (!data) return <div className="h-px" aria-hidden />

  const running = data.status === 'running'
  const tasks = data.tasks ?? []
  const total = data.total ?? tasks.length
  const okCount = data.okCount ?? tasks.filter((t) => t.status === 'done').length
  const failedCount = tasks.filter((t) => t.status === 'failed').length
  const parallel = data.parallel

  const elapsed = useElapsed(data.startedAt, running, data.updatedAt)

  // Collapse automatically once the whole delegation finishes — the result
  // summary is already streamed into the following assistant text.
  useEffect(() => {
    if (!running) {
      const t = window.setTimeout(() => setExpanded(false), 600)
      return () => window.clearTimeout(t)
    }
  }, [running])

  const title = parallel ? 'Sub-agents' : 'Sub-agent'

  return (
    <div className="rounded-xl border border-border/60 bg-bg-tertiary/30 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-bg-hover/40 transition-colors"
      >
        <div
          className={`w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
            running ? 'bg-accent-primary/10' : failedCount > 0 ? 'bg-accent-danger/10' : 'bg-accent-success/10'
          }`}
        >
          {parallel ? (
            <Users className={`w-3 h-3 ${running ? 'text-accent-primary' : 'text-text-secondary'}`} />
          ) : (
            <Bot className={`w-3 h-3 ${running ? 'text-accent-primary' : 'text-text-secondary'}`} />
          )}
        </div>

        <span className="text-[13px] font-semibold text-text-primary">{title}</span>

        {running ? (
          <span className="flex items-center gap-1.5 text-[11px] text-accent-primary">
            <Loader2 className="w-3 h-3 animate-spin" />
            <span>Running{parallel && total > 1 ? ` ${total} tasks` : ''}…</span>
          </span>
        ) : (
          <span
            className={`text-[11px] ${failedCount > 0 ? 'text-accent-danger' : 'text-accent-success'}`}
          >
            {okCount}/{total} done
          </span>
        )}

        {elapsed && (
          <span className="text-[10px] text-text-muted/60 font-mono">{elapsed}</span>
        )}

        <span className="flex-1" />
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-text-muted shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-text-muted shrink-0" />
        )}
      </button>

      {/* Task list */}
      {expanded && tasks.length > 0 && (
        <div className="border-t border-border/40 px-3 py-2 space-y-1.5">
          {tasks.map((task, i) => (
            <TaskRow key={i} task={task} />
          ))}
        </div>
      )}
    </div>
  )
})

SubagentRow.displayName = 'SubagentRow'

function TaskRow({ task }: { task: SubagentTask }) {
  return (
    <div className="flex items-start gap-2 text-[12px]">
      <span className="mt-0.5 shrink-0">
        {task.status === 'running' ? (
          <Loader2 className="w-3 h-3 text-accent-primary animate-spin" />
        ) : task.status === 'failed' ? (
          <X className="w-3 h-3 text-accent-danger" />
        ) : (
          <Check className="w-3 h-3 text-accent-success" />
        )}
      </span>
      <span
        className={`leading-snug min-w-0 break-words ${
          task.status === 'running'
            ? 'text-text-primary'
            : task.status === 'failed'
              ? 'text-text-secondary line-through decoration-accent-danger/40'
              : 'text-text-secondary'
        }`}
      >
        {task.title || '(task)'}
      </span>
    </div>
  )
}

/** Live "Ns" / "Nm Ss" elapsed chip while running; frozen final value once done. */
function useElapsed(startedAt: number | undefined, running: boolean, updatedAt?: number): string {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [running])

  if (!startedAt) return ''
  const end = running ? now : (updatedAt ?? now)
  const secs = Math.max(0, Math.round((end - startedAt) / 1000))
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}m ${s}s`
}

export default SubagentRow
