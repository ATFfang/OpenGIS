import { type ReactNode } from 'react'
import { FolderOpen, Globe, Zap } from 'lucide-react'
import { useAssetStore } from '@/stores/assetStore'
import { useT } from '@/i18n'
import OrbLogo from './OrbLogo'

export function WelcomeContent({ onSuggestionClick }: { onSuggestionClick: (text: string) => void }) {
  const t = useT()
  const workspacePath = useAssetStore((s) => s.workspacePath)
  const hasWorkspace = !!workspacePath

  const suggestions: Array<{
    text: string
    icon: ReactNode
    desc: string
    gradient: string
    iconColor: string
    borderColor: string
  }> = [
    {
      text: t.chat.suggestionLoadData,
      icon: <FolderOpen className="w-4 h-4" />,
      desc: t.chat.suggestionLoadDataDesc,
      gradient: 'from-blue-500/20 to-cyan-500/20',
      iconColor: 'text-blue-400',
      borderColor: 'hover:border-blue-500/30',
    },
    {
      text: t.chat.suggestionBuffer,
      icon: <Globe className="w-4 h-4" />,
      desc: t.chat.suggestionBufferDesc,
      gradient: 'from-green-500/20 to-emerald-500/20',
      iconColor: 'text-green-400',
      borderColor: 'hover:border-green-500/30',
    },
    {
      text: t.chat.suggestionChoropleth,
      icon: <Zap className="w-4 h-4" />,
      desc: t.chat.suggestionChoroplethDesc,
      gradient: 'from-purple-500/20 to-pink-500/20',
      iconColor: 'text-purple-400',
      borderColor: 'hover:border-purple-500/30',
    },
  ]

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      <div className="relative mb-8 animate-fade-in">
        <OrbLogo size={80} />
      </div>

      <h2 className="text-xl font-bold text-text-primary mb-2 animate-fade-in">
        {t.chat.emptyState.title}
      </h2>
      <p className="text-sm text-text-muted mb-8 max-w-[320px] leading-relaxed animate-fade-in">
        {t.chat.emptyState.hint}
      </p>

      {!hasWorkspace && (
        <div className="w-full max-w-[360px] mb-6 animate-fade-in">
          <div className="flex items-start gap-3 bg-amber-500/5 border border-amber-500/20 rounded-xl px-4 py-3">
            <div className="w-5 h-5 rounded-full bg-amber-500/15 flex items-center justify-center shrink-0 mt-0.5">
              <span className="text-amber-500 text-xs">⚠</span>
            </div>
            <div className="text-left">
              <p className="text-[12px] font-medium text-amber-600 dark:text-amber-400">
                {t.chat.emptyState.noWorkspace}
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-2 w-full max-w-[360px] animate-slide-up">
        {suggestions.map((suggestion, index) => (
          <button
            key={suggestion.text}
            onClick={() => onSuggestionClick(suggestion.text)}
            className={`w-full text-left bg-bg-secondary hover:bg-bg-tertiary border border-border ${suggestion.borderColor} rounded-xl px-4 py-3 transition-all duration-200 group hover:shadow-sm`}
            style={{ animationDelay: `${index * 50}ms` }}
          >
            <div className="flex items-center gap-3">
              <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${suggestion.gradient} flex items-center justify-center shrink-0 ring-1 ring-white/5`}>
                <span className={suggestion.iconColor}>{suggestion.icon}</span>
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-[10px] uppercase tracking-wider text-text-muted group-hover:text-accent-primary transition-colors font-semibold">
                  {suggestion.desc}
                </span>
                <p className="text-[13px] text-text-secondary group-hover:text-text-primary transition-colors truncate mt-0.5 leading-snug">
                  {suggestion.text}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
