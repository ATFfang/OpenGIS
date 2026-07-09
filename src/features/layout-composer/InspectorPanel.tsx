import { Trash2 } from 'lucide-react'
import type { MapLayerDefinition } from '@/services/geo'
import { ColorControl, NumberControl } from './InspectorControls'
import type { LayoutElement, LayoutElementFrame, LayoutElementStyle, LayoutElementType, LayoutMapView } from './types'

export function InspectorPanel({
  element,
  layers,
  onFrameChange,
  onPropsChange,
  onStyleChange,
  onVariantChange,
  onMapViewChange,
  onSplitLegend,
  onRemove,
}: {
  element: LayoutElement | null
  layers: MapLayerDefinition[]
  onFrameChange: (frame: Partial<LayoutElementFrame>) => void
  onPropsChange: (props: Record<string, unknown>) => void
  onStyleChange: (style: Partial<LayoutElementStyle>) => void
  onVariantChange: (variant: NonNullable<LayoutElementStyle['variant']>) => void
  onMapViewChange: (mapView: Partial<LayoutMapView>) => void
  onSplitLegend: (layerIds: string[]) => void
  onRemove: () => void
}) {
  const style = element?.style ?? {}
  const mapView = element?.mapView ?? { x: 0, y: 0, scale: 1 }
  const variants = element ? variantsForElement(element.type) : []

  return (
    <div className="w-64 border-l border-border bg-bg-secondary/70 shrink-0">
      <div className="h-10 border-b border-border px-3 flex items-center justify-between">
        <div className="text-xs font-medium">属性</div>
        {element && (
          <button
            onClick={onRemove}
            className="w-7 h-7 rounded-md text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 flex items-center justify-center"
            title="删除元素"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {!element ? (
        <div className="p-3 text-xs text-text-muted">选择一个画布元素。</div>
      ) : (
        <div className="p-3 space-y-4 overflow-y-auto h-[calc(100%-40px)]">
          <div>
            <div className="text-xs font-medium">{element.label}</div>
            <div className="text-[11px] text-text-muted mt-0.5">{element.type}</div>
          </div>

          {variants.length > 0 && (
            <label className="block text-[11px] text-text-muted">
              样式
              <select
                value={style.variant ?? variants[0]}
                onChange={(event) => onVariantChange(event.target.value as NonNullable<LayoutElementStyle['variant']>)}
                className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
              >
                {variants.map((variant) => (
                  <option key={variant} value={variant}>{variant}</option>
                ))}
              </select>
            </label>
          )}

          <div className="grid grid-cols-2 gap-2">
            {(['x', 'y', 'width', 'height'] as const).map((key) => (
              <label key={key} className="text-[11px] text-text-muted">
                <span className="uppercase">{key}</span>
                <input
                  type="number"
                  value={Number(element.frame[key].toFixed(1))}
                  onChange={(event) => onFrameChange({ [key]: Number(event.target.value) })}
                  className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
                />
              </label>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <ColorControl
              label={element.type === 'legend' || element.type === 'map-frame' ? '背景' : '填充'}
              value={style.fillColor ?? style.backgroundColor ?? '#ffffff'}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { backgroundColor: value }
                : { fillColor: value })}
            />
            {(element.type === 'scale-bar' || element.type === 'north-arrow') && (
              <ColorControl
                label="背景"
                value={style.backgroundColor ?? '#ffffff'}
                onChange={(value) => onStyleChange({ backgroundColor: value })}
              />
            )}
            <ColorControl
              label="线/边框"
              value={style.strokeColor ?? style.borderColor ?? '#111827'}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { borderColor: value }
                : { strokeColor: value })}
            />
            <ColorControl
              label="文字"
              value={style.textColor ?? '#111827'}
              onChange={(value) => onStyleChange({ textColor: value })}
            />
            <NumberControl
              label="透明度"
              value={style.opacity ?? 1}
              min={0}
              max={1}
              step={0.05}
              onChange={(value) => onStyleChange({ opacity: value })}
            />
            {(element.type === 'scale-bar' || element.type === 'north-arrow' || element.type === 'legend') && (
              <NumberControl
                label="背景透明"
                value={style.backgroundOpacity ?? (element.type === 'legend' ? style.opacity ?? 0.94 : 0)}
                min={0}
                max={1}
                step={0.05}
                onChange={(value) => onStyleChange({ backgroundOpacity: value })}
              />
            )}
            <NumberControl
              label="线宽"
              value={style.strokeWidth ?? style.borderWidth ?? 1}
              min={0}
              max={12}
              step={0.5}
              onChange={(value) => onStyleChange(element.type === 'legend' || element.type === 'map-frame'
                ? { borderWidth: value }
                : { strokeWidth: value })}
            />
            <NumberControl
              label="字号"
              value={style.fontSize ?? (element.type === 'text' ? 18 : 10)}
              min={6}
              max={48}
              step={1}
              onChange={(value) => onStyleChange({ fontSize: value })}
            />
            <NumberControl
              label="圆角"
              value={style.borderRadius ?? 0}
              min={0}
              max={24}
              step={1}
              onChange={(value) => onStyleChange({ borderRadius: value })}
            />
            <NumberControl
              label="内边距"
              value={style.padding ?? 8}
              min={0}
              max={32}
              step={1}
              onChange={(value) => onStyleChange({ padding: value })}
            />
          </div>

          {element.type === 'map-frame' && (
            <div>
              <div className="text-[11px] text-text-muted mb-2">内部地图</div>
              <div className="grid grid-cols-3 gap-2">
                <NumberControl label="X" value={mapView.x} min={-100} max={100} step={1} onChange={(value) => onMapViewChange({ x: value })} />
                <NumberControl label="Y" value={mapView.y} min={-100} max={100} step={1} onChange={(value) => onMapViewChange({ y: value })} />
                <NumberControl label="缩放" value={mapView.scale} min={0.12} max={8} step={0.05} onChange={(value) => onMapViewChange({ scale: value })} />
              </div>
            </div>
          )}

          {element.type === 'scale-bar' && (
            <div className="space-y-2">
              <label className="h-7 flex items-center gap-2 text-[11px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={element.props?.autoLabel !== false}
                  onChange={(event) => onPropsChange({ autoLabel: event.target.checked })}
                />
                自动按地图比例计算标注
              </label>
              <label className="block text-[11px] text-text-muted">
                标注
                <input
                  value={String(element.props?.label ?? '')}
                  onChange={(event) => onPropsChange({ label: event.target.value })}
                  disabled={element.props?.autoLabel !== false}
                  className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
                />
              </label>
              <NumberControl
                label="分段"
                value={Number(element.props?.segments ?? 4)}
                min={1}
                max={8}
                step={1}
                onChange={(value) => onPropsChange({ segments: Math.round(value) })}
              />
            </div>
          )}

          {element.type === 'legend' && (
            <LegendInspector
              element={element}
              layers={layers}
              onPropsChange={onPropsChange}
              onSplitLegend={onSplitLegend}
            />
          )}

          {element.type === 'text' && (
            <label className="block text-[11px] text-text-muted">
              文本
              <input
                value={String(element.props?.text ?? '')}
                onChange={(event) => onPropsChange({ text: event.target.value })}
                className="mt-1 h-7 w-full rounded-md bg-bg-primary border border-border px-2 text-xs text-text-primary outline-none focus:border-accent-primary"
              />
            </label>
          )}
        </div>
      )}
    </div>
  )
}

function variantsForElement(type: LayoutElementType): NonNullable<LayoutElementStyle['variant']>[] {
  if (type === 'map-frame') return ['default', 'boxed', 'minimal']
  if (type === 'scale-bar') return ['alternating', 'double-line', 'minimal']
  if (type === 'north-arrow') return ['classic', 'triangle', 'compass']
  if (type === 'legend') return ['panel', 'minimal', 'boxed']
  return []
}

function LegendInspector({
  element,
  layers,
  onPropsChange,
  onSplitLegend,
}: {
  element: LayoutElement
  layers: MapLayerDefinition[]
  onPropsChange: (props: Record<string, unknown>) => void
  onSplitLegend: (layerIds: string[]) => void
}) {
  const selectedLayerIds = Array.isArray(element.props?.layerIds)
    ? element.props.layerIds.filter((id): id is string => typeof id === 'string')
    : []
  const selected = new Set(selectedLayerIds)
  const setLayerSelected = (layerId: string, checked: boolean) => {
    const next = checked
      ? [...selectedLayerIds, layerId]
      : selectedLayerIds.filter((id) => id !== layerId)
    onPropsChange({ layerIds: next })
  }

  return (
    <div className="space-y-2">
      <label className="h-7 flex items-center gap-2 text-[11px] text-text-secondary">
        <input
          type="checkbox"
          checked={element.props?.grouped !== false}
          onChange={(event) => onPropsChange({ grouped: event.target.checked })}
        />
        组合为一个图例
      </label>
      <div className="rounded-md border border-border bg-bg-primary/70 p-2">
        <div className="text-[11px] text-text-muted mb-2">选择图层</div>
        <div className="space-y-1 max-h-36 overflow-y-auto">
          {layers.map((layer) => (
            <label key={layer.id} className="h-6 flex items-center gap-2 text-[11px] text-text-secondary">
              <input
                type="checkbox"
                checked={selected.has(layer.id)}
                onChange={(event) => setLayerSelected(layer.id, event.target.checked)}
              />
              <span className="truncate">{layer.name}</span>
              <span className="ml-auto text-[10px] text-text-muted">{layer.style.renderType}</span>
            </label>
          ))}
          {layers.length === 0 && (
            <div className="text-[11px] text-text-muted">当前没有可用图层。</div>
          )}
        </div>
      </div>
      <button
        disabled={selectedLayerIds.length === 0}
        onClick={() => onSplitLegend(selectedLayerIds)}
        className="h-7 w-full rounded-md border border-border bg-bg-primary text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50"
      >
        拆分为独立图例
      </button>
    </div>
  )
}
