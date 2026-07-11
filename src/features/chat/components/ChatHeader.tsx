import { type RefObject } from 'react'
import { History, Plus, Search } from 'lucide-react'
import { useChatStore } from '@/stores/chatStore'
import { useT } from '@/i18n'
import type { ChatMessage } from '@/types/chat'
import appIconImg from '../../../../resources/icons/app-icon.png'
import { ConversationListDropdown } from './ConversationListDropdown'

interface ChatHeaderProps {
  variant: 'default' | 'floating'
  hasTask: boolean
  isStreaming: boolean
  conversation: { id: string; title: string; messages: ChatMessage[]; updatedAt: number } | null
  conversations: { id: string; title: string; messages: ChatMessage[]; updatedAt: number }[]
  activeConversationId: string | null
  isEditingTitle: boolean
  editingTitle: string
  titleInputRef: RefObject<HTMLInputElement>
  searchOpen: boolean
  showConversationList: boolean
  onEditingTitleChange: (value: boolean) => void
  onEditingTitleTextChange: (value: string) => void
  onShowConversationListChange: (value: boolean) => void
  onSelectConversation: (id: string) => void
  onCreateConversation: () => void
  onFocusSearch: () => void
}

export function ChatHeader({
  variant,
  hasTask,
  isStreaming,
  conversation,
  conversations,
  activeConversationId,
  isEditingTitle,
  editingTitle,
  titleInputRef,
  searchOpen,
  showConversationList,
  onEditingTitleChange,
  onEditingTitleTextChange,
  onShowConversationListChange,
  onSelectConversation,
  onCreateConversation,
  onFocusSearch,
}: ChatHeaderProps) {
  const t = useT()

  const finishRename = () => {
    const trimmed = editingTitle.trim()
    if (trimmed && activeConversationId) {
      useChatStore.getState().renameConversation(activeConversationId, trimmed)
    }
    onEditingTitleChange(false)
  }

  return (
    <header className={`app-region-drag shrink-0 bg-[var(--chat-header-bg)] backdrop-blur-sm relative z-50 ${
      variant === 'floating' ? 'rounded-t-2xl' : 'border-b border-border'
    }`}>
      <div className="flex h-11 items-center justify-between gap-2 px-4">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <div className="relative shrink-0">
            <div className="w-7 h-7 rounded-lg overflow-hidden flex items-center justify-center">
              <img src={appIconImg} alt="OpenGIS" className="w-7 h-7 object-contain" />
            </div>
            {isStreaming && (
              <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-accent-success ring-2 ring-[var(--chat-header-bg)] animate-pulse" />
            )}
          </div>
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            {isEditingTitle && hasTask ? (
              <form
                className="flex items-center gap-1"
                onSubmit={(event) => {
                  event.preventDefault()
                  finishRename()
                }}
              >
                <input
                  ref={titleInputRef}
                  className="app-region-no-drag text-[13px] font-semibold text-text-primary leading-tight bg-bg-secondary border border-border rounded px-1.5 py-0.5 outline-none focus:border-accent-primary w-[160px]"
                  value={editingTitle}
                  onChange={(event) => onEditingTitleTextChange(event.target.value)}
                  onBlur={finishRename}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') onEditingTitleChange(false)
                  }}
                  autoFocus
                />
              </form>
            ) : (
              <span
                className="app-region-no-drag block max-w-full truncate whitespace-nowrap text-[13px] font-semibold text-text-primary leading-tight cursor-pointer hover:text-accent-primary transition-colors"
                onDoubleClick={() => {
                  if (hasTask && conversation) {
                    onEditingTitleTextChange(conversation.title || t.chat.newConversation)
                    onEditingTitleChange(true)
                  }
                }}
                title={t.chat.doubleClickRename}
              >
                {hasTask ? (conversation?.title || t.chat.newConversation) : 'OpenGIS Agent'}
              </span>
            )}
            <span className="block max-w-full truncate whitespace-nowrap text-[10px] text-text-muted leading-tight mt-0.5">
              {isStreaming ? (
                <span className="text-accent-primary font-medium">{t.chat.progress.generating}</span>
              ) : (
                t.chat.poweredByLLM
              )}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {conversations.length > 1 && (
            <div className="relative">
              <button
                onClick={() => onShowConversationListChange(!showConversationList)}
                className="p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-all duration-150"
                title={t.chat.conversationHistory}
              >
                <History className="w-4 h-4" />
              </button>
              {showConversationList && (
                <ConversationListDropdown
                  conversations={conversations}
                  activeId={activeConversationId}
                  onSelect={(id) => {
                    onSelectConversation(id)
                    onShowConversationListChange(false)
                  }}
                  onDelete={(id) => {
                    useChatStore.getState().deleteConversation(id)
                  }}
                  onClose={() => onShowConversationListChange(false)}
                />
              )}
            </div>
          )}
          {hasTask && (
            <button
              onClick={onFocusSearch}
              className={`p-1.5 rounded-lg transition-all duration-150 ${
                searchOpen
                  ? 'text-accent-primary bg-accent-primary/10'
                  : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
              }`}
              title="搜索对话"
            >
              <Search className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={onCreateConversation}
            className="p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-all duration-150"
            title={t.chat.newConversation}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  )
}
