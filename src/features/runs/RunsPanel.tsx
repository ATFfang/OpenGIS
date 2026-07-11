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
  Hammer,
  Boxes,
  GitBranch,
  Play,
  X,
} from 'lucide-react'
import { useT } from '@/i18n'
import { useAssetStore } from '@/stores/assetStore'
import {
  useRunsStore,
  type RunSummary,
  type RunDetail,
  type RunStep,
  type AgentQueueItem,
  type AgentWorkStatus,
  type PermissionRule,
} from '@/stores/runsStore'
import { useDialog } from '@/components/Dialog'
import { useViewStore } from '@/stores/viewStore'

// ─── Main panel ───────────────────────────────────────────────────

export function RunsPanel() {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const runs = useRunsStore((s) => s.runs)
  const isLoading = useRunsStore((s) => s.isLoading)
  const loaded = useRunsStore((s) => s.loaded)
  const error = useRunsStore((s) => s.error)
  const refresh = useRunsStore((s) => s.refresh)
  const refreshControlPlane = useRunsStore((s) => s.refreshControlPlane)
  const queueItems = useRunsStore((s) => s.queueItems)
  const permissionRules = useRunsStore((s) => s.permissionRules)
  const controlLoading = useRunsStore((s) => s.controlLoading)

  // First mount: populate the list. Re-fetch on workspace change —
  // each workspace has its own .opengis/runs/ directory.
  useEffect(() => {
    refresh().catch(() => { /* error lives in store.error */ })
    refreshControlPlane().catch(() => { /* best effort */ })
  }, [workspacePath, refresh, refreshControlPlane])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-1">
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          {t.runs.title}
        </span>
        <button
          onClick={() => refresh()}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
          title={t.runs.refreshHistory}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <ControlPlaneSummary
          queueItems={queueItems}
          permissionRules={permissionRules}
          loading={controlLoading}
        />
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

function ControlPlaneSummary({
  queueItems,
  permissionRules,
  loading,
}: {
  queueItems: AgentQueueItem[]
  permissionRules: PermissionRule[]
  loading: boolean
}) {
  const t = useT()
  const processQueue = useRunsStore((s) => s.processQueue)
  const retryQueueItem = useRunsStore((s) => s.retryQueueItem)
  const cancelQueueItem = useRunsStore((s) => s.cancelQueueItem)
  const removePermissionRule = useRunsStore((s) => s.removePermissionRule)
  const queued = queueItems.filter((item) => item.status === 'queued')
  const active = queueItems.filter((item) => item.status === 'running')
  const retryable = queueItems.filter((item) => item.status === 'error' || item.status === 'cancelled')
  const visibleQueue = [...active, ...queued, ...retryable].slice(0, 3)

  return (
    <div className="border-b border-border/70 px-2 py-2 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <ListRestart className="w-3.5 h-3.5 text-text-muted" />
        <span className="text-2xs font-semibold text-text-muted uppercase tracking-wider flex-1">
          {t.runs.agentControl}
        </span>
        <button
          onClick={() => processQueue().catch(() => {})}
          disabled={loading || queued.length === 0 || active.length > 0}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-text-muted"
          title={t.runs.processQueue}
        >
          {loading ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
        </button>
      </div>

      {visibleQueue.length > 0 ? (
        <div className="space-y-1">
          {visibleQueue.map((item) => (
            <div key={item.id} className="flex items-center gap-1.5 text-2xs">
              <StatusDot status={item.status} />
              <span className="font-mono text-text-muted/80 truncate flex-1" title={item.error || item.id}>
                {item.status} · {(item.inbox_id || item.id).slice(0, 8)}
              </span>
              {(item.status === 'error' || item.status === 'cancelled') && (
                <button
                  onClick={() => retryQueueItem(item.id).catch(() => {})}
                  className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10"
                  title={t.runs.retryQueue}
                >
                  <RotateCcw className="w-3 h-3" />
                </button>
              )}
              {item.status === 'queued' && (
                <button
                  onClick={() => cancelQueueItem(item.id).catch(() => {})}
                  className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10"
                  title={t.runs.cancelQueue}
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-2xs text-text-muted/60">{t.runs.noQueuedItems}</div>
      )}

      {permissionRules.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-border/50">
          <div className="text-2xs text-text-muted/70">
            {t.runs.permissionRules} ({permissionRules.length})
          </div>
          {permissionRules.slice(0, 3).map((rule) => (
            <div key={rule.id} className="flex items-center gap-1.5 text-2xs">
              <span className="font-mono text-text-muted/80 truncate flex-1">
                {rule.action}:{rule.tool}
              </span>
              <button
                onClick={() => removePermissionRule(rule.id).catch(() => {})}
                className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10"
                title={t.runs.removeRule}
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Row ──────────────────────────────────────────────────────────

function RunRow({ run }: { run: RunSummary }) {
  const t = useT()
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
      title: t.runs.revertTitle,
      message:
        t.runs.revertMessage,
      okLabel: t.runs.revertButton,
      danger: true,
    })
    if (!ok) return

    setBusy('revert')
    try {
      await revertRun(run.run_id)
      await alert({
        title: t.runs.workspaceReverted,
        message: t.runs.workspaceRevertedMsg.replace('{runId}', run.run_id.slice(0, 8)),
      })
    } catch (err: any) {
      await alert({
        title: t.runs.revertFailed,
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
        title: t.runs.replayFailed,
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
            {run.prompt || <span className="text-text-muted/60 italic">{t.runs.emptyPrompt}</span>}
          </div>
        </div>

        {/* Inline actions — show on hover */}
        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleReplay}
            disabled={!!busy}
            className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors disabled:opacity-40"
            title={t.runs.replayTitle}
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
              ? t.runs.revertTooltip
              : t.runs.noSnapshotTooltip}
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
                {t.runs.loadingDetail}
              </div>
            ) : detail ? (
              <RunDetailView detail={detail} />
            ) : (
              <div className="py-2 text-2xs text-text-muted/70">
                {t.runs.noDetail}
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
  const t = useT()
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
        {detail.session?.profile_name && (
          <span className="mr-2">profile={detail.session.profile_name}</span>
        )}
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

      {/* Session */}
      {detail.session && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider flex items-center gap-1">
            <GitBranch className="w-3 h-3" />
            {t.runs.session}
          </div>
          <div className="text-2xs text-text-muted/80 font-mono break-all">
            {detail.session.kind}/{detail.session.status || detail.status}
            {detail.session.children && detail.session.children.length > 0 && (
              <span className="ml-2">children={detail.session.children.length}</span>
            )}
          </div>
        </div>
      )}

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
            {t.runs.steps} ({detail.steps.length})
          </div>
          {detail.steps.map((step, i) => (
            <StepRow key={i} step={step} onOpenFile={openFile} />
          ))}
        </div>
      )}

      {/* Tool calls */}
      {detail.tool_calls && detail.tool_calls.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider flex items-center gap-1">
            <Hammer className="w-3 h-3" />
            {t.runs.toolCalls} ({detail.tool_calls.length})
          </div>
          {detail.tool_calls.slice(0, 8).map((call) => (
            <div key={call.call_id} className="text-2xs text-text-muted/80 font-mono break-all">
              <span className={call.error ? 'text-accent-danger' : 'text-accent-primary'}>
                {call.name}
              </span>
              {typeof call.duration_ms === 'number' && (
                <span className="ml-1.5 text-text-muted/60">{Math.round(call.duration_ms)}ms</span>
              )}
              {call.metadata?.permission != null && (
                <span className="ml-1.5 text-text-muted/60">perm={String(call.metadata.permission)}</span>
              )}
            </div>
          ))}
          {detail.tool_calls.length > 8 && (
            <div className="text-2xs text-text-muted/60 italic">
              +{detail.tool_calls.length - 8} {t.runs.more}
            </div>
          )}
        </div>
      )}

      {/* Artifacts */}
      {detail.artifacts && detail.artifacts.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider flex items-center gap-1">
            <Boxes className="w-3 h-3" />
            {t.runs.artifacts} ({detail.artifacts.length})
          </div>
          {detail.artifacts.slice(0, 8).map((artifact, i) => (
            <div key={artifact.id || i} className="text-2xs text-text-muted/80 font-mono break-all">
              <span className="text-accent-success">{artifact.kind}</span>
              <span className="ml-1.5">
                {artifact.title || artifact.path || artifact.layer_id || artifact.id}
              </span>
            </div>
          ))}
          {detail.artifacts.length > 8 && (
            <div className="text-2xs text-text-muted/60 italic">
              +{detail.artifacts.length - 8} {t.runs.more}
            </div>
          )}
        </div>
      )}

      {/* Final answer */}
      {detail.final_answer && (
        <div className="space-y-0.5">
          <div className="text-2xs font-semibold text-text-muted uppercase tracking-wider">
            {t.runs.finalAnswer}
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
            {t.runs.riskyOps} ({detail.risky_ops.length})
          </div>
          {detail.risky_ops.slice(0, 10).map((op, i) => (
            <div key={i} className="text-2xs text-text-muted/80 font-mono break-all">
              <span className="text-accent-warning">{op.op}</span>
              {op.path && <span className="ml-1.5">{op.path}</span>}
            </div>
          ))}
          {detail.risky_ops.length > 10 && (
            <div className="text-2xs text-text-muted/60 italic">
              +{detail.risky_ops.length - 10} {t.runs.more}
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
  const t = useT()
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
            title={t.runs.openScript}
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

function StatusDot({ status }: { status: RunSummary['status'] | AgentWorkStatus }) {
  const color =
    status === 'completed' || status === 'success' ? 'bg-accent-success' :
    status === 'error' ? 'bg-accent-danger' :
    status === 'running' ? 'bg-accent-warning animate-pulse-soft' :
    status === 'queued' ? 'bg-accent-primary' :
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
  const t = useT()
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
          {hasWorkspace ? t.runs.noRuns : t.runs.noWorkspace}
        </p>
        <p className="text-2xs text-text-muted/60 max-w-[180px]">
          {hasWorkspace
            ? t.runs.noRunsHint
            : t.runs.noWorkspaceHint}
        </p>
      </div>
    </div>
  )
}

function LoadingState() {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <RefreshCw className="w-5 h-5 text-text-muted animate-spin mx-auto mb-2" />
        <p className="text-xs text-text-muted">{t.runs.loadingRuns}</p>
      </div>
    </div>
  )
}

function ErrorRow({ message, onRetry }: { message: string; onRetry: () => void }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-4 h-full">
      <div className="text-center">
        <div className="w-10 h-10 rounded-xl bg-accent-danger/10 flex items-center justify-center mx-auto mb-3">
          <AlertCircle className="w-5 h-5 text-accent-danger/50" />
        </div>
        <p className="text-xs text-text-muted mb-1">{t.runs.failedToLoad}</p>
        <p className="text-2xs text-text-muted/60 mb-2 max-w-[180px] truncate">
          {message}
        </p>
        <button
          onClick={onRetry}
          className="text-2xs text-accent-primary hover:text-accent-primary/80 transition-colors"
        >
          {t.common.retry}
        </button>
      </div>
    </div>
  )
}
