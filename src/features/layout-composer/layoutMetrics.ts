import type { LayoutPage } from './types'

export function getLayoutDesignWidth(page: LayoutPage): number {
  return page.widthMm >= page.heightMm ? 920 : 640
}

export function getLayoutAspect(page: LayoutPage): number {
  return page.widthMm / page.heightMm
}

export function scaleLayoutValue(value: number, layoutScale: number): number {
  return value * layoutScale
}
