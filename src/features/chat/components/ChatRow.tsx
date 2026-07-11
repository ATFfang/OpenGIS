import { memo } from 'react'
import type { MessagePart, ChatMessage } from '@/types/chat'
import { messagePartsForRender } from '@/services/chatMessageParts'
import { MessagePartRow } from './MessagePartRow'

interface ChatRowProps {
  message: ChatMessage
  isExpanded: boolean
  onToggleExpand: (ts: number) => void
}

const ChatRow = memo(({ message, isExpanded, onToggleExpand }: ChatRowProps) => {
  return (
    <div className="relative">
      <ChatRowContent
        message={message}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  )
})

ChatRow.displayName = 'ChatRow'
export default ChatRow

const ChatRowContent = memo(({ message, isExpanded, onToggleExpand }: ChatRowProps) => {
  const parts = messagePartsForRender(message)
  const handleToggle = () => onToggleExpand(message.ts)

  if (parts.length === 0) return <div className="h-px" aria-hidden />
  return (
    <MessagePartsRenderer
      message={message}
      parts={parts}
      isExpanded={isExpanded}
      onToggleExpand={handleToggle}
    />
  )
})

ChatRowContent.displayName = 'ChatRowContent'

function MessagePartsRenderer({
  message,
  parts,
  isExpanded,
  onToggleExpand,
}: {
  message: ChatMessage
  parts: MessagePart[]
  isExpanded: boolean
  onToggleExpand: () => void
}) {
  return (
    <div className="space-y-1.5">
      {parts.map((part, index) => (
        <MessagePartRow
          key={part.id || `${message.ts}:${index}`}
          message={message}
          part={part}
          isExpanded={isExpanded}
          onToggleExpand={onToggleExpand}
        />
      ))}
    </div>
  )
}
