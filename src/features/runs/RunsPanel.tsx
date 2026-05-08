/**
 * RunsPanel — sidebar content panel for the Runs tab.
 *
 * Lists all agent runs recorded under `<workspace>/.opengis/runs/`
 * (fallback: `<appData>/opengis/agent-runs/` when no workspace is
 * open). Each row shows time / status / prompt preview plus two
 * inline actions:
 *
 *   Undo2   — revert the workspace to the run's pre_sha (git reset --hard)
 *   RotateCcw — replay: send the same prompt again (new run)
 *
 * Clicking a row expands an inline detail view showing the recorded
 * steps. The panel mirrors the visual language of WorkflowsPanel so
 * the sidebar feels cohesive: 36px header, compact row entries,
 * hover highlight.
 *
 * Design notes:
 * - No context menu. Revert is destructive, so it's a dedicated
 *   button with a dialog confirmation (danger=true).
 * - Replay is non-destructive; a single click fires it, but we still
 *   toast a subtle info note so the user knows a new run started in
 *   the Chat panel.
 * - We don't subscribe to chat events here — RunsPanel is a passive
 *   archive browser, not a live stream.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  RefreshCw,
  RotateCcw,
  Undo2,
  AlertCircle,
  FolderPlus,
  ListRestart,
  ChevronDown,
  ChevronRight,
  FileCode,
} from 'lucide-react'
import { useAssetStore } from '@/stores/assetStore'
import {
  useRunsStore,
  type RunSummary,
  type RunDetail,
  type RunStep,
} from '@/stores/runsStore'
import { useDialog } from '@/components/Dialog'
import { useViewStore } from '@/stores/viewStore'

// ─── Main panel ───────────────────────────────────────────────────

export function RunsPanel() {
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const runs = useRunsStore((s) => s.runs)
  const isLoading = useRunsStore((s) => s.isLoading)
  const loaded = useRunsStore((s) => s.loaded)
  const error = useRunsStore((s) => s.error)
  const refresh = useRunsStore((s) => s.refresh)

  // First mount: populate the list. Re-fetch on workspace change —
  // each workspace has its own .opengis/runs/ directory.
  useEffect(() => {
    refresh().catch(() => { /* error lives in store.error */ })
  }, [workspacePath, refresh])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-1">
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          Runs
        </span>
        <button
          onClick={() => refresh()}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title="Refresh run history"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {error ? (
          <ErrorRow message={error} onRetry={() => refresh()} />
        ) : runs.length === 0 ? (
          isLoading && !loaded ? <LoadingState /> : <EmptyState hasWorkspace={!!workspacePath} />
        ) : (
          <div className="py-1">
            {runs.map((run) => (
              <RunRow key={run.run_id} run={run} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Row ──────────────────────────────────────────────────────────

function RunRow({ run }: { run: RunSummary }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [busy, setBusy] = useState<null | 'revert' | 'replay'>(null)

  const getDetail = useRunsStore((s) => s.getDetail)
  const revertRun = useRunsStore((s) => s.revertRun)
  const replayRun = useRunsStore((s) => s.replayRun)
  const { confirm, alert } = useDialog()

  const handleToggleExpand = useCallback(async () => {
    const next = !expanded
    setExpanded(next)
    if (next && !detail) {
      setLoadingDetail(true)
      try {
        const d = await getDetail(run.run_id)
        setDetail(d)
      } finally {
        setLoadingDetail(false)
      }
    }
  }, [expanded, detail, getDetail, run.run_id])

  const handleRevert = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (busy) return

    const ok = await confirm({
      title: 'Revert this run?',
      message:
        'This will reset the workspace to the git commit recorded ' +
        'before the run started. Any file changes made during or ' +
        'after the run will be lost.',
      okLabel: 'Revert',
      danger: true,
    })
    if (!ok) return

    setBusy('revert')
    try {
      await revertRun(run.run_id)
      await alert({
        title: 'Workspace reverted',
        message: `The workspace has been reset to the state before run ${run.run_id.slice(0, 8)}.`,
      })
    } catch (err: any) {
      await alert({
        title: 'Revert failed',
        message: err?.message || String(err),
      })
    } finally {
      setBusy(null)
    }
  }, [busy, confirm, alert, revertRun, run.run_id])

  const handleReplay = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (busy) return

    setBusy('replay')
    try {
      await replayRun(run.run_id)
      // Replay triggers a new agent.run on the backend which streams
      // into the Chat panel — no toast needed, the user will see the
      // new messages arriving.
    } catch (err: any) {
      await alert({
        title: 'Replay failed',
        message: err?.message || String(err),
      })
    } finally {
      setBusy(null)
    }
  }, [busy, replayRun, alert, run.run_id])

  return (
    <>
      <div
        onClick={handleToggleExpand}
        className="group flex items-start gap-1.5 px-2 py-2 cursor-pointer transition-colors hover:bg-bg-hover"
        title={`run_id: ${run.run_id}`}
      >
        {/* Expand chevron */}
        <span className="pt-0.5 text-text-muted shrink-0">
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
        </span>

        {/* Main content column */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <StatusDot status={run.status} />
            <span className="text-2xs text-text-muted shrink-0">
              {formatTime(run.created_at)}
            </span>
          </div>
          <div className="text-xs text-text-secondary truncate mt-0.5 leading-snug">
            {run.prompt || <span className="text-text-muted/60 italic">(empty prompt)</span>}
          </div>
        </div>

        {/* Inline actions — show on hover */}
        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleReplay}
            disabled={!!busy}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors disabled:opacity-40"
            title="Replay this run with the same prompt"
          >
            {busy === 'replay' ? (
              <RefreshCw className="w-3 h-3 animate-spin" />
            ) : (
              <RotateCcw className="w-3 h-3" />
            )}
          </button>
          <button
            onClick={handleRevert}
            disabled={!!busy || !run.pre_sha}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-text-muted"
            title={run.pre_sha
              ? 'Revert workspace to the commit before this run'
              : 'No pre-run snapshot recorded for this run'}
          >
            {busy === 'revert' ? (
              <RefreshCw className="w-3 h-3 animate-spin" />
            ) : (
              <Undo2 className="w-3 h-3" />
            )}
          </button>
        </div>
      </div>

      {/* Inline detail section */}
      {expanded && (
        <div className="px-2 pb-2 -mt-1">
          <div className="ml-4 pl-2 border-l border-border/60">
            {loadingDetail ? (
              <div className="py-2 text-2xs text-text-muted flex items-center gap-1.5">
                <RefreshCw className="w-3 h-3 animate-spin" />
                Loading run detail...
              </div>
            ) : detail ? (
              <RunDetailView detail={detail} />
            ) : (
              <div className="py-2 text-2xs text-text-muted/70">
                (no detail available)
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

// ─── Detail view ──────────────────────────────────────────────────

function RunDetailView({ detail }: { detail: RunDetail }) {
  const openFile = useCallback((absPath: string) => {
    const view = useViewStore.getState()
    // Reuse existing tab if already open.
    const existing = view.tabs.find((t) => t.filePath === absPath)
    if (existing) {
      view.setActiveTab(existing.id)
      return
    }
    const name = absPath.split(/[\\/]/).pop() || absPath
    view.openTab({
      title: name,
      type: 'code',
      filePath: absPath,
      language: name.endsWith('.py') ? 'python' : undefined,
    })
  }, [])

  return (
    <div className="py-1.5 space-y-1.5">
      {/* Meta line */}
      <div className="text-2xs text-text-muted/80 font-mono break-all">
        {detail.model && <span className="mr-2">model={detail.model}</span>}
        {detail.pre_sha && (
          <span className="mr-2" title="git SHA before run">
            pre={detail.pre_sha.slice(0, 7)}
          </span>
        )}
        {detail.post_sha && (
          <span title="git SHA after run">
            post={detail.post_sha.slice(0, 7)}
          </span>
        )}
      </div>

      {/* Error box */}
      {detail.error && (
        <div className="text-2xs text-accent-danger/90 bg-accent-danger/5 rounded px-2 py-1 whitespace-pre-wrap break-words">
          {detail.error}
        </div>
      )}

      {/* Steps */}
      {detail.steps && detail.steps.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider">
            Steps ({detail.steps.length})
          </div>
          {detail.steps.map((step, i) => (
            <StepRow key={i} step={step} onOpenFile={openFile} />
          ))}
        </div>
      )}

      {/* Final answer */}
      {detail.final_answer && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider">
            Final answer
          </div>
          <div className="text-2xs text-text-secondary whitespace-pre-wrap break-words bg-bg-secondary/60 rounded px-2 py-1 max-h-40 overflow-y-auto scrollbar-thin">
            {detail.final_answer}
          </div>
        </div>
      )}

      {/* Risky ops summary */}
      {detail.risky_ops && detail.risky_ops.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider">
            Risky ops ({detail.risky_ops.length})
          </div>
          {detail.risky_ops.slice(0, 10).map((op, i) => (
            <div key={i} className="text-2xs text-text-muted/80 font-mono break-all">
              <span className="text-accent-warning">{op.op}</span>
              {op.path && <span className="ml-1.5">{op.path}</span>}
            </div>
          ))}
          {detail.risky_ops.length > 10 && (
            <div className="text-2xs text-text-muted/60 italic">
              +{detail.risky_ops.length - 10} more
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StepRow({
  step,
  onOpenFile,
}: {
  step: RunStep
  onOpenFile: (path: string) => void
}) {
  const hasScript = !!step.script_path
  const hasError = !!step.error

  return (
    <div className="flex items-start gap-1.5 text-2xs leading-snug">
      <span className="text-text-muted/60 w-4 text-right shrink-0">
        {step.step ?? '·'}
      </span>
      <div className="flex-1 min-w-0">
        {hasScript && (
          <button
            onClick={() => onOpenFile(step.script_path as string)}
            className="inline-flex items-center gap-1 text-accent-primary hover:underline font-mono break-all text-left"
            title="Open script in a new tab"
          >
            <FileCode className="w-3 h-3 shrink-0" />
            <span className="truncate">
              {(step.script_path as string).split(/[\\/]/).pop()}
            </span>
          </button>
        )}
        {!hasScript && step.kind && (
          <span className="text-text-muted/80">{step.kind}</span>
        )}
        {hasError && (
          <div className="text-accent-danger/90 whitespace-pre-wrap break-words mt-0.5">
            {step.error}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Small building blocks ────────────────────────────────────────

function StatusDot({ status }: { status: RunSummary['status'] }) {
  const color =
    status === 'completed' ? 'bg-accent-success' :
    status === 'error' ? 'bg-accent-danger' :
    status === 'running' ? 'bg-accent-warning animate-pulse-soft' :
    status === 'cancelled' ? 'bg-text-muted' :
    'bg-text-muted/50'
  return (
    <span
      className={`w-1.5 h-1.5 rounded-full shrink-0 ${color}`}
      title={status}
    />
  )
}

function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleString([], {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

// ─── Empty / loading / error ──────────────────────────────────────

function EmptyState({ hasWorkspace }: { hasWorkspace: boolean }) {
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-primary/10 flex items-center justify-center mx-auto mb-3">
          {hasWorkspace ? (
            <ListRestart className="w-5 h-5 text-accent-primary/60" />
          ) : (
            <FolderPlus className="w-5 h-5 text-accent-primary/50" />
          )}
        </div>
        <p className="text-xs text-text-muted mb-1">
          {hasWorkspace ? 'No runs yet' : 'No workspace folder'}
        </p>
        <p className="text-2xs text-text-muted/60 max-w-[180px]">
          {hasWorkspace
            ? 'Your agent runs will appear here after the first chat message.'
            : 'Open a workspace to keep per-project run history.'}
        </p>
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <RefreshCw className="w-5 h-5 text-text-muted animate-spin mx-auto mb-2" />
        <p className="text-xs text-text-muted">Loading runs...</p>
      </div>
    </div>
  )
}

function ErrorRow({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-danger/10 flex items-center justify-center mx-auto mb-3">
          <AlertCircle className="w-5 h-5 text-accent-danger/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">Failed to load</p>
        <p className="text-2xs text-text-muted/60 mb-2 max-w-[180px] truncate">
          {message}
        </p>
        <button
          onClick={onRetry}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors"
        >
          Retry
        </button>
      </div>
    </div>
  )
}
