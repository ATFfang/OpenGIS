import { useState, useCallback, useRef, useEffect } from 'react'
import { Eye, EyeOff } from 'lucide-react'

// ─── VSCode-style Setting Item Components ──────────────────────

interface SettingItemProps {
  id: string
  label: string
  description?: string
  children: React.ReactNode
  modified?: boolean
}

/**
 * A single setting row — mirrors VSCode's setting item layout:
 * Label (bold) + description (muted) + control widget.
 */
export function SettingItem({ id, label, description, children, modified }: SettingItemProps) {
  return (
    <div className="py-3 px-1 group" id={`setting-${id}`}>
      <div className="flex items-start gap-2">
        {modified && (
          <div className="w-1.5 h-1.5 rounded-full bg-accent-primary mt-2 shrink-0" title="Modified" />
        )}
        <div className="flex-1 min-w-0">
          <label
            htmlFor={id}
            className="text-sm font-medium text-text-primary cursor-pointer select-none"
          >
            {label}
          </label>
          {description && (
            <p className="text-xs text-text-muted mt-0.5 leading-relaxed">{description}</p>
          )}
          <div className="mt-2">{children}</div>
        </div>
      </div>
    </div>
  )
}

/* --- Text / Password Input --- */

interface SettingInputProps {
  id: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: 'text' | 'password' | 'number'
  className?: string
}

export function SettingInput({
  id,
  value,
  onChange,
  placeholder,
  type = 'text',
  className = '',
}: SettingInputProps) {
  const [showPassword, setShowPassword] = useState(false)
  const isPassword = type === 'password'

  return (
    <div className="relative max-w-[400px]">
      <input
        id={id}
        type={isPassword && !showPassword ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`
          w-full h-[30px] px-2.5 text-sm
          bg-bg-tertiary text-text-primary
          border border-border rounded
          outline-none
          focus:border-accent-primary
          placeholder:text-text-muted
          transition-colors
          ${isPassword ? 'pr-8' : ''}
          ${className}
        `}
        autoComplete="off"
        spellCheck={false}
      />
      {isPassword && (
        <button
          type="button"
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 text-text-muted hover:text-text-secondary transition-colors"
          tabIndex={-1}
        >
          {showPassword ? (
            <EyeOff className="w-3.5 h-3.5" />
          ) : (
            <Eye className="w-3.5 h-3.5" />
          )}
        </button>
      )}
    </div>
  )
}

/* --- Number Input --- */

interface SettingNumberProps {
  id: string
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  step?: number
}

export function SettingNumber({ id, value, onChange, min, max, step = 1 }: SettingNumberProps) {
  return (
    <input
      id={id}
      type="number"
      value={value}
      onChange={(e) => {
        const v = parseFloat(e.target.value)
        if (!isNaN(v)) onChange(v)
      }}
      min={min}
      max={max}
      step={step}
      className="
        w-[120px] h-[30px] px-2.5 text-sm
        bg-bg-tertiary text-text-primary
        border border-border rounded
        outline-none
        focus:border-accent-primary
        transition-colors
        [appearance:textfield]
        [&::-webkit-outer-spin-button]:appearance-none
        [&::-webkit-inner-spin-button]:appearance-none
      "
    />
  )
}

/* --- Select Dropdown --- */

interface SettingSelectProps {
  id: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  className?: string
}

export function SettingSelect({ id, value, onChange, options, className = '' }: SettingSelectProps) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`
        h-[30px] px-2 pr-7 text-sm
        bg-bg-tertiary text-text-primary
        border border-border rounded
        outline-none cursor-pointer
        focus:border-accent-primary
        transition-colors
        appearance-none
        bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%2212%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%239aa0ad%22%20stroke-width%3D%222%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E')]
        bg-[length:12px] bg-[right_6px_center] bg-no-repeat
        ${className}
      `}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}

/* --- Checkbox / Toggle --- */

interface SettingCheckboxProps {
  id: string
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
}

export function SettingCheckbox({ id, checked, onChange, label }: SettingCheckboxProps) {
  return (
    <label htmlFor={id} className="inline-flex items-center gap-2 cursor-pointer select-none">
      <div className="relative">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="sr-only peer"
        />
        <div
          className="
            w-[18px] h-[18px] rounded-[3px]
            border border-border
            bg-bg-tertiary
            peer-checked:bg-accent-primary peer-checked:border-accent-primary
            peer-focus-visible:ring-2 peer-focus-visible:ring-accent-primary/30
            transition-all duration-150
            flex items-center justify-center
          "
        >
          {checked && (
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path
                d="M2.5 6L5 8.5L9.5 3.5"
                stroke="white"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>
      </div>
      {label && <span className="text-sm text-text-secondary">{label}</span>}
    </label>
  )
}

/* --- Slider --- */

interface SettingSliderProps {
  id: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number
  showValue?: boolean
  valueFormatter?: (v: number) => string
}

export function SettingSlider({
  id,
  value,
  onChange,
  min,
  max,
  step = 1,
  showValue = true,
  valueFormatter,
}: SettingSliderProps) {
  const pct = ((value - min) / (max - min)) * 100

  return (
    <div className="flex items-center gap-3 max-w-[400px]">
      <input
        id={id}
        type="range"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        min={min}
        max={max}
        step={step}
        className="
          flex-1 h-1 appearance-none cursor-pointer rounded-full
          bg-bg-hover
          [&::-webkit-slider-thumb]:appearance-none
          [&::-webkit-slider-thumb]:w-3
          [&::-webkit-slider-thumb]:h-3
          [&::-webkit-slider-thumb]:rounded-full
          [&::-webkit-slider-thumb]:bg-accent-primary
          [&::-webkit-slider-thumb]:border-0
          [&::-webkit-slider-thumb]:cursor-pointer
          [&::-webkit-slider-thumb]:transition-transform
          [&::-webkit-slider-thumb]:hover:scale-125
        "
        style={{
          background: `linear-gradient(to right, var(--accent-primary) ${pct}%, var(--bg-hover) ${pct}%)`,
        }}
      />
      {showValue && (
        <span className="text-xs text-text-muted w-10 text-right tabular-nums">
          {valueFormatter ? valueFormatter(value) : value}
        </span>
      )}
    </div>
  )
}

/* --- TextArea --- */

interface SettingTextAreaProps {
  id: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  rows?: number
}

export function SettingTextArea({
  id,
  value,
  onChange,
  placeholder,
  rows = 4,
}: SettingTextAreaProps) {
  return (
    <textarea
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={rows}
      className="
        w-full max-w-[500px] px-2.5 py-2 text-sm
        bg-bg-tertiary text-text-primary
        border border-border rounded
        outline-none resize-y
        focus:border-accent-primary
        placeholder:text-text-muted
        transition-colors
        font-mono leading-relaxed
      "
      spellCheck={false}
    />
  )
}

/* --- Section Header --- */

interface SettingSectionProps {
  title: string
  children: React.ReactNode
}

export function SettingSection({ title, children }: SettingSectionProps) {
  return (
    <div className="mb-6">
      <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-1 px-1">
        {title}
      </h3>
      <div className="border-t border-border">
        {children}
      </div>
    </div>
  )
}
