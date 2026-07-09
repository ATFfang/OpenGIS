/**
 * Chat 消息分组工具 —— 让"一轮 agent 回答"只画一个机器人头像。
 *
 * Python 侧 agent runtime 一轮完整回答会依次推多条消息：
 *   reasoning → code → code_result → text(final_answer) / tool
 * 之前 ChatView 每条都套一个 MessageWrapper 画头像，用户会误以为"机器人回答了 N 次"。
 *
 * 本模块把消息按"发言方"聚合成 Group：
 *   - user_feedback                        → role='user'（每条独立一组）
 *   - 其它 assistant 产出                   → 与前一条 assistant 合并
 *
 * 纯函数，无副作用，单元可测。
 */

import type { UIMessage } from '@/types/chat'

export type MessageRole = 'user' | 'assistant' | 'system'

export interface MessageGroupData {
  role: MessageRole
  items: UIMessage[]
}

export function roleOf(msg: UIMessage): MessageRole {
  if (msg.say === 'user_feedback') return 'user'
  return 'assistant'
}

export function groupMessages(messages: UIMessage[]): MessageGroupData[] {
  const groups: MessageGroupData[] = []
  for (const msg of messages) {
    const role = roleOf(msg)
    const last = groups[groups.length - 1]
    // 只有 assistant 才把连续的消息合并；user / system 每条独立一组。
    if (role === 'assistant' && last && last.role === 'assistant') {
      last.items.push(msg)
    } else {
      groups.push({ role, items: [msg] })
    }
  }
  return groups
}
