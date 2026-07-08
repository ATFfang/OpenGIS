import { describe, expect, it } from 'vitest'
import type { UIMessage } from '@/types/chat'
import { messagePartsForRender, withProjectedMessageParts } from '@/services/chatMessageParts'

describe('chatMessageParts', () => {
  it('projects legacy assistant text into MessagePart[]', () => {
    const message: UIMessage = {
      ts: 1000,
      type: 'say',
      say: 'text',
      text: 'hello',
      partial: true,
    }

    const projected = withProjectedMessageParts(message)

    expect(projected.parts).toHaveLength(1)
    expect(projected.parts?.[0]).toMatchObject({
      id: '1000:text',
      type: 'text',
      status: 'streaming',
      text: 'hello',
    })
  })

  it('refreshes legacy projected parts when the legacy message changes', () => {
    const message = withProjectedMessageParts({
      ts: 2000,
      type: 'say',
      say: 'code',
      text: 'print("a")',
      stepNumber: 3,
      partial: true,
    })

    const updated = withProjectedMessageParts({
      ...message,
      text: 'print("ab")',
      partial: false,
    })

    expect(messagePartsForRender(updated)[0]).toMatchObject({
      id: '2000:code:3',
      type: 'code',
      status: 'completed',
      text: 'print("ab")',
    })
  })

  it('keeps native non-legacy parts untouched', () => {
    const message: UIMessage = {
      ts: 3000,
      type: 'say',
      say: 'text',
      text: 'legacy text',
      parts: [
        {
          id: 'native',
          type: 'text',
          status: 'completed',
          text: 'native text',
          data: { source: 'event-log' },
        },
      ],
    }

    expect(withProjectedMessageParts(message).parts?.[0]).toMatchObject({
      id: 'native',
      text: 'native text',
    })
  })
})

