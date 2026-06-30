import { memo, useMemo } from 'react'
import {
  ListChecks,
  Check,
  Circle,
  Loader2,
  MinusCircle,
  XCircle,
} from 'lucide-react'
import type { PlanData, PlanStep, PlanStepStatus } from '@/types/chat'
import { useT } from '@/i18n'

/**
 * PlanRow — renders a TODO / plan checklist emitted by the backend
 * `update_plan` skill (say='plan'). The card is upserted in place by
 * plan_id, so as the agent marks steps done the same card animates.
 */
const PlanRow = memo(({ planData }: { planData?: PlanData }) => {
  const t = useT()

  const steps = planData?.steps ?? []
  const { done, total, active } = useMemo(() => {
    const total = steps.length
    const done = steps.filter((s) => s.status === 'done').length
    const skipped = steps.filter((s) => s.status === 'skipped').length
    const active = steps.find((s) => s.status === 'in_progress')
    // Treat skipped as resolved for the progress ratio so a plan with
    // skipped steps can still reach 100%.
    return { done: done + skipped, total, active }
  }, [steps])

  if (!planData || total === 0) return <div className="h-px" aria-hidden />

  const allDone = done >= total
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div className="w-full min-w-0 bg-bg-tertiary/40 border border-border/60 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-border/50">
        <ListChecks className="w-4 h-4 shrink-0 text-accent-primary" />
        <span className="text-[13px] font-semibold text-text-primary truncate">
          {planData.title?.trim() || t.chat.plan.title}
        </span>
        <span className="ml-auto shrink-0 text-[11px] font-mono text-text-muted tabular-nums">
          {done}/{total}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-1 w-full bg-border/40">
        <div
          className={`h-full transition-all duration-500 ${
            allDone ? 'bg-accent-success' : 'bg-accent-primary'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Steps */}
      <ul className="px-2 py-1.5">
        {steps.map((step) => (
          <PlanStepRow
            key={step.id}
            step={step}
            isActive={active?.id === step.id}
          />
        ))}
      </ul>
    </div>
  )
})

PlanRow.displayName = 'PlanRow'
export default PlanRow

// --- Per-step row ---

function PlanStepRow({ step, isActive }: { step: PlanStep; isActive: boolean }) {
  const t = useT()
  return (
    <li
      className={`flex items-start gap-2.5 px-2 py-1.5 rounded-lg ${
        isActive ? 'bg-accent-primary/5' : ''
      }`}
    >
      <span className="mt-0.5 shrink-0">
        <StatusIcon status={step.status} />
      </span>
      <div className="flex-1 min-w-0">
        <div
          className={`text-[13px] leading-snug ${textClassFor(step.status)}`}
        >
          {step.title}
        </div>
        {step.note && (
          <div className="text-[11px] text-text-muted mt-0.5 leading-snug">
            {step.note}
          </div>
        )}
      </div>
      {step.status === 'in_progress' && (
        <span className="ml-1 shrink-0 self-center text-[10px] uppercase tracking-wider font-medium text-accent-primary">
          {t.chat.plan.inProgress}
        </span>
      )}
    </li>
  )
}

function StatusIcon({ status }: { status: PlanStepStatus }) {
  switch (status) {
    case 'done':
      return (
        <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-accent-success/15">
          <Check className="w-3 h-3 text-accent-success" />
        </span>
      )
    case 'in_progress':
      return <Loader2 className="w-4 h-4 text-accent-primary animate-spin" />
    case 'skipped':
      return <MinusCircle className="w-4 h-4 text-text-muted" />
    case 'failed':
      return <XCircle className="w-4 h-4 text-accent-danger" />
    case 'pending':
    default:
      return <Circle className="w-4 h-4 text-text-muted/50" />
  }
}

function textClassFor(status: PlanStepStatus): string {
  switch (status) {
    case 'done':
      return 'text-text-muted line-through'
    case 'skipped':
      return 'text-text-muted/70 line-through'
    case 'failed':
      return 'text-accent-danger'
    case 'in_progress':
      return 'text-text-primary font-medium'
    case 'pending':
    default:
      return 'text-text-secondary'
  }
}
