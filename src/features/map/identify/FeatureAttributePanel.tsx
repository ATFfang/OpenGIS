/**
 * FeatureAttributePanel — A floating panel that displays feature attributes
 * when the user clicks on map features in identify mode.
 *
 * Design:
 * - Dark glass aesthetic matching the project's design language
 * - Floating panel anchored to bottom-right of the map
 * - Supports multi-feature pagination
 * - Smooth animations and transitions
 * - Responsive scrollable attribute table
 */
import { useState, useEffect, useMemo } from 'react'
import {
  X,
  ChevronLeft,
  ChevronRight,
  MapPin,
  Layers,
  Copy,
  Check,
} from 'lucide-react'
import { useT } from '@/i18n'

// ─── Types ──────────────────────────────────────────────────────────

export interface FeatureInfo {
  /** Layer display name */
  layerName: string
  /** Geometry type (Point, LineString, Polygon, etc.) */
  geometryType: string
  /** Feature properties (key-value pairs) */
  properties: Record<string, any>
  /** Optional coordinates for point features */
  coordinates?: [number, number]
}

interface FeatureAttributePanelProps {
  /** Array of identified features */
  features: FeatureInfo[]
  /** Callback when panel is closed */
  onClose: () => void
  /** Whether the panel is visible */
  visible: boolean
}

// ─── Geometry type badge colors ─────────────────────────────────────

const GEOM_COLORS: Record<string, string> = {
  Point: 'bg-accent-primary/20 text-blue-400',
  MultiPoint: 'bg-accent-primary/20 text-blue-400',
  LineString: 'bg-accent-success/20 text-green-400',
  MultiLineString: 'bg-accent-success/20 text-green-400',
  Polygon: 'bg-accent-warning/20 text-amber-400',
  MultiPolygon: 'bg-accent-warning/20 text-amber-400',
}

// ─── Value formatting ───────────────────────────────────────────────

function formatValue(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    return v.toFixed(6).replace(/\.?0+$/, '')
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'string') {
    return v.length > 120 ? v.slice(0, 117) + '…' : v
  }
  try {
    const s = JSON.stringify(v)
    return s.length > 120 ? s.slice(0, 117) + '…' : s
  } catch {
    return String(v)
  }
}

// ─── Component ──────────────────────────────────────────────────────

export function FeatureAttributePanel({
  features,
  onClose,
  visible,
}: FeatureAttributePanelProps) {
  const t = useT()
  const [currentIndex, setCurrentIndex] = useState(0)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  // Reset index when features change
  useEffect(() => {
    setCurrentIndex(0)
  }, [features])

  // Current feature
  const feature = features[currentIndex] ?? null

  // Filter out internal/cluster properties
  const filteredProps = useMemo(() => {
    if (!feature) return []
    return Object.entries(feature.properties).filter(
      ([k]) =>
        !k.startsWith('_') &&
        k !== 'cluster' &&
        k !== 'cluster_id' &&
        k !== 'point_count' &&
        k !== 'point_count_abbreviated',
    )
  }, [feature])

  // Copy value to clipboard
  const handleCopy = (key: string, value: any) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    navigator.clipboard.writeText(text).then(() => {
      setCopiedKey(key)
      setTimeout(() => setCopiedKey(null), 1500)
    })
  }

  if (!visible || !feature) return null

  const geomColorClass =
    GEOM_COLORS[feature.geometryType] ?? 'bg-bg-tertiary text-text-muted'

  return (
    <div className="absolute bottom-4 right-4 z-20 animate-slide-up">
      <div
        className="
          w-[340px] max-h-[420px]
          glass rounded-xl panel-shadow
          flex flex-col overflow-hidden
          border border-border
        "
      >
        {/* ─── Header ─────────────────────────────────────────── */}
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border shrink-0">
          <div className="w-6 h-6 rounded-md bg-accent-primary/15 flex items-center justify-center">
            <MapPin className="w-3.5 h-3.5 text-accent-primary" />
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-xs font-semibold text-text-primary truncate">
              {feature.layerName}
            </h3>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${geomColorClass}`}
              >
                {feature.geometryType}
              </span>
              {feature.coordinates && (
                <span className="text-[10px] text-text-muted font-mono">
                  {feature.coordinates[1].toFixed(4)},{' '}
                  {feature.coordinates[0].toFixed(4)}
                </span>
              )}
            </div>
          </div>

          <button
            onClick={onClose}
            className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
            title={t.map.close}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* ─── Pagination (multi-feature) ─────────────────────── */}
        {features.length > 1 && (
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-bg-tertiary/50 shrink-0">
            <button
              onClick={() =>
                setCurrentIndex((i) => (i - 1 + features.length) % features.length)
              }
              className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
              title={t.map.previousFeature}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>

            <div className="flex items-center gap-1.5">
              <Layers className="w-3 h-3 text-text-muted" />
              <span className="text-[11px] text-text-secondary font-medium">
                {currentIndex + 1}{' '}
                <span className="text-text-muted">/ {features.length}</span>
              </span>
            </div>

            <button
              onClick={() =>
                setCurrentIndex((i) => (i + 1) % features.length)
              }
              className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-primary hover:bg-accent-primary/10 transition-colors"
              title={t.map.nextFeature}
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ─── Attribute Table ────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {filteredProps.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-text-muted">
              <div className="w-10 h-10 rounded-full bg-bg-tertiary flex items-center justify-center mb-2">
                <MapPin className="w-4 h-4" />
              </div>
              <span className="text-xs">{t.map.noAttributes}</span>
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10">
                <tr className="bg-bg-secondary border-b border-border">
                  <th className="text-left px-3 py-1.5 text-text-muted font-medium w-[38%]">
                    {t.map.field}
                  </th>
                  <th className="text-left px-3 py-1.5 text-text-muted font-medium">
                    {t.map.value}
                  </th>
                  <th className="w-7" />
                </tr>
              </thead>
              <tbody>
                {filteredProps.map(([key, value], idx) => (
                  <tr
                    key={key}
                    className={`
                      group transition-colors hover:bg-bg-hover/50
                      ${idx % 2 === 0 ? 'bg-transparent' : 'bg-bg-tertiary/30'}
                    `}
                  >
                    <td className="px-3 py-1.5 text-text-secondary font-medium break-all align-top">
                      {key}
                    </td>
                    <td className="px-3 py-1.5 text-text-primary break-words align-top font-mono text-[11px]">
                      {formatValue(value)}
                    </td>
                    <td className="pr-2 align-top pt-1.5">
                      <button
                        onClick={() => handleCopy(key, value)}
                        className="w-5 h-5 rounded flex items-center justify-center text-text-muted/0 group-hover:text-text-muted hover:!text-accent-primary hover:bg-accent-primary/10 transition-all"
                        title={t.map.copyValue}
                      >
                        {copiedKey === key ? (
                          <Check className="w-3 h-3 text-accent-success" />
                        ) : (
                          <Copy className="w-3 h-3" />
                        )}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ─── Footer ─────────────────────────────────────────── */}
        <div className="px-3 py-1.5 border-t border-border bg-bg-tertiary/30 shrink-0">
          <span className="text-[10px] text-text-muted">
            {filteredProps.length} {t.map.fields}
            {features.length > 1 && ` · ${features.length} ${t.map.featuresAtPoint}`}
          </span>
        </div>
      </div>
    </div>
  )
}
