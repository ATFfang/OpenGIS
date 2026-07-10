import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Loader2,
  PackageOpen,
  RefreshCw,
  Search,
} from 'lucide-react'
import { pythonClient } from '@/services/pythonClient'
import { useAssetStore } from '@/stores/assetStore'
import { useViewStore } from '@/stores/viewStore'
import { useT } from '@/i18n'

interface OperationSummary {
  id: string
  name?: string
  version?: string
  status?: string
  description?: string
  entry?: string
  dependencies?: string[]
  last_success_run?: string
  updated_at?: string
  abs_path?: string
  path?: string
}

export function OperationsPanel() {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const tabs = useViewStore((s) => s.tabs)
  const activeTabId = useViewStore((s) => s.activeTabId)
  const [query, setQuery] = useState('')
  const [operations, setOperations] = useState<OperationSummary[]>([])
  const [operationRoot, setOperationRoot] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const activeOperationPath = useMemo(() => {
    const active = tabs.find((tab) => tab.id === activeTabId && tab.language === 'operation')
    return active?.filePath || ''
  }, [tabs, activeTabId])

  const refresh = useCallback(async () => {
    if (!workspacePath) {
      setOperations([])
      setOperationRoot('')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const result = await pythonClient.send<{
        operation_root?: string
        operations?: OperationSummary[]
      }>('rpc.operations.list', {
        workspace_path: workspacePath,
        query,
        limit: 100,
      }, 20000)
      setOperations(Array.isArray(result?.operations) ? result.operations : [])
      setOperationRoot(result?.operation_root || '')
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [workspacePath, query])

  useEffect(() => {
    refresh().catch(() => {})
  }, [refresh])

  const openOperation = useCallback((operation: OperationSummary) => {
    const filePath = operation.abs_path
      ? `${operation.abs_path.replace(/[\\/]+$/, '')}/operation.json`
      : `${operationRoot.replace(/[\\/]+$/, '')}/${operation.id}/operation.json`
    const view = useViewStore.getState()
    const existing = view.tabs.find((tab) => tab.filePath === filePath)
    if (existing) {
      view.setActiveTab(existing.id)
      return
    }
    view.openTab({
      title: operation.name || operation.id,
      type: 'code',
      filePath,
      language: 'operation',
      content: JSON.stringify({ operationId: operation.id }),
    })
  }, [operationRoot])

  return (
    <div className="w-full h-full flex flex-col bg-bg-primary overflow-hidden select-none">
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0 gap-1">
        <PackageOpen className="w-3.5 h-3.5 text-accent-primary" />
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          {t.operations.title}
        </span>
        <button
          onClick={() => refresh()}
          disabled={!workspacePath || loading}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors disabled:opacity-40"
          title={t.common.refresh}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="border-b border-border px-2 py-2">
        <div className="flex items-center gap-1.5 rounded-md bg-bg-secondary border border-border px-2 h-8">
          <Search className="w-3.5 h-3.5 text-text-muted shrink-0" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t.operations.searchPlaceholder}
            className="min-w-0 flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-muted outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {!workspacePath ? (
          <StateMessage icon="empty" title={t.operations.noWorkspace} body={t.operations.noWorkspaceHint} />
        ) : error ? (
          <StateMessage icon="error" title={t.operations.failedToLoad} body={error} />
        ) : operations.length === 0 ? (
          <StateMessage
            icon={loading ? 'loading' : 'empty'}
            title={loading ? t.common.loading : t.operations.empty}
            body={operationRoot || t.operations.emptyHint}
          />
        ) : (
          <div className="py-1">
            {operations.map((operation) => {
              const filePath = operation.abs_path
                ? `${operation.abs_path.replace(/[\\/]+$/, '')}/operation.json`
                : `${operationRoot.replace(/[\\/]+$/, '')}/${operation.id}/operation.json`
              return (
                <OperationRow
                  key={operation.id}
                  operation={operation}
                  active={filePath === activeOperationPath}
                  onOpen={() => openOperation(operation)}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function OperationRow({
  operation,
  active,
  onOpen,
}: {
  operation: OperationSummary
  active: boolean
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
        active ? 'bg-accent-primary/12 text-text-primary' : 'text-text-secondary hover:bg-bg-hover'
      }`}
      title={operation.description || operation.id}
    >
      <PackageOpen className="h-3.5 w-3.5 shrink-0 text-accent-primary/80" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{operation.name || operation.id}</span>
          <StatusBadge status={operation.status} />
        </div>
        <div className="mt-0.5 truncate text-2xs text-text-muted">
          {operation.description || operation.id}
        </div>
      </div>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-muted opacity-60 group-hover:opacity-100" />
    </button>
  )
}

function StatusBadge({ status }: { status?: string }) {
  const normalized = (status || 'draft').toLowerCase()
  const tone =
    normalized === 'validated'
      ? 'bg-accent-success/12 text-accent-success border-accent-success/25'
      : normalized === 'failed'
      ? 'bg-accent-danger/12 text-accent-danger border-accent-danger/25'
      : 'bg-bg-secondary text-text-muted border-border'
  return (
    <span className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] leading-none ${tone}`}>
      {status || 'draft'}
    </span>
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
    <div className="h-full flex flex-col items-center justify-center px-5 text-center text-text-muted">
      <Icon className={`mb-2 h-5 w-5 ${icon === 'loading' ? 'animate-spin' : ''}`} />
      <div className="text-xs font-medium text-text-secondary">{title}</div>
      {body && <div className="mt-1 text-2xs leading-relaxed">{body}</div>}
    </div>
  )
}
