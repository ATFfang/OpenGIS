import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge Tailwind CSS classes with clsx.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Format file size in human-readable format.
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

/**
 * Detect GIS file type from extension.
 */
export function detectGISFormat(filename: string): string | null {
  const ext = filename.split('.').pop()?.toLowerCase()
  const formats: Record<string, string> = {
    shp: 'Shapefile',
    geojson: 'GeoJSON',
    json: 'GeoJSON',
    gpkg: 'GeoPackage',
    tif: 'GeoTIFF',
    tiff: 'GeoTIFF',
    kml: 'KML',
    kmz: 'KMZ',
    gml: 'GML',
    csv: 'CSV',
    nc: 'NetCDF',
    hdf5: 'HDF5',
    h5: 'HDF5',
  }
  return ext ? formats[ext] ?? null : null
}

/**
 * Check if a file extension is a supported GIS format.
 */
export function isGISFile(filename: string): boolean {
  return detectGISFormat(filename) !== null
}
