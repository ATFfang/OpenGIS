/**
 * DialogHost — renders the currently-active dialog.
 *
 * Mount this once near the root of the app (inside <App/>, after
 * global providers but above any surface that might trigger a
 * dialog). It subscribes to the module-level controller and
 * renders a `<PromptDialog/>`, `<ConfirmDialog/>`, or
 * `<AlertDialog/>` depending on the request kind. Only one dialog
 * is visible at a time.
 *
 * Visual conventions: inherits the `bg-bg-secondary / border /
 * accent-primary` token palette used by the context menus and
 * Layers panel, so nothing here needs bespoke styling.
 */
import { forwardRef, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Info, X, AlertCircle } from 'lucide-react'
import {
  __subscribe,
  __resolveAlert,
  __resolveConfirm,
  __resolvePrompt,
  type AlertOptions,
  type ConfirmOptions,
  type DialogRequest,
  type PromptOptions,
} from './dialogController'

// ─── Host ─────────────────────────────────────────────────────────

export function DialogHost() {
  const [req, setReq] = useState<DialogRequest | null>(null)

  useEffect(() => {
    return __subscribe(setReq)
  }, [])

  if (!req) return null

  return (
    <Backdrop onDismiss={() => dismiss(req)}>
      {req.kind === 'prompt' && (
        <PromptView key={req.id} options={req.options} />
      )}
      {req.kind === 'confirm' && (
        <ConfirmView key={req.id} options={req.options} />
      )}
      {req.kind === 'alert' && (
        <AlertView key={req.id} options={req.options} />
      )}
    </Backdrop>
  )
}

// ─── Backdrop ────────────────────────────────────────────────────

function Backdrop({
  children,
  onDismiss,
}: {
  children: React.ReactNode
  onDismiss: () => void
}) {
  // Click on the backdrop itself cancels the dialog; clicks on the
  // card bubble stop before they reach us.
  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onDismiss()
  }

  return (
    <div
      onMouseDown={handleBackdropClick}
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm animate-fade-in"
      role="presentation"
    >
      {children}
    </div>
  )
}

function dismiss(req: DialogRequest) {
  if (req.kind === 'prompt') __resolvePrompt(null)
  else if (req.kind === 'confirm') __resolveConfirm(false)
  else __resolveAlert()
}

// ─── Prompt ──────────────────────────────────────────────────────

function PromptView({ options }: { options: PromptOptions }) {
  const [value, setValue] = useState(options.defaultValue ?? '')
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Autofocus + select-all on mount so defaultValue behaves like
  // window.prompt did.
  useEffect(() => {
    const el = inputRef.current
    if (el) {
      el.focus()
      el.select()
    }
  }, [])

  const handleSubmit = () => {
    const err = options.validate?.(value) ?? null
    if (err) {
      setError(err)
      return
    }
    __resolvePrompt(value)
  }

  const handleCancel = () => {
    __resolvePrompt(null)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleSubmit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      handleCancel()
    }
  }

  return (
    <Card onKeyDown={handleKeyDown}>
      <Header title={options.title} onClose={handleCancel} />
      {options.message && (
        <p className="px-4 pt-3 text-xs text-text-secondary">{options.message}</p>
      )}
      <div className="px-4 py-3">
        <input
          ref={inputRef}
          type="text"
          value={value}
          placeholder={options.placeholder}
          onChange={(e) => {
            setValue(e.target.value)
            if (error) setError(null)
          }}
          className="
            w-full h-8 px-2.5 text-xs text-text-primary
            bg-bg-primary border border-border rounded
            focus:outline-none focus:border-accent-primary
            placeholder:text-text-muted
            transition-colors
          "
        />
        {error && (
          <p className="mt-1.5 text-2xs text-accent-danger flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            {error}
          </p>
        )}
      </div>
      <Footer>
        <Button onClick={handleCancel} variant="ghost">
          {options.cancelLabel ?? 'Cancel'}
        </Button>
        <Button onClick={handleSubmit} variant="primary">
          {options.okLabel ?? 'OK'}
        </Button>
      </Footer>
    </Card>
  )
}

// ─── Confirm ─────────────────────────────────────────────────────

