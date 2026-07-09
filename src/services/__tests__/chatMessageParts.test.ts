import { describe, expect, it } from 'vitest'
import type { UIMessage } from '@/types/chat'
import {
  messagePartsForRender,
  upsertMessagePart,
} from '@/services/chatMessageParts'

describe('chatMessageParts', () => {
  it('renders only native MessagePart[] from the message envelope', () => {
    const message: UIMessage = {
      ts: 1000,
      type: 'say',
      say: 'text',
      text: 'legacy envelope text',
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

    expect(messagePartsForRender(message)).toEqual(message.parts)
  })

  it('does not synthesize fallback parts for legacy envelope-only messages', () => {
    const message: UIMessage = {
      ts: 2000,
      type: 'say',
      say: 'text',
      text: 'old text',
      partial: true,
    }

    expect(messagePartsForRender(message)).toEqual([])
  })

  it('upserts native streaming parts by stable id', () => {
    const message: UIMessage = {
      ts: 4000,
      type: 'say',
      say: 'text',
      text: '',
    }

    const first = upsertMessagePart(message, {
      id: 'run:text:final',
      type: 'text',
      status: 'streaming',
      text: 'hel',
    })
    const second = upsertMessagePart(first, {
      id: 'run:text:final',
      type: 'text',
      status: 'streaming',
      text: 'lo',
    })
    const done = upsertMessagePart(second, {
      id: 'run:text:final',
      type: 'text',
      status: 'completed',
      data: { finished: true },
    })

    expect(messagePartsForRender(done)).toHaveLength(1)
    expect(messagePartsForRender(done)[0]).toMatchObject({
      id: 'run:text:final',
      type: 'text',
      status: 'completed',
      text: 'hello',
      data: { finished: true },
    })
  })

  it('keeps reasoning and text as separate native parts', () => {
    const message: UIMessage = {
      ts: 5000,
      type: 'say',
      say: 'reasoning',
      text: '',
    }

    const withReasoning = upsertMessagePart(message, {
      id: 'run:reasoning:1',
      type: 'reasoning',
      status: 'streaming',
      text: 'reasoning summary',
    })
    const withText = upsertMessagePart(withReasoning, {
      id: 'run:text:final',
      type: 'text',
      status: 'streaming',
      text: 'final answer',
    })

    expect(messagePartsForRender(withText)).toHaveLength(2)
    expect(messagePartsForRender(withText)[0]).toMatchObject({
      id: 'run:reasoning:1',
      type: 'reasoning',
      text: 'reasoning summary',
    })
    expect(messagePartsForRender(withText)[1]).toMatchObject({
      id: 'run:text:final',
      type: 'text',
      text: 'final answer',
    })
  })
})
