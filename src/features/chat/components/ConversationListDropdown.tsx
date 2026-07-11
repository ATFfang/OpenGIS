import { useEffect, useRef, useState } from 'react'
import { MessageSquare, Pencil, Trash2 } from 'lucide-react'
import { useChatStore } from '@/stores/chatStore'
import { useT } from '@/i18n'
import type { ChatMessage } from '@/types/chat'

interface ConversationListDropdownProps {
  conversations: { id: string; title: string; messages: ChatMessage[]; updatedAt: number }[]
  activeId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onClose: () => void
}

export function ConversationListDropdown({
  conversations,
  activeId,
  onSelect,
  onDelete,
  onClose,
}: ConversationListDropdownProps) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const titleInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId) {
      titleInputRef.current?.focus()
      titleInputRef.current?.select()
    }
  }, [editingId])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  const finishRename = (id: string) => {
    const trimmed = editingTitle.trim()
    if (trimmed) {
      useChatStore.getState().renameConversation(id, trimmed)
    }
    setEditingId(null)
  }

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-64 max-h-80 overflow-y-auto bg-bg-primary border border-border rounded-xl shadow-xl z-[999] animate-fade-in"
      style={{ scrollbarWidth: 'thin' }}
    >
      <div className="p-2">
        <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold px-2 py-1.5">
          {t.chat.conversations} ({conversations.length})
        </div>
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`group flex items-center gap-2 px-2 py-2 rounded-lg cursor-pointer transition-all duration-150 ${
              conversation.id === activeId
                ? 'bg-accent-primary/10 text-accent-primary'
                : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
            }`}
            onClick={() => onSelect(conversation.id)}
          >
            <MessageSquare className="w-3.5 h-3.5 shrink-0" />
            <div className="flex-1 min-w-0">
              {editingId === conversation.id ? (
                <input
                  ref={titleInputRef}
                  className="text-[12px] leading-tight bg-bg-primary border border-border rounded px-1 py-0.5 outline-none focus:border-accent-primary w-full"
                  value={editingTitle}
                  onChange={(event) => setEditingTitle(event.target.value)}
                  onClick={(event) => event.stopPropagation()}
                  onBlur={() => finishRename(conversation.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      finishRename(conversation.id)
                    } else if (event.key === 'Escape') {
                      setEditingId(null)
                    }
                  }}
                />
              ) : (
                <p
                  className="text-[12px] truncate leading-tight"
                  onDoubleClick={(event) => {
                    event.stopPropagation()
                    setEditingTitle(conversation.title || t.chat.newConversation)
                    setEditingId(conversation.id)
                  }}
                >
                  {conversation.title || t.chat.newConversation}
                </p>
              )}
              <p className="text-[10px] text-text-muted mt-0.5">
                {conversation.messages.length} {t.chat.messages}
              </p>
            </div>
            <div className="flex items-center gap-0.5">
              <button
                onClick={(event) => {
                  event.stopPropagation()
                  setEditingTitle(conversation.title || t.chat.newConversation)
                  setEditingId(conversation.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-accent-primary rounded transition-all"
                title={t.chat.renameConversation}
              >
                <Pencil className="w-3 h-3" />
              </button>
              <button
                onClick={(event) => {
                  event.stopPropagation()
                  onDelete(conversation.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-accent-danger rounded transition-all"
                title={t.chat.deleteConversation}
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
