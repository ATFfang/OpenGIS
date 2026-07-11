import { create } from 'zustand'
import type { PivotTarget } from '@/features/pivot/types'

interface PivotStore {
  isOpen: boolean
  target: PivotTarget | null
  mode: 'data' | 'agent'
  open: (target: PivotTarget) => void
  close: () => void
  setMode: (mode: 'data' | 'agent') => void
}

export const usePivotStore = create<PivotStore>((set) => ({
  isOpen: false,
  target: null,
  mode: 'data',
  open: (target) => set({ isOpen: true, target, mode: 'data' }),
  close: () => set({ isOpen: false, target: null, mode: 'data' }),
  setMode: (mode) => set({ mode }),
}))
