export type LayoutElementType = 'map-frame' | 'scale-bar' | 'north-arrow' | 'legend' | 'text'
export type LayoutElementVariant =
  | 'default'
  | 'minimal'
  | 'boxed'
  | 'alternating'
  | 'double-line'
  | 'classic'
  | 'triangle'
  | 'compass'
  | 'panel'

export interface LayoutElementStyle {
  variant?: LayoutElementVariant
  fillColor?: string
  strokeColor?: string
  strokeWidth?: number
  opacity?: number
  backgroundColor?: string
  backgroundOpacity?: number
  borderColor?: string
  borderWidth?: number
  borderRadius?: number
  textColor?: string
  fontSize?: number
  fontWeight?: number
  padding?: number
}

export interface LayoutMapView {
  x: number
  y: number
  scale: number
}

export interface LayoutPage {
  id: string
  name: string
  widthMm: number
  heightMm: number
  background: string
}

export interface LayoutElementFrame {
  x: number
  y: number
  width: number
  height: number
}

export interface LayoutElement {
  id: string
  type: LayoutElementType
  label: string
  frame: LayoutElementFrame
  style?: LayoutElementStyle
  mapView?: LayoutMapView
  locked?: boolean
  visible?: boolean
  props?: Record<string, unknown>
}

export interface LayoutTemplate {
  page: LayoutPage
  elements: LayoutElement[]
}

export interface LayoutExportOptions {
  pixelRatio: number
  fileName: string
  autoDownload?: boolean
}

export interface LayoutExportResult {
  blob: Blob
  width: number
  height: number
  fileName: string
}
