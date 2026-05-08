import { MapPin, Upload } from 'lucide-react'
import { getSupportedExtensions } from '@/services/geo'

/**
 * Placeholder shown when no layers are loaded on the map.
 * Displays a drop zone hint and supported format list.
 */
export function MapPlaceholder() {
  const extensions = getSupportedExtensions()
    .filter((ext) => ['.geojson', '.shp', '.csv', '.kml', '.tif', '.gpkg'].includes(ext))
    .join(' · ')

  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
      <div className="text-center animate-fade-in">
        <div className="w-16 h-16 rounded-2xl bg-accent-geo/10 flex items-center justify-center mx-auto mb-4">
          <MapPin className="w-8 h-8 text-accent-geo/60" />
        </div>
        <h3 className="text-lg font-display font-semibold text-text-secondary mb-2">
          No layers loaded
        </h3>
        <p className="text-sm text-text-muted max-w-[280px] mb-4">
          Drag & drop GIS files here, or use the Layer Panel to add data.
        </p>
        <div className="flex items-center justify-center gap-2 text-xs text-text-muted">
          <Upload className="w-3.5 h-3.5" />
          <span>{extensions}</span>
        </div>
      </div>
    </div>
  )
}
