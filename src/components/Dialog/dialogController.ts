/**
 * Dialog controller — imperative API for prompt/confirm/alert.
 *
 * Why not React Context?
 * ------------------------
 * A Context-based API would force consumers to use a hook, which
 * doesn't compose well with imperative flows like
 * `if (!await confirm(...)) return` inside callbacks. Worse, every
 * context value update re-renders the subtree. Following the
 * pattern popularised by libraries like `sonner` and
 * `react-hot-toast`, we expose a module-level singleton that
 * pushes state into a `<DialogHost>` mounted once near the root.
 *
 * The `useDialog()` hook is purely ergonomic — it returns the same
 * singleton but matches the idiomatic React import style. Both
 * forms are valid:
 *
 *   const { confirm } = useDialog()
 *   const { confirm } = dialog          // identical
 *
 * The host subscribes via `subscribe()` and renders whatever the
 * store's `current` field points at.
 */
import { useMemo } from 'react'

// ─── Types ────────────────────────────────────────────────────────

export interface PromptOptions {
  title: string
  message?: string
  defaultValue?: string
  placeholder?: string
  /** Primary button label (default: "OK"). */
  okLabel?: string
  /** Cancel button label (default: "Cancel"). */
  cancelLabel?: string
  /** Validation hook — return a non-empty string to show an error and keep the dialog open. */
  validate?: (value: string) => string | null
}

export interface ConfirmOptions {
  title: string
  message?: string
  okLabel?: string
  cancelLabel?: string
  /** Style the primary button as destructive (red). */
  danger?: boolean
}

export interface AlertOptions {
  title: string
  message?: string
  okLabel?: string
  /** Optional severity — colours the header icon. */
  severity?: 'info' | 'warning' | 'error'
}

// ─── Internal request shape ──────────────────────────────────────

export type DialogRequest =
  | {
      id: number
      kind: 'prompt'
      options: PromptOptions
      resolve: (v: string | null) => void
    }
  | {
      id: number
      kind: 'confirm'
      options: ConfirmOptions
      resolve: (v: boolean) => void
    }
  | {
      id: number
      kind: 'alert'
      options: AlertOptions
      resolve: () => void
    }

// ─── Store ────────────────────────────────────────────────────────

type Listener = (req: DialogRequest | null) => void

let seq = 0
let current: DialogRequest | null = null
const listeners = new Set<Listener>()

function emit() {
  for (const l of listeners) l(current)
}

function enqueue(req: DialogRequest) {
  if (listeners.size === 0) {
    if (req.kind === 'prompt') req.resolve(null)
    else if (req.kind === 'confirm') req.resolve(false)
    else req.resolve()
    return
  }
  // The host renders one dialog at a time. If a second `confirm`
  // fires while one is open we overwrite — this matches the
  // browser's native behaviour (the second call blocks until the
  // first finishes, but since we're non-blocking we simply resolve
  // the old one as cancelled and show the new one). In practice
  // this should rarely happen; callers are expected to `await`.
  if (current) {
    // Cancel the previous request gracefully.
    if (current.kind === 'prompt') current.resolve(null)
    else if (current.kind === 'confirm') current.resolve(false)
    else current.resolve()
  }
  current = req
  emit()
}

function close(result: { kind: 'prompt'; value: string | null } |
                       { kind: 'confirm'; value: boolean } |
                       { kind: 'alert' }) {
  const req = current
  if (!req) return
  current = null
  emit()
  if (req.kind === 'prompt' && result.kind === 'prompt') req.resolve(result.value)
  else if (req.kind === 'confirm' && result.kind === 'confirm') req.resolve(result.value)
  else if (req.kind === 'alert' && result.kind === 'alert') req.resolve()
  else {
    // Type mismatch — treat as cancellation. Shouldn't happen.
    if (req.kind === 'prompt') req.resolve(null)
    else if (req.kind === 'confirm') req.resolve(false)
    else req.resolve()
  }
}

// ─── Public controller ───────────────────────────────────────────

export const dialog = {
  prompt(options: PromptOptions): Promise<string | null> {
    return new Promise((resolve) => {
      enqueue({ id: ++seq, kind: 'prompt', options, resolve })
    })
  },
  confirm(options: ConfirmOptions): Promise<boolean> {
    return new Promise((resolve) => {
      enqueue({ id: ++seq, kind: 'confirm', options, resolve })
    })
  },
  alert(options: AlertOptions): Promise<void> {
    return new Promise((resolve) => {
      enqueue({ id: ++seq, kind: 'alert', options, resolve })
    })
  },
}

// ─── Host-internal API (not exported via index.ts) ───────────────

export function __subscribe(listener: Listener): () => void {
  listeners.add(listener)
  // Flush current state to the new listener so StrictMode's
  // double-mount still sees the active request.
  listener(current)
  return () => {
    listeners.delete(listener)
  }
}

export function __resolvePrompt(value: string | null) {
  close({ kind: 'prompt', value })
}

export function __resolveConfirm(value: boolean) {
  close({ kind: 'confirm', value })
}

export function __resolveAlert() {
  close({ kind: 'alert' })
}

// ─── Hook ─────────────────────────────────────────────────────────

/**
 * Returns a stable reference to the dialog controller. Safe to
 * destructure inside effects and callbacks — the returned object
 * is identical across renders.
 */
export function useDialog() {
  // `dialog` is a module-level singleton so a simple memoised
  // passthrough keeps the hook API idiomatic without forcing a
  // Context provider on consumers.
  return useMemo(() => dialog, [])
}
