/**
 * groupMessages —— 让"一轮 agent 回答"只画一个机器人头像的分组逻辑。
 *
 * 关键场景：
 *   - 纯 assistant 消息（reasoning → code → code_result → text）合并成一组
 *   - user 消息每条独立一组
 *   - system（api_req_started / api_req_finished）独立一组
 *   - user → assistant → user → assistant → user → assistant 的多轮对话
 *     groups 数 == user 组数 + assistant 组数
 */

import { describe, expect, it } from 'vitest'
import type { UIMessage, SayType, AskType } from '@/types/chat'
import { groupMessages, roleOf } from '../groupMessages'

let tsCounter = 1_700_000_000_000
function msg(opts: {
  say?: SayType
  ask?: AskType
  text?: string
  partial?: boolean
}): UIMessage {
  tsCounter += 1
  return {
    ts: tsCounter,
    type: opts.ask ? 'ask' : 'say',
    say: opts.say,
    ask: opts.ask,
    text: opts.text,
    partial: opts.partial,
  }
}

describe('roleOf', () => {
  it('classifies user / system / assistant correctly', () => {
    expect(roleOf(msg({ say: 'user_feedback', text: 'hi' }))).toBe('user')
    expect(roleOf(msg({ ask: 'followup', text: '?' }))).toBe('user')
    expect(roleOf(msg({ say: 'api_req_started' }))).toBe('system')
    expect(roleOf(msg({ say: 'api_req_finished' }))).toBe('system')
    expect(roleOf(msg({ say: 'text', text: 'ok' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'reasoning', text: '...' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'code', text: 'print(1)' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'code_result', text: '1' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'tool', text: 'add_layer' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'error', text: 'boom' }))).toBe('assistant')
    expect(roleOf(msg({ say: 'completion_result', text: 'done' }))).toBe('assistant')
  })
})

describe('groupMessages', () => {
  it('returns [] for empty input', () => {
    expect(groupMessages([])).toEqual([])
  })

  it('merges a full assistant turn (reasoning → code → code_result → text) into ONE group', () => {
    const messages = [
      msg({ say: 'user_feedback', text: '加载 csv' }),
      msg({ say: 'reasoning', text: 'Let me think...' }),
      msg({ say: 'code', text: 'add_layer("data.csv")' }),
      msg({ say: 'code_result', text: '{"layer_id": "L1"}' }),
      msg({ say: 'text', text: '已经加载完成。' }),
    ]
    const groups = groupMessages(messages)

    // 1 user + 1 merged assistant = 2 groups
    expect(groups).toHaveLength(2)
    expect(groups[0].role).toBe('user')
    expect(groups[0].items).toHaveLength(1)
    expect(groups[1].role).toBe('assistant')
    // 关键：4 条 assistant 消息全合并到同一组（同一个头像）
    expect(groups[1].items).toHaveLength(4)
    expect(groups[1].items.map((m) => m.say)).toEqual([
      'reasoning',
      'code',
      'code_result',
      'text',
    ])
  })

  it('keeps user messages in separate groups (does not merge consecutive user messages)', () => {
    const messages = [
      msg({ say: 'user_feedback', text: 'hi' }),
      msg({ say: 'user_feedback', text: 'another' }),
    ]
    const groups = groupMessages(messages)
    // 两条 user 消息各自独立，不合并
    expect(groups).toHaveLength(2)
    expect(groups.every((g) => g.role === 'user' && g.items.length === 1)).toBe(true)
  })

  it('keeps system messages isolated from assistant groups', () => {
    // text → api_req_started → text：system 条把 assistant 切成两组
    const messages = [
      msg({ say: 'text', text: 'hello' }),
      msg({ say: 'api_req_started', text: '{}' }),
      msg({ say: 'text', text: 'world' }),
    ]
    const groups = groupMessages(messages)
    expect(groups.map((g) => g.role)).toEqual(['assistant', 'system', 'assistant'])
    expect(groups[0].items).toHaveLength(1)
    expect(groups[1].items).toHaveLength(1)
    expect(groups[2].items).toHaveLength(1)
  })

  it('handles multi-round conversation (U → A → U → A)', () => {
    const messages = [
      msg({ say: 'user_feedback', text: 'q1' }),
      msg({ say: 'reasoning', text: 'think1' }),
      msg({ say: 'text', text: 'ans1' }),
      msg({ say: 'user_feedback', text: 'q2' }),
      msg({ say: 'code', text: 'do2' }),
      msg({ say: 'code_result', text: 'out2' }),
      msg({ say: 'text', text: 'ans2' }),
    ]
    const groups = groupMessages(messages)
    expect(groups.map((g) => g.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(groups[1].items).toHaveLength(2) // reasoning + text
    expect(groups[3].items).toHaveLength(3) // code + code_result + text
  })

  it('assistant → user_feedback breaks the group (even if next is assistant again)', () => {
    const messages = [
      msg({ say: 'text', text: 'a1' }),
      msg({ say: 'user_feedback', text: 'interrupt' }),
      msg({ say: 'text', text: 'a2' }),
    ]
    const groups = groupMessages(messages)
    expect(groups).toHaveLength(3)
    expect(groups[0].items[0].text).toBe('a1')
    expect(groups[2].items[0].text).toBe('a2')
  })

  it('preserves message order inside each group', () => {
    const m1 = msg({ say: 'reasoning', text: '1' })
    const m2 = msg({ say: 'code', text: '2' })
    const m3 = msg({ say: 'code_result', text: '3' })
    const groups = groupMessages([m1, m2, m3])
    expect(groups).toHaveLength(1)
    expect(groups[0].items).toEqual([m1, m2, m3])
  })
})
