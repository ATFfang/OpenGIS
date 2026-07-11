import type { LayoutElement, LayoutPage } from './types'

export function buildScaleBarMetrics(
  element: LayoutElement,
  page: LayoutPage | undefined,
  fallbackScaleDenominator: number,
): { labels: string[]; widthRatio: number; maxDistanceMeters: number } {
  const auto = element.props?.autoLabel !== false
  if (!auto || !page) {
    return {
      labels: parseScaleBarLabels(String(element.props?.label ?? '0        5        10 km')),
      widthRatio: 1,
      maxDistanceMeters: 0,
    }
  }

  const scaleDenominator = Number(element.props?.scaleDenominator ?? fallbackScaleDenominator)
  const widthMm = page.widthMm * (element.frame.width / 100)
  const availableMeters = Math.max(0, (widthMm * scaleDenominator) / 1000)
  const maxDistanceMeters = niceDistance(availableMeters)
  const widthRatio = availableMeters > 0 ? Math.max(0.2, Math.min(1, maxDistanceMeters / availableMeters)) : 1
  const segments = Math.max(1, Math.min(8, Number(element.props?.segments ?? 4)))
  const labels = Array.from({ length: segments + 1 }, (_, index) =>
    formatDistance((maxDistanceMeters / segments) * index),
  )
  return { labels, widthRatio, maxDistanceMeters }
}

function niceDistance(maxMeters: number): number {
  if (!Number.isFinite(maxMeters) || maxMeters <= 0) return 0
  const exponent = Math.floor(Math.log10(maxMeters))
  const base = Math.pow(10, exponent)
  for (const multiple of [5, 2, 1]) {
    const candidate = multiple * base
    if (candidate <= maxMeters) return candidate
  }
  return base / 2
}

function formatDistance(meters: number): string {
  if (meters >= 1000) {
    const km = meters / 1000
    return `${formatCompactNumber(km, km >= 10 ? 1 : 2)} km`
  }
  return `${Math.round(meters)} m`
}

function formatCompactNumber(value: number, maxFractionDigits: number): string {
  return value.toFixed(maxFractionDigits).replace(/\.?0+$/, '')
}

function parseScaleBarLabels(label: string): string[] {
  const parts = label.trim().split(/\s+/).filter(Boolean)
  if (parts.length <= 1) return parts.length === 1 ? parts : ['0', '5', '10 km']
  const last = parts[parts.length - 1]
  const previous = parts[parts.length - 2]
  if (!looksNumeric(last) && looksNumeric(previous)) {
    return [...parts.slice(0, -2), `${previous} ${last}`]
  }
  return parts
}

function looksNumeric(value: string): boolean {
  return /^[-+]?\d+(?:\.\d+)?(?:,\d{3})*$/.test(value)
}
