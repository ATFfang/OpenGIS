import { type RefObject } from 'react'
import { ArrowUp, ChevronDown, Search, X } from 'lucide-react'

interface ChatSearchCapsuleProps {
  query: string
  inputRef: RefObject<HTMLInputElement>
  current: number
  total: number
  onQueryChange: (value: string) => void
  onPrevious: () => void
  onNext: () => void
  onClose: () => void
}

export function ChatSearchCapsule({
  query,
  inputRef,
  current,
  total,
  onQueryChange,
  onPrevious,
  onNext,
  onClose,
}: ChatSearchCapsuleProps) {
  const hasQuery = query.trim().length > 0
  const hasResults = total > 0

  return (
    <div className="shrink-0 bg-bg-primary px-3 py-1.5">
      <div className="mx-auto flex w-full max-w-[520px] items-center gap-1.5 rounded-full bg-bg-secondary/85 px-2 py-1 shadow-[0_1px_10px_rgba(0,0,0,0.08)] ring-1 ring-black/5 dark:ring-white/5">
        <Search className="h-3.5 w-3.5 shrink-0 text-text-muted" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              onClose()
              return
            }
            if (e.key === 'Enter') {
              e.preventDefault()
              if (e.shiftKey) onPrevious()
              else onNext()
            }
          }}
          placeholder="搜索对话"
          className="min-w-0 flex-1 bg-transparent px-1 text-xs text-text-primary outline-none placeholder:text-text-muted/50"
        />
        <span className={`min-w-[42px] text-center text-[10px] tabular-nums ${hasQuery && !hasResults ? 'text-accent-danger' : 'text-text-muted'}`}>
          {hasQuery ? (hasResults ? `${current}/${total}` : '无结果') : 'Ctrl F'}
        </span>
        <button
          type="button"
          onClick={onPrevious}
          disabled={!hasResults}
          className="flex h-6 w-6 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-35"
          title="上一个"
        >
          <ArrowUp className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!hasResults}
          className="flex h-6 w-6 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-35"
          title="下一个"
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex h-6 w-6 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary"
          title="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
