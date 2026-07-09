import { memo } from 'react'
import type { MessagePart, UIMessage } from '@/types/chat'
import { messagePartsForRender } from '@/services/chatMessageParts'
import { MessagePartRow } from './MessagePartRow'

interface ChatRowProps {
  message: UIMessage
  isExpanded: boolean
  onToggleExpand: (ts: number) => void
  isLast: boolean
}

const ChatRow = memo(({ message, isExpanded, onToggleExpand, isLast }: ChatRowProps) => {
  return (
    <div className="relative">
      <ChatRowContent
        message={message}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
        isLast={isLast}
      />
    </div>
  )
})

ChatRow.displayName = 'ChatRow'
export default ChatRow

const ChatRowContent = memo(({ message, isExpanded, onToggleExpand, isLast }: ChatRowProps) => {
  const parts = messagePartsForRender(message)
  const handleToggle = () => onToggleExpand(message.ts)

  if (parts.length === 0) return <div className="h-px" aria-hidden />
  return (
    <MessagePartsRenderer
      message={message}
      parts={parts}
      isExpanded={isExpanded}
      onToggleExpand={handleToggle}
      isLast={isLast}
    />
  )
})

ChatRowContent.displayName = 'ChatRowContent'

function MessagePartsRenderer({
  message,
  parts,
  isExpanded,
  onToggleExpand,
  isLast,
}: {
  message: UIMessage
  parts: MessagePart[]
  isExpanded: boolean
  onToggleExpand: () => void
  isLast: boolean
}) {
  return (
    <div className="space-y-2">
      {parts.map((part, index) => (
        <MessagePartRow
          key={part.id || `${message.ts}:${index}`}
          message={message}
          part={part}
          isExpanded={isExpanded}
          onToggleExpand={onToggleExpand}
          isLast={isLast}
        />
      ))}
    </div>
  )
}
