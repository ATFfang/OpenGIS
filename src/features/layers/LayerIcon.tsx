/**
 * LayerIcon — renders a small geometry-type icon with the layer's color.
 * Used in the layer panel to visually distinguish layer types.
 */
import type { GeometryType } from '@/services/geo'

interface LayerIconProps {
  geometryType?: GeometryType
  color: string
  className?: string
  size?: number
}

export function LayerIcon({ geometryType, color, className = '', size = 14 }: LayerIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 14 14"
      className={className}
      style={{ minWidth: size, minHeight: size }}
    >
      {renderGeometryIcon(geometryType, color)}
    </svg>
  )
}

function renderGeometryIcon(geometryType: GeometryType | undefined, color: string) {
  switch (geometryType) {
    case 'Point':
    case 'MultiPoint':
      return (
        <circle cx="7" cy="7" r="4" fill={color} stroke="white" strokeWidth="1" opacity="0.9" />
      )

    case 'LineString':
    case 'MultiLineString':
      return (
        <polyline
          points="2,11 5,4 9,9 12,3"
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )

    case 'Polygon':
    case 'MultiPolygon':
      return (
        <polygon
          points="7,1 13,5 11,12 3,12 1,5"
          fill={color}
          fillOpacity="0.4"
          stroke={color}
          strokeWidth="1.2"
        />
      )

    default:
      // Raster or unknown — grid icon
      return (
        <rect
          x="2"
          y="2"
          width="10"
          height="10"
          rx="1"
          fill={color}
          fillOpacity="0.3"
          stroke={color}
          strokeWidth="1"
        />
      )
  }
}
