import { forwardRef, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Code2,
  KeyRound,
  ListChecks,
  MessageSquareText,
  ShieldAlert,
  X,
} from 'lucide-react'
import { pythonClient } from '@/services/pythonClient'
import { useApprovalStore, type ApprovalRequest } from '@/stores/approvalStore'
import { useAssetStore } from '@/stores/assetStore'

export function ApprovalGate() {
  const current = useApprovalStore((s) => s.current)
  const queueLength = useApprovalStore((s) => s.queue.length)
  const inlineHostCount = useApprovalStore((s) => s.inlineHostCount)
  const resolveCurrent = useApprovalStore((s) => s.resolveCurrent)

  useEffect(() => {
    if (!current?.timeoutSeconds) return
    const timer = window.setTimeout(() => {
      resolveCurrent({ approved: false, answer: null })
    }, current.timeoutSeconds * 1000)
    return () => window.clearTimeout(timer)
  }, [current?.id, current?.timeoutSeconds, resolveCurrent])

  if (!current || inlineHostCount > 0) return null

  return (
    <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/55 backdrop-blur-sm animate-fade-in">
      <ApprovalCard request={current} queueLength={queueLength} />
    </div>
  )
}

export function ApprovalInline() {
  const current = useApprovalStore((s) => s.current)
  const queueLength = useApprovalStore((s) => s.queue.length)
  const resolveCurrent = useApprovalStore((s) => s.resolveCurrent)
  const registerInlineHost = useApprovalStore((s) => s.registerInlineHost)
  const unregisterInlineHost = useApprovalStore((s) => s.unregisterInlineHost)

  useEffect(() => {
    registerInlineHost()
    return unregisterInlineHost
  }, [registerInlineHost, unregisterInlineHost])

  useEffect(() => {
    if (!current?.timeoutSeconds) return
    const timer = window.setTimeout(() => {
      resolveCurrent({ approved: false, answer: null })
    }, current.timeoutSeconds * 1000)
    return () => window.clearTimeout(timer)
  }, [current?.id, current?.timeoutSeconds, resolveCurrent])

  if (!current) return null

  return (
    <div className="bg-transparent px-2.5 py-1.5 animate-slide-up">
      <ApprovalCard request={current} queueLength={queueLength} embedded />
    </div>
  )
}

