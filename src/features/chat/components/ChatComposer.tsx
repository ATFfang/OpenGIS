import { type ClipboardEvent, type KeyboardEvent, type RefObject } from 'react'
import { FileText, GitBranch, Paperclip, Send, Square, Wrench, X, Zap } from 'lucide-react'
import type { ChatAttachment } from '@/stores/chatStore'
import { useT } from '@/i18n'
import { AttachPanel } from './AttachPanel'

interface ChatComposerProps {
  inputValue: string
  attachments: ChatAttachment[]
  showAttachPanel: boolean
  isBusy: boolean
  isCancelling: boolean
  textAreaRef: RefObject<HTMLTextAreaElement>
  onInputChange: (value: string) => void
  onTextAreaResize: () => void
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  onPaste: (event: ClipboardEvent<HTMLTextAreaElement>) => void
  onSend: () => void
  onStop: () => void
  onToggleAttachPanel: () => void
  onCloseAttachPanel: () => void
  onAttachFile: () => void
  onAttachWorkflow: (entry: { path: string; name: string }) => void
  onAttachToolGroup: (name: string, groups: string[]) => void
  onRemoveAttachment: (index: number) => void
}

export function ChatComposer({
  inputValue,
  attachments,
  showAttachPanel,
  isBusy,
  isCancelling,
  textAreaRef,
  onInputChange,
  onTextAreaResize,
  onKeyDown,
  onPaste,
  onSend,
  onStop,
  onToggleAttachPanel,
  onCloseAttachPanel,
  onAttachFile,
  onAttachWorkflow,
  onAttachToolGroup,
  onRemoveAttachment,
}: ChatComposerProps) {
  const t = useT()

  return (
    <div className="p-3 pt-2 relative">
      <div className="relative bg-bg-secondary rounded-2xl border border-border focus-within:border-accent-primary/40 focus-within:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all duration-200">
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2 pb-0">
            {attachments.map((attachment, index) => (
              <div
                key={`${attachment.path}-${index}`}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-accent-primary/10 border border-accent-primary/25 text-[11px] text-text-secondary group"
              >
                {attachment.type === 'workflow' ? (
                  <GitBranch className="w-3 h-3 text-accent-primary shrink-0" />
                ) : attachment.type === 'tool_group' ? (
                  <Wrench className="w-3 h-3 text-amber-400 shrink-0" />
                ) : (
                  <FileText className="w-3 h-3 text-text-muted shrink-0" />
                )}
                <span className="max-w-[120px] truncate">{attachment.name}</span>
                <button
                  onClick={() => onRemoveAttachment(index)}
                  className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-text-muted/60 hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
                >
                  <X className="w-2.5 h-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={textAreaRef}
          value={inputValue}
          onChange={(event) => {
            onInputChange(event.target.value)
            onTextAreaResize()
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          spellCheck={false}
          placeholder={t.chat.placeholder}
          rows={1}
          className="w-full bg-transparent text-[13px] text-text-primary placeholder:text-text-muted/50 py-2.5 pl-4 pr-24 resize-none outline-none max-h-[200px] overflow-y-auto leading-6 align-middle"
          style={{
            minHeight: '44px',
            scrollbarWidth: 'none',
            fontFamily: 'inherit',
          }}
        />

        <div className="absolute right-2 bottom-2 flex items-center gap-0.5">
          <button
            onClick={onToggleAttachPanel}
            className={`p-2 rounded-lg transition-all duration-150 ${
              showAttachPanel || attachments.length > 0
                ? 'text-accent-primary bg-accent-primary/10'
                : 'text-text-muted/40 hover:text-text-secondary hover:bg-bg-hover'
            }`}
            title={t.chat.attachFile}
          >
            <Paperclip className="w-4 h-4" />
          </button>

          {isBusy ? (
            <button
              onClick={onStop}
              disabled={isCancelling}
              className="p-2 bg-accent-danger/10 text-accent-danger hover:bg-accent-danger/20 disabled:opacity-50 disabled:cursor-wait rounded-lg transition-all duration-150 ring-1 ring-accent-danger/20"
              title={t.chat.stopGeneration}
            >
              <Square className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              onClick={onSend}
              disabled={isBusy || (!inputValue.trim() && attachments.length === 0)}
              className="p-2 bg-accent-primary text-white hover:bg-accent-primary/90 disabled:bg-bg-tertiary disabled:text-text-muted/30 disabled:cursor-not-allowed rounded-lg transition-all duration-150 shadow-sm disabled:shadow-none"
              title={t.chat.send}
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {showAttachPanel && (
        <AttachPanel
          onAttachFile={onAttachFile}
          onAttachWorkflow={onAttachWorkflow}
          onAttachToolGroup={onAttachToolGroup}
          attachedToolGroups={attachments.filter((attachment) => attachment.type === 'tool_group').map((attachment) => attachment.name)}
          onClose={onCloseAttachPanel}
        />
      )}

      <div className="flex items-center justify-between mt-1.5 px-1">
        <span className="text-[10px] text-text-muted/40">
          <kbd className="px-1 py-0.5 bg-bg-tertiary/50 rounded text-[9px] font-mono">↵</kbd> {t.chat.sendHint}
          <span className="mx-1.5">·</span>
          <kbd className="px-1 py-0.5 bg-bg-tertiary/50 rounded text-[9px] font-mono">⇧↵</kbd> {t.chat.newLineHint}
        </span>
        <span className="text-[10px] text-text-muted/40 flex items-center gap-1">
          {isBusy ? (
            <>
              <Zap className="w-2.5 h-2.5 text-accent-primary" />
              <span className="text-accent-primary">{isCancelling ? t.chat.cancelling : t.chat.streaming}</span>
            </>
          ) : (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-accent-success/60" />
              {t.chat.ready}
            </>
          )}
        </span>
      </div>
    </div>
  )
}
