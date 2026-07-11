import type { MapLayerDefinition } from '@/services/geo'
import { scaleLayoutValue } from './layoutMetrics'
import { colorWithOpacity } from './layoutColor'
import { buildLegendSections } from './layoutLegend'
import { buildScaleBarMetrics } from './layoutScaleBar'
import type { LayoutElement, LayoutPage } from './types'

export function renderElementContent(
  element: LayoutElement,
  mapSnapshotUrl: string | null,
  layers: MapLayerDefinition[],
  editingMapFrame = false,
  layoutScale = 1,
  page?: LayoutPage,
  mapScaleDenominator = 50000,
) {
  const style = element.style ?? {}
  const opacity = style.opacity ?? 1
  const background = colorWithOpacity(style.backgroundColor ?? '#ffffff', style.backgroundOpacity ?? 0)
  const borderWidth = scaleLayoutValue(style.borderWidth ?? 0, layoutScale)
  const padding = scaleLayoutValue(style.padding ?? 0, layoutScale)
  if (element.type === 'map-frame') {
    const mapView = element.mapView ?? { x: 0, y: 0, scale: 1 }
    const variant = style.variant ?? 'default'
    return (
      <div
        className="w-full h-full overflow-hidden"
        style={{
          background: style.backgroundColor ?? '#dbeafe',
          border: `${scaleLayoutValue(style.borderWidth ?? 1, layoutScale)}px solid ${style.borderColor ?? '#111827'}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? (variant === 'boxed' ? 4 : 0), layoutScale),
          opacity,
        }}
      >
        {mapSnapshotUrl ? (
          <img
            src={mapSnapshotUrl}
            alt="Map snapshot"
            className="w-full h-full object-cover"
            style={{
              transform: `translate(${mapView.x}%, ${mapView.y}%) scale(${mapView.scale})`,
              transformOrigin: 'center',
              transition: editingMapFrame ? 'none' : 'transform 120ms ease',
            }}
            draggable={false}
          />
        ) : (
          <div className="w-full h-full bg-sky-100 flex items-center justify-center text-slate-500 text-xs">
            Map snapshot unavailable
          </div>
        )}
      </div>
    )
  }
  if (element.type === 'scale-bar') {
    const variant = style.variant ?? 'alternating'
    const strokeWidth = scaleLayoutValue(style.strokeWidth ?? 2, layoutScale)
    const color = style.strokeColor ?? '#111827'
    const fill = style.fillColor ?? color
    const textColor = style.textColor ?? color
    const segments = Number(element.props?.segments ?? 4)
    const metrics = buildScaleBarMetrics(element, page, mapScaleDenominator)
    const labels = metrics.labels
    const fontSize = scaleLayoutValue(style.fontSize ?? 10, layoutScale)
    const tickHeight = Math.max(3, scaleLayoutValue(8, layoutScale))
    return (
      <div
        className="w-full h-full relative"
        style={{
          background,
          border: `${borderWidth}px solid ${style.borderColor ?? color}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: textColor,
          opacity,
          padding,
        }}
      >
        <div
          className="absolute left-0 right-0"
          style={{
            width: `${metrics.widthRatio * 100}%`,
            bottom: fontSize + scaleLayoutValue(4, layoutScale),
            height: '42%',
            borderBottom: variant === 'boxed' ? undefined : `${strokeWidth}px solid ${color}`,
          }}
        >
          {variant === 'alternating' && (
            <div
              className="absolute left-0 right-0 bottom-0 flex"
              style={{ height: tickHeight }}
            >
              {Array.from({ length: segments }).map((_, index) => (
                <span
                  key={index}
                  className="flex-1 border"
                  style={{
                    borderColor: color,
                    background: index % 2 === 0 ? fill : 'transparent',
                  }}
                />
              ))}
            </div>
          )}
          {variant !== 'alternating' && Array.from({ length: segments + 1 }).map((_, index) => (
            <div
              key={index}
              className="absolute"
              style={{
                bottom: -tickHeight / 2,
                height: tickHeight,
                left: `${(100 / segments) * index}%`,
                borderLeft: `${strokeWidth}px solid ${color}`,
              }}
            />
          ))}
          {variant === 'double-line' && (
            <div
              className="absolute left-0 right-0"
              style={{ bottom: '35%', borderBottom: `${strokeWidth}px solid ${color}` }}
            />
          )}
        </div>
        {labels.map((label, index) => {
          const x = labels.length === 1 ? 50 : (index / (labels.length - 1)) * 100
          return (
            <span
              key={`${label}-${index}`}
              className="absolute bottom-0 leading-none whitespace-nowrap"
              style={{
                left: `${x}%`,
                fontSize,
                transform:
                  labels.length === 1 ? 'translateX(-50%)' :
                  index === 0 ? 'none' :
                  index === labels.length - 1 ? 'translateX(-100%)' :
                  'translateX(-50%)',
              }}
            >
              {label}
            </span>
          )
        })}
      </div>
    )
  }
  if (element.type === 'north-arrow') {
    const variant = style.variant ?? 'classic'
    const fill = style.fillColor ?? '#111827'
    const textColor = style.textColor ?? fill
    return (
      <div
        className="w-full h-full flex flex-col items-center justify-center"
        style={{
          background,
          border: `${borderWidth}px solid ${style.borderColor ?? fill}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: fill,
          opacity,
          padding,
        }}
      >
        <svg viewBox="0 0 48 64" className="w-full h-[78%]" aria-hidden>
          {variant === 'compass' ? (
            <>
              <circle cx="24" cy="30" r="18" fill="none" stroke="currentColor" strokeWidth="3" />
              <path d="M24 4 L31 30 L24 56 L17 30 Z" fill="currentColor" />
              <path d="M8 30 L24 24 L40 30 L24 36 Z" fill="rgba(255,255,255,0.75)" />
            </>
          ) : variant === 'triangle' ? (
            <path d="M24 2 L44 52 L24 40 L4 52 Z" fill="currentColor" />
          ) : (
            <>
              <path d="M24 2 L44 46 L24 36 L4 46 Z" fill="currentColor" />
              <path d="M24 10 L24 36 L10 43 Z" fill="rgba(255,255,255,0.7)" />
            </>
          )}
        </svg>
        <div
          className="font-semibold leading-none"
          style={{ color: textColor, fontSize: scaleLayoutValue(style.fontSize ?? 12, layoutScale) }}
        >
          N
        </div>
      </div>
    )
  }
  if (element.type === 'legend') {
    const legendSections = buildLegendSections(layers, element)
    const legendFontSize = scaleLayoutValue(style.fontSize ?? 10, layoutScale)
    const legendPadding = scaleLayoutValue(style.padding ?? 8, layoutScale)
    const swatchSize = scaleLayoutValue(12, layoutScale)
    return (
      <div
        className="w-full h-full overflow-hidden"
        style={{
          background: colorWithOpacity(
            style.backgroundColor ?? '#ffffff',
            style.backgroundOpacity ?? (style.opacity ?? 0.94),
          ),
          border: `${scaleLayoutValue(style.borderWidth ?? 1, layoutScale)}px solid ${style.borderColor ?? '#d1d5db'}`,
          borderRadius: scaleLayoutValue(style.borderRadius ?? 0, layoutScale),
          color: style.textColor ?? '#111827',
          opacity: style.opacity ?? 1,
          padding: legendPadding,
          fontSize: legendFontSize,
        }}
      >
        <div className="font-semibold" style={{ marginBottom: scaleLayoutValue(8, layoutScale) }}>Legend</div>
        <div className="flex flex-col" style={{ gap: scaleLayoutValue(6, layoutScale) }}>
          {legendSections.map((section) => (
            <div key={section.layerId} className="min-w-0">
              {section.showTitle && (
                <div className="font-medium truncate" style={{ fontSize: legendFontSize }}>
                  {section.title}
                </div>
              )}
              <div className="flex flex-col" style={{ gap: scaleLayoutValue(4, layoutScale), marginTop: section.showTitle ? scaleLayoutValue(4, layoutScale) : 0 }}>
                {section.entries.map((entry) => (
                  <div key={`${section.layerId}-${entry.label}`} className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="border border-neutral-700 shrink-0"
                      style={{ background: entry.color, width: swatchSize, height: swatchSize }}
                    />
                    <span className="truncate" style={{ fontSize: legendFontSize }}>{entry.label}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {legendSections.length === 0 && (
            <div style={{ fontSize: legendFontSize, color: '#737373' }}>选择图层生成图例</div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div
      className="w-full h-full flex items-center overflow-hidden"
      style={{
        color: style.textColor ?? '#111827',
        fontSize: scaleLayoutValue(style.fontSize ?? 18, layoutScale),
        fontWeight: style.fontWeight ?? 600,
        opacity,
      }}
    >
      {String(element.props?.text ?? 'Map Title')}
    </div>
  )
}
