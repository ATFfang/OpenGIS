export function colorWithOpacity(color: string, opacity: number): string {
  const alpha = Math.max(0, Math.min(1, opacity))
  const hex = color.trim()
  const shortHex = /^#([0-9a-f]{3})$/i.exec(hex)
  if (shortHex) {
    const [r, g, b] = shortHex[1].split('').map((part) => parseInt(part + part, 16))
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  const fullHex = /^#([0-9a-f]{6})$/i.exec(hex)
  if (fullHex) {
    const value = fullHex[1]
    const r = parseInt(value.slice(0, 2), 16)
    const g = parseInt(value.slice(2, 4), 16)
    const b = parseInt(value.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  return alpha >= 1 ? color : color
}
