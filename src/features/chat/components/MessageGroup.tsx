import { type ReactNode, useCallback, useState } from 'react'
import { Check, Copy, Pencil } from 'lucide-react'
import { useT } from '@/i18n'
import { messagePartsForRender } from '@/services/chatMessageParts'
import type { ChatMessage } from '@/types/chat'
import type { MessageRole } from '../groupMessages'

interface MessageGroupProps {
  role: MessageRole
  items: ChatMessage[]
  onEditUser?: (text: string) => void
  highlighted?: boolean
  children: ReactNode
}

export function MessageGroup({
  role,
  items,
  onEditUser,
  highlighted = false,
  children,
}: MessageGroupProps) {
  const t = useT()

  if (role === 'system') {
    return <div className={`${highlighted ? 'bg-accent-primary/5' : ''} px-5 transition-colors`}>{children}</div>
  }

  if (role === 'user') {
    const userText = extractUserText(items)
    return (
      <div className={`${highlighted ? 'bg-accent-primary/5' : ''} px-5 py-1.5 animate-fade-in group/msg transition-colors`}>
        <div className="flex justify-end">
          <div className="max-w-[85%] min-w-0">
            {children}
            <div className="flex justify-end gap-0.5 mt-1 opacity-0 group-hover/msg:opacity-100 transition-opacity">
              {userText && onEditUser && (
                <MessageActionButton
                  title={t.chat.editAndResend}
                  onClick={() => onEditUser(userText)}
                  icon={<Pencil className="w-3 h-3" />}
                />
              )}
              {userText && <CopyActionButton text={userText} />}
            </div>
          </div>
        </div>
      </div>
    )
  }

  const groupText = extractGroupText(items)
  return (
    <div className={`${highlighted ? 'bg-accent-primary/5' : ''} px-5 py-1.5 animate-fade-in group/msg transition-colors`}>
      <div className="min-w-0 space-y-2">
        {children}
        {groupText && (
          <div className="flex gap-0.5 opacity-0 group-hover/msg:opacity-100 transition-opacity">
            <CopyActionButton text={groupText} />
          </div>
        )}
      </div>
    </div>
  )
}

export function extractGroupText(items: ChatMessage[]): string {
  const parts: string[] = []
  for (const message of items) {
    const messageParts = messagePartsForRender(message)
    if (messageParts.length > 0) {
      for (const part of messageParts) {
        if (part.type === 'text' && part.text?.trim()) {
          parts.push(part.text.trim())
        } else if (part.type === 'code' && part.text?.trim()) {
          parts.push('```python\n' + part.text.trim() + '\n```')
        } else if (part.type === 'tool_output' && part.text?.trim() && !isCodeExecutionOutput(part) && !isOperationToolOutput(part)) {
          parts.push(part.text.trim())
        }
      }
      continue
    }
  }
  return parts.join('\n\n')
}

function isOperationToolOutput(part: ReturnType<typeof messagePartsForRender>[number]): boolean {
  return !!part.tool && OPERATION_TOOLS.has(part.tool)
}

function isCodeExecutionOutput(part: ReturnType<typeof messagePartsForRender>[number]): boolean {
  const data = part.data ?? {}
  return (
    part.tool === 'execute_code'
    || part.tool === 'gis_execute_python'
    || data.stepNumber != null
    || data.step != null
  )
}

const OPERATION_TOOLS = new Set([
  'list_operations',
  'get_operation',
  'validate_operation',
  'run_operation',
  'create_operation',
  'edit_operation',
  'promote_script_to_operation',
])

function extractUserText(items: ChatMessage[]): string {
  for (const message of items) {
    for (const part of messagePartsForRender(message)) {
      if (part.type === 'text' && part.data?.role === 'user' && part.text?.trim()) {
        return part.text.trim()
      }
    }
  }
  return ''
}

function MessageActionButton({
  title,
  onClick,
  icon,
}: {
  title: string
  onClick: () => void
  icon: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted/70 hover:text-text-primary hover:bg-bg-hover transition-colors"
    >
      {icon}
    </button>
  )
}

function CopyActionButton({ text }: { text: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }, [text])

  return (
    <MessageActionButton
      title={t.common.copy}
      onClick={handleCopy}
      icon={copied ? <Check className="w-3 h-3 text-accent-success" /> : <Copy className="w-3 h-3" />}
    />
  )
}
