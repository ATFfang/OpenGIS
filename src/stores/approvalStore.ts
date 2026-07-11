import { create } from 'zustand'

export type ApprovalKind = 'code' | 'confirm' | 'choose' | 'text'

export interface ApprovalRequestInput {
  kind: ApprovalKind
  title: string
  message?: string
  requestId?: string
  toolName?: string
  danger?: boolean
  timeoutSeconds?: number
  runId?: string
  step?: number
  code?: string
  risks?: string[]
  options?: string[]
  placeholder?: string
  defaultValue?: string
}

export interface ApprovalRequest extends ApprovalRequestInput {
  id: number
  createdAt: number
  resolve: (result: ApprovalResult) => void
}

export interface ApprovalResult {
  approved?: boolean
  answer?: string | null
}

interface ApprovalStore {
  current: ApprovalRequest | null
  queue: ApprovalRequest[]
  inlineHostCount: number
  request: (input: ApprovalRequestInput) => Promise<ApprovalResult>
  resolveCurrent: (result: ApprovalResult) => void
  registerInlineHost: () => void
  unregisterInlineHost: () => void
  clear: () => void
}

let seq = 0

export const useApprovalStore = create<ApprovalStore>((set, get) => ({
  current: null,
  queue: [],
  inlineHostCount: 0,

  request: (input) => {
    return new Promise((resolve) => {
      const req: ApprovalRequest = {
        ...input,
        id: ++seq,
        createdAt: Date.now(),
        resolve,
      }
      set((state) => {
        if (!state.current) {
          return { current: req }
        }
        return { queue: [...state.queue, req] }
      })
    })
  },

  resolveCurrent: (result) => {
    const current = get().current
    if (!current) return
    current.resolve(result)
    set((state) => {
      const [next, ...rest] = state.queue
      return {
        current: next ?? null,
        queue: rest,
      }
    })
  },

  registerInlineHost: () => {
    set((state) => ({ inlineHostCount: state.inlineHostCount + 1 }))
  },

  unregisterInlineHost: () => {
    set((state) => ({ inlineHostCount: Math.max(0, state.inlineHostCount - 1) }))
  },

  clear: () => {
    const { current, queue } = get()
    current?.resolve({ approved: false, answer: null })
    for (const req of queue) {
      req.resolve({ approved: false, answer: null })
    }
    set({ current: null, queue: [] })
  },
}))
