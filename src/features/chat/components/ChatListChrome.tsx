import appIconImg from '../../../../resources/icons/app-icon.png'

export type ActiveWorkState = { label: string; tone: 'thinking' | 'code' | 'working' } | null

export function ListSpacer() {
  return <div className="h-3" aria-hidden />
}

export function TypingFooter({ context }: { context?: ActiveWorkState }) {
  if (!context) return <div className="h-3" aria-hidden />
  return (
    <div className="px-5 py-3">
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-lg overflow-hidden shrink-0">
          <img src={appIconImg} alt="OpenGIS" className="w-full h-full object-contain" />
        </div>
        <ActiveWorkIndicator label={context.label} tone={context.tone} />
      </div>
    </div>
  )
}

function ActiveWorkIndicator({ label, tone }: { label: string; tone: 'thinking' | 'code' | 'working' }) {
  const dotClass =
    tone === 'code'
      ? 'bg-accent-warning'
      : tone === 'working'
        ? 'bg-accent-geo'
        : 'bg-accent-primary'
  return (
    <div className="flex items-center gap-2 pt-1 min-w-0">
      <span className="relative flex w-2 h-2 shrink-0" aria-hidden>
        <span className={`absolute inline-flex h-full w-full rounded-full ${dotClass} opacity-35 animate-ping`} />
        <span className={`relative inline-flex rounded-full w-2 h-2 ${dotClass}`} />
      </span>
      <span className="chat-thinking-text text-xs font-medium truncate">
        {label.endsWith('...') || label.endsWith('…') ? label : `${label}...`}
      </span>
    </div>
  )
}