function ConfirmView({ options }: { options: ConfirmOptions }) {
  const okRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    okRef.current?.focus()
  }, [])

  const handleOk = () => __resolveConfirm(true)
  const handleCancel = () => __resolveConfirm(false)

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      handleCancel()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      handleOk()
    }
  }

  return (
    <Card onKeyDown={handleKeyDown}>
      <Header
        title={options.title}
        onClose={handleCancel}
        icon={
          options.danger ? (
            <AlertTriangle className="w-4 h-4 text-accent-danger" />
          ) : null
        }
      />
      {options.message && (
        <p className="px-4 py-3 text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">
          {options.message}
        </p>
      )}
      <Footer>
        <Button onClick={handleCancel} variant="ghost">
          {options.cancelLabel ?? 'Cancel'}
        </Button>
        <Button
          ref={okRef}
          onClick={handleOk}
          variant={options.danger ? 'danger' : 'primary'}
        >
          {options.okLabel ?? 'OK'}
        </Button>
      </Footer>
    </Card>
  )
}

// ─── Alert ───────────────────────────────────────────────────────

function AlertView({ options }: { options: AlertOptions }) {
  const okRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    okRef.current?.focus()
  }, [])

  const handleOk = () => __resolveAlert()

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape' || e.key === 'Enter') {
      e.preventDefault()
      handleOk()
    }
  }

  const icon =
    options.severity === 'error' ? (
      <AlertCircle className="w-4 h-4 text-accent-danger" />
    ) : options.severity === 'warning' ? (
      <AlertTriangle className="w-4 h-4 text-accent-warning" />
    ) : (
      <Info className="w-4 h-4 text-accent-primary" />
    )

  return (
    <Card onKeyDown={handleKeyDown}>
      <Header title={options.title} onClose={handleOk} icon={icon} />
      {options.message && (
        <p className="px-4 py-3 text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">
          {options.message}
        </p>
      )}
      <Footer>
        <Button ref={okRef} onClick={handleOk} variant="primary">
          {options.okLabel ?? 'OK'}
        </Button>
      </Footer>
    </Card>
  )
}

// ─── Shared building blocks ──────────────────────────────────────

function Card({
  children,
  onKeyDown,
}: {
  children: React.ReactNode
  onKeyDown: (e: React.KeyboardEvent) => void
}) {
  // Stop mousedown from bubbling so backdrop clicks don't dismiss
  // when the user merely clicks inside the card.
  return (
    <div
      onKeyDown={onKeyDown}
      onMouseDown={(e) => e.stopPropagation()}
      role="dialog"
      aria-modal="true"
      className="
        min-w-[320px] max-w-[440px] w-[90vw]
        bg-bg-secondary border border-border rounded-lg
        panel-shadow animate-slide-up
        flex flex-col
      "
    >
      {children}
    </div>
  )
}

function Header({
  title,
  onClose,
  icon,
}: {
  title: string
  onClose: () => void
  icon?: React.ReactNode
}) {
  return (
    <div className="h-10 border-b border-border flex items-center px-3 gap-2">
      {icon}
      <span className="text-xs font-semibold text-text-primary flex-1 truncate">
        {title}
      </span>
      <button
        onClick={onClose}
        className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
        title="Close"
        tabIndex={-1}
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

function Footer({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-11 border-t border-border flex items-center justify-end gap-2 px-3">
      {children}
    </div>
  )
}

// Button keeps the ref plumbing lightweight so dialogs can focus
// their primary action on mount. React 18 requires forwardRef for
// function components that accept refs.
interface ButtonProps {
  onClick: () => void
  children: React.ReactNode
  variant: 'primary' | 'danger' | 'ghost'
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ onClick, children, variant }, ref) => {
    const base =
      'h-7 px-3 rounded text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1'
    const styles: Record<ButtonProps['variant'], string> = {
      primary:
        'bg-accent-primary text-white hover:bg-accent-primary/90 focus-visible:ring-accent-primary',
      danger:
        'bg-accent-danger text-white hover:bg-accent-danger/90 focus-visible:ring-accent-danger',
      ghost:
        'text-text-secondary hover:text-text-primary hover:bg-bg-hover focus-visible:ring-border',
    }
    return (
      <button
        ref={ref}
        onClick={onClick}
        className={`${base} ${styles[variant]}`}
      >
        {children}
      </button>
    )
  }
)
Button.displayName = 'DialogButton'
