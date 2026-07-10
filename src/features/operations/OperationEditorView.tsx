import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  FileCode,
  Loader2,
  PackageOpen,
  Play,
  RefreshCw,
} from 'lucide-react'
import type { ViewTab } from '@/stores/viewStore'
import { useAssetStore } from '@/stores/assetStore'
import { useViewStore } from '@/stores/viewStore'
import { pythonClient } from '@/services/pythonClient'
import { useT } from '@/i18n'

interface OperationDetail {
  id: string
  name?: string
  version?: string
  revision?: number
  status?: string
  description?: string
  entry?: string
  path?: string
  abs_path?: string
  runtime?: {
    language?: string
    python?: string
    dependencies?: string[]
  }
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  readme?: string
}

interface OperationRunRecord {
  run_id?: string
  status?: string
  returncode?: number
  started_at?: string
  finished_at?: string
  input_path?: string
  output_path?: string
  stdout_path?: string
  stderr_path?: string
  output?: Record<string, unknown>
}

interface OperationTabPayload {
  operationId?: string
}

export function OperationEditorView({ tab }: { tab: ViewTab }) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const operationId = getOperationId(tab)
  const [detail, setDetail] = useState<OperationDetail | null>(null)
  const [paramsText, setParamsText] = useState('{}')
  const [loading, setLoading] = useState(false)
  const [openingCode, setOpeningCode] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runResult, setRunResult] = useState<OperationRunRecord | null>(null)

  const loadDetail = useCallback(async () => {
    if (!workspacePath || !operationId) return
    setLoading(true)
    setError(null)
    try {
      const result = await pythonClient.send<{ operation?: OperationDetail }>('rpc.operations.get', {
        workspace_path: workspacePath,
        operation_id: operationId,
        include_readme: true,
        include_code: false,
      }, 20000)
      const operation = result?.operation ?? null
      setDetail(operation)
      setParamsText(buildInitialParams(operation?.input_schema))
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [workspacePath, operationId])

  useEffect(() => {
    loadDetail().catch(() => {})
  }, [loadDetail])

  const runOperation = useCallback(async () => {
    if (!workspacePath || !operationId) return
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(paramsText || '{}')
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error(t.operations.paramsMustBeObject)
      }
    } catch (err: any) {
      setError(`${t.operations.invalidParams}: ${err?.message || String(err)}`)
      return
    }
    setRunning(true)
    setError(null)
    try {
      const result = await pythonClient.send<OperationRunRecord>('rpc.operations.run', {
        workspace_path: workspacePath,
        operation_id: operationId,
        params: parsed,
        timeout_seconds: 600,
      }, 10 * 60 * 1000)
      setRunResult(result)
      await loadDetail()
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setRunning(false)
    }
  }, [workspacePath, operationId, paramsText, loadDetail, t.operations.invalidParams, t.operations.paramsMustBeObject])

  const openEntrypoint = useCallback(async () => {
    if (!detail?.abs_path) return
    const entry = detail.entry || 'main.py'
    const path = `${detail.abs_path.replace(/[\\/]+$/, '')}/${entry}`
    const api = window.electronAPI
    if (!api?.readFile) {
      setError('electronAPI.readFile is unavailable')
      return
    }
    setOpeningCode(true)
    setError(null)
    try {
      const result = await api.readFile(path)
      if (!result?.success || result.content == null) {
        throw new Error(result?.error || 'readFile returned no content')
      }
      useViewStore.getState().openFileAsTab(path, entry, result.content, 'python')
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setOpeningCode(false)
    }
  }, [detail])

  if (!workspacePath) {
    return <StateMessage icon="empty" title={t.operations.noWorkspace} body={t.operations.noWorkspaceHint} />
  }

  if (!operationId) {
    return <StateMessage icon="error" title={t.operations.failedToLoad} body="Missing operation id." />
  }

  if (loading && !detail) {
    return <StateMessage icon="loading" title={t.common.loading} body={operationId} />
  }

  if (!detail) {
    return <StateMessage icon="error" title={t.operations.failedToLoad} body={error || operationId} />
  }

  const dependencies = detail.runtime?.dependencies || []

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-bg-primary">
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-border bg-bg-primary/95 px-5 py-3 backdrop-blur">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-primary/10 text-accent-primary">
          <PackageOpen className="h-4.5 w-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-text-primary">{detail.name || detail.id}</h2>
            <StatusBadge status={detail.status} />
          </div>
          <p className="mt-0.5 truncate text-xs text-text-muted">{detail.description || t.operations.noDescription}</p>
        </div>
        <button
          onClick={() => loadDetail()}
          disabled={loading}
          className="flex h-8 items-center gap-1.5 rounded-md bg-bg-secondary px-2.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          {t.common.refresh}
        </button>
        <button
          onClick={openEntrypoint}
          disabled={openingCode}
          className="flex h-8 items-center gap-1.5 rounded-md bg-bg-secondary px-2.5 text-xs text-text-secondary hover:bg-bg-hover hover:text-accent-primary disabled:opacity-50"
        >
          {openingCode ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileCode className="h-3.5 w-3.5" />}
          {t.operations.openEntrypoint}
        </button>
      </div>

      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <section className="rounded-lg border border-border bg-bg-secondary/55 p-4">
            <SectionTitle>{t.operations.runtime}</SectionTitle>
            <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
              <InfoPill label={t.operations.version} value={detail.version || '-'} />
              <InfoPill label="Revision" value={String(detail.revision ?? '-')} />
              <InfoPill label={t.operations.entry} value={detail.entry || 'main.py'} />
              <InfoPill label="ID" value={detail.id} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(dependencies.length ? dependencies : [t.operations.noDependencies]).map((dependency) => (
                <span key={dependency} className="max-w-full truncate rounded border border-border bg-bg-primary px-2 py-1 text-2xs text-text-muted">
                  {dependency}
                </span>
              ))}
            </div>
          </section>

          <JsonBlock title={t.operations.inputSchema} value={detail.input_schema || {}} />
          <JsonBlock title={t.operations.outputSchema} value={detail.output_schema || {}} />

          {detail.readme && (
            <section className="rounded-lg border border-border bg-bg-secondary/55 p-4">
              <SectionTitle>{t.operations.readme}</SectionTitle>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-bg-primary p-3 text-xs text-text-secondary">
                {detail.readme}
              </pre>
            </section>
          )}
        </div>

        <aside className="space-y-4">
          <section className="rounded-lg border border-border bg-bg-secondary/55 p-4">
            <SectionTitle>{t.operations.params}</SectionTitle>
            <textarea
              value={paramsText}
              onChange={(event) => setParamsText(event.target.value)}
              spellCheck={false}
              className="h-56 w-full resize-none rounded-md border border-border bg-bg-primary p-3 font-mono text-xs text-text-primary outline-none focus:border-accent-primary/60"
            />
            <button
              onClick={runOperation}
              disabled={running}
              className="mt-3 flex h-9 w-full items-center justify-center gap-1.5 rounded-md bg-accent-primary text-xs font-medium text-white hover:bg-accent-primary/90 disabled:opacity-50"
            >
              {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              {running ? t.operations.running : t.operations.run}
            </button>
          </section>

          {error && (
            <div className="rounded-lg border border-accent-danger/30 bg-accent-danger/10 p-3 text-xs text-accent-danger">
              {error}
            </div>
          )}

          {runResult && (
            <section className="rounded-lg border border-border bg-bg-secondary/55 p-4">
              <SectionTitle>{t.operations.result}</SectionTitle>
              <pre className="max-h-96 overflow-auto rounded-md border border-border bg-bg-primary p-3 text-2xs text-text-secondary">
                {JSON.stringify(runResult, null, 2)}
              </pre>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}

function getOperationId(tab: ViewTab): string {
  try {
    const payload = JSON.parse(tab.content || '{}') as OperationTabPayload
    if (payload.operationId) return payload.operationId
  } catch {
    // Fall through to path-derived id.
  }
  const normalized = tab.filePath?.replace(/\\/g, '/') || ''
  const parts = normalized.split('/').filter(Boolean)
  if (parts[parts.length - 1] === 'operation.json') return parts[parts.length - 2] || ''
  return parts[parts.length - 1] || ''
}

function StatusBadge({ status }: { status?: string }) {
  const normalized = (status || 'draft').toLowerCase()
  const tone =
    normalized === 'validated'
      ? 'bg-accent-success/12 text-accent-success border-accent-success/25'
      : normalized === 'failed'
      ? 'bg-accent-danger/12 text-accent-danger border-accent-danger/25'
      : 'bg-bg-primary text-text-muted border-border'
  return (
    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] leading-none ${tone}`}>
      {status || 'draft'}
    </span>
  )
}

function InfoPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-bg-primary px-2.5 py-2">
      <div className="text-[10px] text-text-muted/70">{label}</div>
      <div className="truncate text-text-secondary" title={value}>{value}</div>
    </div>
  )
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="rounded-lg border border-border bg-bg-secondary/55 p-4">
      <SectionTitle>{title}</SectionTitle>
      <pre className="max-h-80 overflow-auto rounded-md border border-border bg-bg-primary p-3 text-xs text-text-secondary">
        {JSON.stringify(value, null, 2)}
      </pre>
    </section>
  )
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="mb-2 text-2xs font-semibold uppercase tracking-wider text-text-muted">
      {children}
    </div>
  )
}

function StateMessage({
  icon,
  title,
  body,
}: {
  icon: 'empty' | 'error' | 'loading'
  title: string
  body?: string
}) {
  const Icon =
    icon === 'error'
      ? AlertCircle
      : icon === 'loading'
      ? Loader2
      : CheckCircle2
  return (
    <div className="h-full flex flex-col items-center justify-center bg-bg-primary px-5 text-center text-text-muted">
      <Icon className={`mb-2 h-6 w-6 ${icon === 'loading' ? 'animate-spin' : ''}`} />
      <div className="text-sm font-medium text-text-secondary">{title}</div>
      {body && <div className="mt-1 max-w-md text-xs leading-relaxed">{body}</div>}
    </div>
  )
}

function buildInitialParams(schema: Record<string, unknown> | undefined): string {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) {
    return '{}'
  }
  const initial: Record<string, unknown> = {}
  for (const [key, raw] of Object.entries(properties as Record<string, any>)) {
    if (raw && typeof raw === 'object' && 'default' in raw) {
      initial[key] = raw.default
    }
  }
  return JSON.stringify(initial, null, 2)
}
