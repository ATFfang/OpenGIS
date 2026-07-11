import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  PackageOpen,
} from 'lucide-react'
import type { ViewTab } from '@/stores/viewStore'
import { useAssetStore } from '@/stores/assetStore'
import { pythonClient } from '@/services/pythonClient'
import { useT } from '@/i18n'
import MarkdownBlock from '@/features/chat/components/MarkdownBlock'

interface OperationDetail {
  id: string
  name?: string
  version?: string
  revision?: number
  status?: string
  scope?: string
  read_only?: boolean
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

interface OperationTabPayload {
  operationId?: string
}

export function OperationEditorView({ tab }: { tab: ViewTab }) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const operationId = getOperationId(tab)
  const [detail, setDetail] = useState<OperationDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [workspacePath, operationId])

  useEffect(() => {
    loadDetail().catch(() => {})
  }, [loadDetail])

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
  const readmeBaseDir = detail.abs_path || detail.path || workspacePath

  return (
    <div className="operation-workbench scrollbar-none h-full min-h-0 overflow-y-auto">
      <div className="sticky top-0 z-10 flex items-center gap-3 bg-bg-primary/95 px-5 py-3 backdrop-blur app-region-drag">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--operation-card-bg)] text-accent-primary">
          <PackageOpen className="h-4.5 w-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-text-primary">{detail.name || detail.id}</h2>
            <ScopeBadge scope={detail.scope} readOnly={detail.read_only} />
            <StatusBadge status={detail.status} />
          </div>
          <p className="mt-0.5 truncate text-xs text-text-muted">{detail.description || t.operations.noDescription}</p>
        </div>
      </div>

      <div className="mx-auto flex max-w-7xl flex-col gap-4 p-5">
        <section className="rounded-lg bg-[var(--operation-card-bg)] p-4">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.35fr_1fr]">
            <div className="min-w-0">
              <SectionTitle>Overview</SectionTitle>
              <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <InfoPill label={t.operations.version} value={detail.version || '-'} />
                <InfoPill label="Revision" value={String(detail.revision ?? '-')} />
                <InfoPill label={t.operations.entry} value={detail.entry || 'main.py'} />
                <InfoPill label="ID" value={detail.id} />
              </div>
            </div>

            <div className="min-w-0">
              <SectionTitle>{t.operations.runtime}</SectionTitle>
              <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                <InfoPill label="Language" value={detail.runtime?.language || 'python'} />
                <InfoPill label="Python" value={detail.runtime?.python || '-'} />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(dependencies.length ? dependencies : [t.operations.noDependencies]).map((dependency) => (
                  <span key={dependency} className="max-w-full truncate rounded bg-[var(--operation-card-soft)] px-2 py-1 text-2xs text-text-muted">
                    {dependency}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>

        {error && (
          <div className="rounded-lg bg-accent-danger/10 p-3 text-xs text-accent-danger">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <JsonBlock title={t.operations.inputSchema} value={detail.input_schema || {}} />
          <JsonBlock title={t.operations.outputSchema} value={detail.output_schema || {}} />
        </div>

        {detail.readme && (
          <section className="rounded-lg bg-[var(--operation-card-bg)] p-4">
            <SectionTitle>{t.operations.readme}</SectionTitle>
            <div className="scrollbar-none max-h-[520px] overflow-auto rounded-md bg-[var(--operation-card-muted)] px-3 py-2 text-xs leading-relaxed text-text-secondary select-text">
              <MarkdownBlock markdown={detail.readme} baseDir={readmeBaseDir} />
            </div>
          </section>
        )}
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
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] leading-none ${tone}`}>
      {status || 'draft'}
    </span>
  )
}

function ScopeBadge({ scope, readOnly }: { scope?: string; readOnly?: boolean }) {
  const normalized = (scope || 'workspace').toLowerCase()
  const label = normalized === 'builtin' ? '内置' : '项目'
  const tone = normalized === 'builtin'
    ? 'bg-accent-geo/10 text-accent-geo'
    : 'bg-bg-secondary text-text-muted'
  return (
    <span
      className={`shrink-0 rounded px-2 py-0.5 text-[10px] leading-none ${tone}`}
      title={readOnly ? 'OpenGIS built-in operation' : 'Workspace operation'}
    >
      {label}
    </span>
  )
}

function InfoPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md bg-[var(--operation-card-soft)] px-2.5 py-2">
      <div className="text-[10px] text-text-muted/70">{label}</div>
      <div className="truncate text-text-secondary" title={value}>{value}</div>
    </div>
  )
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="rounded-lg bg-[var(--operation-card-bg)] p-4">
      <SectionTitle>{title}</SectionTitle>
      <div className="scrollbar-none max-h-96 overflow-auto rounded-md bg-[var(--operation-card-muted)] p-3 font-mono text-[11px] leading-relaxed text-text-secondary select-text">
        <JsonValue value={value} />
      </div>
    </section>
  )
}

function JsonValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-text-muted">[]</span>
    return (
      <span>
        <span className="text-text-muted">[</span>
        <span className="block pl-4">
          {value.map((item, index) => (
            <span key={index} className="block">
              <JsonValue value={item} />
              {index < value.length - 1 && <span className="text-text-muted">,</span>}
            </span>
          ))}
        </span>
        <span className="text-text-muted">]</span>
      </span>
    )
  }

  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return <span className="text-text-muted">{'{}'}</span>
    return (
      <span>
        <span className="text-text-muted">{'{'}</span>
        <span className="block pl-4">
          {entries.map(([key, item], index) => (
            <span key={key} className="block">
              <span className="text-accent-primary/90">"{key}"</span>
              <span className="text-text-muted">: </span>
              <JsonValue value={item} />
              {index < entries.length - 1 && <span className="text-text-muted">,</span>}
            </span>
          ))}
        </span>
        <span className="text-text-muted">{'}'}</span>
      </span>
    )
  }

  if (typeof value === 'string') {
    return <span className="text-accent-success/90">"{value}"</span>
  }
  if (typeof value === 'number') {
    return <span className="text-accent-warning">{Number.isFinite(value) ? value : String(value)}</span>
  }
  if (typeof value === 'boolean') {
    return <span className="text-accent-geo">{String(value)}</span>
  }
  if (value == null) {
    return <span className="text-text-muted">null</span>
  }
  return <span>{String(value)}</span>
}

function SectionTitle({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`mb-2 text-2xs font-semibold uppercase tracking-wider text-text-muted ${className}`}>
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
