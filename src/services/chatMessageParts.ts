import type { MessagePart, ChatMessage } from '@/types/chat'

export function messagePartsForRender(message: ChatMessage): MessagePart[] {
  return message.parts ?? []
}

export function upsertMessagePart(message: ChatMessage, incoming: MessagePart): ChatMessage {
  const current = message.parts ?? []
  const index = current.findIndex((part) => part.id === incoming.id)
  if (index < 0) {
    return { ...message, parts: [...current, incoming] }
  }
  const existing = current[index]
  const merged: MessagePart = {
    ...existing,
    ...incoming,
    text: mergePartText(existing, incoming),
    data: {
      ...(existing.data ?? {}),
      ...(incoming.data ?? {}),
    },
  }
  return {
    ...message,
    parts: [
      ...current.slice(0, index),
      merged,
      ...current.slice(index + 1),
    ],
  }
}

function mergePartText(existing: MessagePart, incoming: MessagePart): string | undefined {
  const next = incoming.text ?? ''
  if (!next) return existing.text
  if (incoming.status === 'streaming') return `${existing.text ?? ''}${next}`
  return next
}