function ApprovalCard({
  request,
  queueLength,
  embedded = false,
}: {
  request: ApprovalRequest
  queueLength: number
  embedded?: boolean
}) {
  const resolveCurrent = useApprovalStore((s) => s.resolveCurrent)
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const [value, setValue] = useState(request.defaultValue ?? '')
  const [busy, setBusy] = useState<'allow' | 'remember' | null>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    setValue(request.defaultValue ?? '')
    window.setTimeout(() => textAreaRef.current?.focus(), 0)
  }, [request.id, request.defaultValue])

  const riskItems = request.risks?.filter(Boolean) ?? []
  const isResidentWorkerTool = ['start_worker', 'pause_worker', 'delete_worker'].includes(request.toolName || '')
  const canRemember = Boolean(
    workspacePath
      && request.toolName
      && request.kind !== 'choose'
      && request.kind !== 'text'
      && !isResidentWorkerTool,
  )

  const deny = () => resolveCurrent({ approved: false, answer: null })

  const allowOnce = async () => {
    if (request.kind === 'choose' || request.kind === 'text') {
      resolveCurrent({ answer: value })
      return
    }
    resolveCurrent({ approved: true })
  }

  const rememberAndAllow = async () => {
    if (!workspacePath || !request.toolName) {
      resolveCurrent({ approved: true })
      return
    }
    setBusy('remember')
    try {
      await pythonClient.send('rpc.agent.permissions.rules.add', {
        workspace_path: workspacePath,
        tool: request.toolName,
        action: 'allow',
        scope: 'workspace',
        reason: request.message || request.title,
      })
      import('@/stores/runsStore').then(({ useRunsStore }) => {
        useRunsStore.getState().refreshControlPlane().catch(() => {})
      })
      resolveCurrent({ approved: true })
    } finally {
      setBusy(null)
    }
  }

  const primaryLabel = request.kind === 'choose'
    ? '提交选择'
    : request.kind === 'text'
      ? '提交'
      : '本次允许'
  const isCodeApproval = request.kind === 'code'
  const cardClass = embedded
    ? `approval-card w-full max-h-[min(360px,42vh)] overflow-hidden rounded-md ${
        isCodeApproval
          ? 'border border-accent-primary/55 ring-1 ring-accent-primary/25'
          : 'border ring-1 ring-black/5 dark:ring-white/5'
      }`
    : `approval-card w-[min(720px,calc(100vw-32px))] max-h-[min(760px,calc(100vh-48px))] overflow-hidden rounded-lg border ${
        isCodeApproval
          ? 'border-accent-primary/65 ring-2 ring-accent-primary/20'
          : 'approval-border'
      }`
  const headerClass = embedded
    ? 'flex items-start gap-2 px-3 pb-1.5 pt-2'
    : 'flex items-start gap-3 border-b approval-border px-4 py-3'
  const iconWrapClass = embedded
    ? 'approval-muted mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded'
    : 'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md'
  const titleClass = embedded
    ? 'truncate text-xs font-semibold text-text-primary'
    : 'truncate text-sm font-semibold text-text-primary'
  const messageClass = embedded
    ? 'mt-0.5 line-clamp-2 text-2xs leading-snug text-text-secondary'
    : 'mt-1 text-xs leading-relaxed text-text-secondary'
  const bodyClass = embedded
    ? 'max-h-[205px] overflow-y-auto px-3 pb-1.5 pt-1 scrollbar-thin'
    : 'max-h-[520px] overflow-y-auto px-4 py-3 scrollbar-thin'
  const footerClass = embedded
    ? 'approval-strong flex items-center gap-1.5 px-3 pb-2 pt-1.5'
    : 'approval-strong flex items-center gap-2 border-t approval-border px-4 py-3'
  const secondaryButtonClass = embedded
    ? 'approval-muted inline-flex h-7 items-center gap-1 rounded px-2.5 text-2xs text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary'
    : 'approval-muted inline-flex h-8 items-center gap-1.5 rounded border approval-border px-3 text-xs text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary'
  const rememberButtonClass = embedded
    ? 'inline-flex h-7 items-center gap-1 rounded bg-accent-primary/10 px-2.5 text-2xs text-accent-primary transition-colors hover:bg-accent-primary/15 disabled:opacity-60'
    : 'inline-flex h-8 items-center gap-1.5 rounded border border-accent-primary/30 bg-accent-primary/10 px-3 text-xs text-accent-primary transition-colors hover:bg-accent-primary/15 disabled:opacity-60'
  const primaryButtonClass = embedded
    ? 'inline-flex h-7 items-center gap-1 rounded bg-accent-primary px-2.5 text-2xs font-medium text-white transition-colors hover:bg-accent-primary/90 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex h-8 items-center gap-1.5 rounded bg-accent-primary px-3 text-xs font-medium text-white transition-colors hover:bg-accent-primary/90 disabled:cursor-not-allowed disabled:opacity-60'
  const actionIconClass = embedded ? 'h-3 w-3' : 'h-3.5 w-3.5'

  return (
    <div
      className={cardClass}
      role="dialog"
      aria-modal={embedded ? undefined : true}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          deny()
        }
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          allowOnce()
        }
      }}
    >
      <div className={headerClass}>
        <div className={`${iconWrapClass} ${request.danger ? 'text-accent-danger' : 'text-accent-primary'} ${embedded ? '' : request.danger ? 'bg-accent-danger/10' : 'bg-accent-primary/10'}`}>
          {iconFor(request, embedded)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className={titleClass}>{request.title}</h2>
            {request.toolName && (
              <span className={`${embedded ? 'approval-muted px-1 py-0.5 text-[10px]' : 'approval-muted border approval-border px-1.5 py-0.5 text-2xs'} shrink-0 rounded font-mono text-text-secondary`}>
                {request.toolName}
              </span>
            )}
          </div>
          <p className={messageClass}>
            {request.message || labelForKind(request.kind)}
          </p>
        </div>
        {queueLength > 0 && (
          <span className={`${embedded ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-2xs'} approval-muted rounded-full text-text-muted`}>
            队列 +{queueLength}
          </span>
        )}
      </div>

      <div className={bodyClass}>
        {riskItems.length > 0 && (
          <div className={`${embedded ? 'mb-2 px-2 py-1.5' : 'mb-3 border border-accent-warning/25 px-3 py-2'} rounded-md bg-accent-warning/10`}>
            <div className={`${embedded ? 'mb-1 text-2xs' : 'mb-1.5 text-xs'} flex items-center gap-1.5 font-medium text-accent-warning`}>
              <ShieldAlert className={actionIconClass} />
              风险提示
            </div>
            <ul className="space-y-1">
              {riskItems.map((risk, index) => (
                <li key={`${risk}-${index}`} className={`${embedded ? 'gap-1.5 text-2xs leading-snug' : 'gap-2 text-xs leading-relaxed'} flex text-text-secondary`}>
                  <ChevronRight className={`${actionIconClass} mt-0.5 shrink-0 text-accent-warning`} />
                  <span className="break-words">{risk}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {request.kind === 'code' && (
          <CodePreview code={request.code || ''} runId={request.runId} step={request.step} embedded={embedded} />
        )}

        {request.kind === 'choose' && (
          <ChoiceInput
            options={request.options ?? []}
            value={value}
            onChange={setValue}
            embedded={embedded}
          />
        )}

        {request.kind === 'text' && (
          <TextInput
            ref={textAreaRef}
            value={value}
            placeholder={request.placeholder}
            onChange={setValue}
            embedded={embedded}
          />
        )}
      </div>

      <div className={footerClass}>
        <button
          onClick={deny}
          className={secondaryButtonClass}
        >
          <X className={actionIconClass} />
          拒绝
        </button>
        <div className="flex-1" />
        {canRemember && (
          <button
            onClick={rememberAndAllow}
            disabled={busy !== null}
            className={rememberButtonClass}
          >
            <KeyRound className={actionIconClass} />
            {busy === 'remember' ? '保存中...' : '本工作区记住'}
          </button>
        )}
        <button
          onClick={() => {
            setBusy('allow')
            Promise.resolve(allowOnce()).finally(() => setBusy(null))
          }}
          disabled={busy !== null || (request.kind === 'choose' && !value.trim())}
          className={primaryButtonClass}
        >
          <Check className={actionIconClass} />
          {busy === 'allow' ? '处理中...' : primaryLabel}
        </button>
      </div>
    </div>
  )
}

function iconFor(request: ApprovalRequest, compact = false) {
  const className = compact ? 'h-3.5 w-3.5' : 'h-4 w-4'
  if (request.kind === 'code') return <Code2 className={className} />
  if (request.kind === 'choose') return <ListChecks className={className} />
  if (request.kind === 'text') return <MessageSquareText className={className} />
  if (request.danger) return <AlertTriangle className={className} />
  return <KeyRound className={className} />
}

function labelForKind(kind: ApprovalRequest['kind']): string {
  if (kind === 'code') return 'Agent 请求执行代码。'
  if (kind === 'choose') return 'Agent 需要你选择一个选项。'
  if (kind === 'text') return 'Agent 需要你补充信息。'
  return 'Agent 请求执行需要确认的操作。'
}

function CodePreview({
  code,
  runId,
  step,
  embedded = false,
}: {
  code: string
  runId?: string
  step?: number
  embedded?: boolean
}) {
  const header = useMemo(() => {
    const parts = []
    if (runId) parts.push(`run ${runId.slice(0, 8)}`)
    if (step != null) parts.push(`step ${step}`)
    return parts.join(' · ')
  }, [runId, step])
  return (
    <div className="approval-muted border border-accent-primary/25 overflow-hidden rounded-md">
      <div className={`${embedded ? 'gap-1.5 px-2.5 py-1.5' : 'gap-2 border-b approval-border px-3 py-2'} flex items-center`}>
        <Code2 className={`${embedded ? 'h-3 w-3' : 'h-3.5 w-3.5'} text-text-muted`} />
        <span className={`${embedded ? 'text-2xs' : 'text-xs'} font-medium text-text-secondary`}>Python code</span>
        {header && <span className="ml-auto text-2xs font-mono text-text-muted">{header}</span>}
      </div>
      <pre className={`${embedded ? 'max-h-[150px] p-2.5 text-2xs leading-snug' : 'max-h-[320px] p-3 text-xs leading-relaxed'} overflow-auto text-text-primary scrollbar-thin`}>
        <code>{code || '# empty code'}</code>
      </pre>
    </div>
  )
}

function ChoiceInput({
  options,
  value,
  onChange,
  embedded = false,
}: {
  options: string[]
  value: string
  onChange: (value: string) => void
  embedded?: boolean
}) {
  return (
    <div className={embedded ? 'space-y-1' : 'space-y-1.5'}>
      {options.map((option) => (
        <button
          key={option}
          onClick={() => onChange(option)}
          className={`flex w-full items-center rounded-md text-left transition-colors ${
            value === option
              ? embedded
                ? 'bg-accent-primary/10 text-text-primary'
                : 'border border-accent-primary bg-accent-primary/10 text-text-primary'
              : embedded
                ? 'approval-muted text-text-secondary hover:bg-bg-hover'
                : 'approval-muted border approval-border text-text-secondary hover:bg-bg-hover'
          } ${embedded ? 'gap-1.5 px-2.5 py-1.5 text-2xs' : 'gap-2 px-3 py-2 text-xs'}`}
        >
          <span className={`h-2 w-2 rounded-full ${value === option ? 'bg-accent-primary' : 'bg-border'}`} />
          <span className="break-words">{option}</span>
        </button>
      ))}
    </div>
  )
}

const TextInput = forwardRef<HTMLTextAreaElement, {
  value: string
  placeholder?: string
  onChange: (value: string) => void
  embedded?: boolean
}>(({ value, placeholder, onChange, embedded = false }, ref) => {
  return (
    <textarea
      ref={ref}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={`${embedded ? 'min-h-20 border-transparent px-2.5 py-1.5 text-2xs leading-snug' : 'min-h-28 approval-border px-3 py-2 text-xs leading-relaxed focus:border-accent-primary'} approval-muted w-full resize-y rounded-md border text-text-primary outline-none transition-colors placeholder:text-text-muted`}
    />
  )
})
TextInput.displayName = 'TextInput'
