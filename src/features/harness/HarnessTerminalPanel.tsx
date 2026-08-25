import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CircleStop,
  ListRestart,
  ExternalLink,
  Play,
  RefreshCw,
  Terminal,
} from 'lucide-react'
import { useT } from '@/i18n'
import { pythonClient } from '@/services/pythonClient'
import { useAssetStore } from '@/stores/assetStore'
import {
  useRunsStore,
  type AgentQueueItem,
  type RunDetail,
  type RunSummary,
} from '@/stores/runsStore'

export function HarnessTerminalPanel({ onOpenRuns }: { onOpenRuns?: () => void }) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const runs = useRunsStore((s) => s.runs)
  const queueItems = useRunsStore((s) => s.queueItems)
  const refresh = useRunsStore((s) => s.refresh)
  const refreshControlPlane = useRunsStore((s) => s.refreshControlPlane)
  const processQueue = useRunsStore((s) => s.processQueue)
  const cancelQueueItem = useRunsStore((s) => s.cancelQueueItem)
  const getDetail = useRunsStore((s) => s.getDetail)
  const controlLoading = useRunsStore((s) => s.controlLoading)

  const [prompt, setPrompt] = useState('')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedDetail, setSelectedDetail] = useState<RunDetail | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const activeQueue = useMemo(
    () => queueItems.find((item) => item.status === 'running' || item.status === 'queued'),
    [queueItems],
  )
  const latestRun = runs[0]
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) || latestRun

  useEffect(() => {
    refresh(20).catch(() => {})
    refreshControlPlane().catch(() => {})
    const timer = window.setInterval(() => {
      refresh(20).catch(() => {})
      refreshControlPlane().catch(() => {})
    }, 2500)
    return () => window.clearInterval(timer)
  }, [refresh, refreshControlPlane, workspacePath])

  useEffect(() => {
    if (!selectedRun?.run_id) {
      setSelectedDetail(null)
      return
    }
    setSelectedRunId(selectedRun.run_id)
    getDetail(selectedRun.run_id, true).then(setSelectedDetail).catch(() => setSelectedDetail(null))
  }, [getDetail, selectedRun?.run_id])

  const handleSubmit = async () => {
    const message = prompt.trim()
    if (!message || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await pythonClient.send('rpc.agent.queue.submit', {
        message,
        workspace_path: workspacePath || undefined,
        conversation_id: `terminal-${Date.now()}`,
      })
      setPrompt('')
      await refreshControlPlane()
      processQueue().catch((e) => {
        setError(e?.message || String(e))
      })
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleStop = async (item: AgentQueueItem) => {
    setError(null)
    try {
      await cancelQueueItem(item.id)
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  return (
    <div className="flex h-full w-full select-none flex-col overflow-hidden bg-bg-primary">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
        <Terminal className="h-3.5 w-3.5 text-text-muted" />
        <span className="flex-1 truncate text-xs font-semibold text-text-secondary">
          {t.harnessTerminal.title}
        </span>
        <button
          type="button"
          onClick={() => {
            refresh(20).catch(() => {})
            refreshControlPlane().catch(() => {})
          }}
          className="flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-accent-primary/10 hover:text-accent-primary"
          title={t.common.refresh}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${controlLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="shrink-0 border-b border-border/70 p-2">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t.harnessTerminal.promptPlaceholder}
          className="h-20 w-full resize-none rounded-md border border-border bg-bg-secondary px-2 py-1.5 text-xs text-text-primary outline-none transition-colors placeholder:text-text-muted focus:border-accent-primary/60"
        />
        <div className="mt-2 flex items-center gap-1.5">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!prompt.trim() || submitting || !!activeQueue}
            className="flex h-7 flex-1 items-center justify-center gap-1.5 rounded-md bg-accent-primary/90 px-2 text-xs font-medium text-white transition-colors hover:bg-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {t.harnessTerminal.start}
          </button>
          {activeQueue && (
            <button
              type="button"
              onClick={() => handleStop(activeQueue)}
              className="flex h-7 items-center justify-center gap-1 rounded-md bg-accent-danger/10 px-2 text-xs font-medium text-accent-danger transition-colors hover:bg-accent-danger/15"
            >
              <CircleStop className="h-3.5 w-3.5" />
              {t.common.stop}
            </button>
          )}
        </div>
        {error && (
          <div className="mt-2 flex items-start gap-1.5 rounded-md bg-accent-danger/10 px-2 py-1.5 text-2xs text-accent-danger">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}
      </div>

      <QueueStrip queueItems={queueItems} />

      <div className="grid min-h-0 flex-1 grid-rows-[minmax(120px,0.42fr)_minmax(0,1fr)]">
        <RunList
          runs={runs}
          selectedRunId={selectedRun?.run_id || null}
          onSelect={setSelectedRunId}
          onOpenRuns={onOpenRuns}
        />
        <RunLog detail={selectedDetail} />
      </div>
    </div>
  )
}

function QueueStrip({ queueItems }: { queueItems: AgentQueueItem[] }) {
  const t = useT()
  const visible = queueItems.slice(0, 4)
  return (
    <div className="shrink-0 border-b border-border/70 px-2 py-2">
      <div className="mb-1 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-muted">
        <ListRestart className="h-3.5 w-3.5" />
        {t.harnessTerminal.queue}
      </div>
      {visible.length === 0 ? (
        <div className="text-2xs text-text-muted/65">{t.runs.noQueuedItems}</div>
      ) : (
        <div className="space-y-1">
          {visible.map((item) => (
            <div key={item.id} className="flex items-center gap-1.5 text-2xs">
              <span className={`h-1.5 w-1.5 rounded-full ${statusColor(item.status)}`} />
              <span className="font-mono text-text-muted">{item.status}</span>
              <span className="min-w-0 flex-1 truncate text-text-secondary">
                {item.run_id || item.inbox_id || item.id}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RunList({
  runs,
  selectedRunId,
  onSelect,
  onOpenRuns,
}: {
  runs: RunSummary[]
  selectedRunId: string | null
  onSelect: (runId: string) => void
  onOpenRuns?: () => void
}) {
  const t = useT()
  return (
    <div className="min-h-0 overflow-y-auto border-b border-border/70 p-2 scrollbar-thin">
      <div className="mb-1 flex items-center gap-1.5">
        <span className="flex-1 text-2xs font-semibold uppercase tracking-wider text-text-muted">
          {t.harnessTerminal.recentRuns}
        </span>
        {selectedRunId && onOpenRuns && (
          <button
            type="button"
            onClick={onOpenRuns}
            className="flex h-5 w-5 items-center justify-center rounded text-text-muted transition-colors hover:bg-accent-primary/10 hover:text-accent-primary"
            title={t.harnessTerminal.openRun}
          >
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>
      {runs.length === 0 ? (
        <div className="text-2xs text-text-muted/65">{t.runs.noRuns}</div>
      ) : (
        <div className="space-y-1">
          {runs.slice(0, 12).map((run) => (
            <button
              key={run.run_id}
              type="button"
              onClick={() => onSelect(run.run_id)}
              className={`w-full rounded-md px-2 py-1.5 text-left transition-colors ${
                selectedRunId === run.run_id
                  ? 'bg-accent-primary/10 text-text-primary'
                  : 'text-text-secondary hover:bg-bg-hover'
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${statusColor(run.status)}`} />
                <span className="font-mono text-2xs text-text-muted">{run.run_id.slice(0, 8)}</span>
                <span className="text-2xs text-text-muted/70">{run.status}</span>
              </div>
              <div className="mt-0.5 truncate text-2xs">{run.prompt || t.runs.emptyPrompt}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function RunLog({ detail }: { detail: RunDetail | null }) {
  const t = useT()
  const rows = useMemo(() => {
    if (!detail) return []
    const toolRows = (detail.tool_call_events || detail.tool_calls || []).map((call: any, index: number) => ({
      key: `tool-${call.call_id || call.ts || index}`,
      label: call.name || 'tool',
      text: stringifyLogValue(call.error || call.output || call.status || ''),
      tone: call.error ? 'error' : 'normal',
    }))
    const eventRows = ((detail as any).events || []).slice(-20).map((event: any, index: number) => ({
      key: `event-${index}-${event.ts || ''}`,
      label: event.type || 'event',
      text: stringifyLogValue(event.data || {}),
      tone: event.type === 'error' ? 'error' : 'muted',
    }))
    return [...toolRows.slice(-30), ...eventRows].slice(-50)
  }, [detail])

  return (
    <div className="min-h-0 overflow-y-auto p-2 scrollbar-thin">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-text-muted">
        {t.harnessTerminal.logs}
      </div>
      {!detail ? (
        <div className="text-2xs text-text-muted/65">{t.runs.noDetail}</div>
      ) : rows.length === 0 ? (
        <div className="text-2xs text-text-muted/65">{t.common.noOutputYet}</div>
      ) : (
        <div className="space-y-1">
          {rows.map((row) => (
            <div key={row.key} className="rounded bg-bg-secondary/70 px-2 py-1">
              <div className="font-mono text-2xs text-text-muted">{row.label}</div>
              {row.text && (
                <div className={`mt-0.5 max-h-16 overflow-hidden break-words text-2xs ${
                  row.tone === 'error' ? 'text-accent-danger' : 'text-text-secondary'
                }`}>
                  {row.text}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function statusColor(status?: string) {
  if (status === 'running') return 'bg-accent-warning animate-pulse-soft'
  if (status === 'queued') return 'bg-accent-primary'
  if (status === 'success' || status === 'completed') return 'bg-accent-success'
  if (status === 'error') return 'bg-accent-danger'
  if (status === 'cancelled') return 'bg-text-muted'
  return 'bg-text-muted/50'
}

function stringifyLogValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
