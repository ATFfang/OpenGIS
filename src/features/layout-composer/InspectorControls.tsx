export function ColorControl({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="text-[11px] text-text-muted">
      {label}
      <div className="mt-1 flex h-7 items-center gap-1 rounded-md border border-border bg-bg-primary px-1">
        <input
          type="color"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="h-5 w-5 border-0 bg-transparent p-0"
        />
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-xs text-text-primary outline-none"
        />
      </div>
    </label>
  )
}

export function NumberControl({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <label className="text-[11px] text-text-muted">
      {label}
      <input
        type="number"
        value={Number(value.toFixed(step < 1 ? 2 : 0))}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 h-7 w-full rounded-md border border-border bg-bg-primary px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}
