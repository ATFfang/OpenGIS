import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Globe } from 'lucide-react'

interface ThinkingRowProps {
  showTitle?: boolean
  reasoningContent?: string
  isVisible: boolean
  isExpanded: boolean
  onToggle?: () => void
  title?: string
  isStreaming?: boolean
  showChevron?: boolean
}

export const ThinkingRow = memo(({
  showTitle = false,
  reasoningContent,
  isVisible,
  isExpanded,
  onToggle,
  title = 'Thinking',
  isStreaming = false,
  showChevron = true,
}: ThinkingRowProps) => {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [canScrollUp, setCanScrollUp] = useState(false)
  const [canScrollDown, setCanScrollDown] = useState(false)

  const checkScrollable = useCallback(() => {
    if (scrollRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
      setCanScrollUp(scrollTop > 1)
      setCanScrollDown(scrollTop + clientHeight < scrollHeight - 1)
    }
  }, [])

  // Auto-scroll to bottom during streaming
  useEffect(() => {
    if (scrollRef.current && isVisible) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
    checkScrollable()
  }, [reasoningContent, isVisible, checkScrollable])

  if (!isVisible) return null
  if (!isExpanded && !showTitle) return null

  return (
    <div className="mb-1">
      {showTitle && (
        <button
          onClick={onToggle}
          className={`inline-flex items-center gap-1.5 text-left select-none px-0 py-0.5 bg-transparent border-none text-text-muted text-[13px] ${
            onToggle ? 'cursor-pointer hover:text-text-secondary' : 'cursor-default'
          } transition-colors duration-150`}
        >
          <Globe className={`w-4 h-4 ${isStreaming ? 'text-accent-primary' : 'text-text-muted opacity-60'}`} />
          <span
            className={`leading-[1.2] font-medium ${
              isStreaming
                ? 'chat-thinking-text select-none'
                : ''
            }`}
          >
            {title}
          </span>
          {showChevron && (
            <span className="transition-transform duration-150">
              {isExpanded
                ? <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
                : <ChevronRight className="w-3.5 h-3.5 text-text-muted" />
              }
            </span>
          )}
        </button>
      )}

      {isExpanded && (
        <div className="relative flex-1 mt-1.5">
          <div className="absolute left-0 top-0 bottom-0 w-[2px] rounded-full bg-accent-primary/15" />
          <div
            ref={scrollRef}
            onScroll={checkScrollable}
            className="flex max-h-[180px] overflow-y-auto text-text-muted leading-relaxed whitespace-pre-wrap break-words pl-3.5 scrollbar-none"
          >
            <span className="pb-2 block text-[12px] italic">{reasoningContent}</span>
          </div>
          {/* Scroll fade indicators */}
          {canScrollUp && (
            <div className="absolute top-0 left-3.5 right-0 h-6 pointer-events-none bg-gradient-to-b from-bg-primary to-transparent" />
          )}
          {canScrollDown && (
            <div className="absolute bottom-0 left-3.5 right-0 h-6 pointer-events-none bg-gradient-to-t from-bg-primary to-transparent" />
          )}
        </div>
      )}
    </div>
  )
})

ThinkingRow.displayName = 'ThinkingRow'
