import type {
  GeoJSONFeature,
  GeoJSONFeatureCollection,
  ParsedVectorData,
} from './types'

export const LARGE_LAYER_THRESHOLD_BYTES = 20 * 1024 * 1024
export const LARGE_LAYER_SAMPLE_FEATURES = 5000
const LARGE_LAYER_SAMPLE_BYTES = 2 * 1024 * 1024

const vectorRegistry = new Map<string, GeoJSONFeatureCollection>()

export function shouldHandleLayer(sizeBytes: number | undefined | null): boolean {
  return Number.isFinite(sizeBytes) && Number(sizeBytes) > LARGE_LAYER_THRESHOLD_BYTES
}

export function registerVectorGeoJSON(
  handleId: string,
  geojson: GeoJSONFeatureCollection,
): void {
  vectorRegistry.set(handleId, geojson)
}

export function hasVectorGeoJSON(handleId: string | undefined): boolean {
  return Boolean(handleId && vectorRegistry.has(handleId))
}

export function releaseVectorGeoJSON(handleId: string | undefined): void {
  if (!handleId) return
  vectorRegistry.delete(handleId)
}

export function resolveVectorGeoJSON(vector: ParsedVectorData): GeoJSONFeatureCollection {
  if (vector.dataHandle) {
    const full = vectorRegistry.get(vector.dataHandle)
    if (full) return full
  }
  return vector.geojson
}

export function makeHandledVectorData(
  vector: ParsedVectorData,
  options: { handleId: string; sizeBytes: number; sampleFeatures?: number },
): ParsedVectorData {
  registerVectorGeoJSON(options.handleId, vector.geojson)
  const sample = sampleFeatureCollection(
    vector.geojson,
    options.sampleFeatures ?? LARGE_LAYER_SAMPLE_FEATURES,
  )
  return {
    ...vector,
    geojson: sample,
    dataHandle: options.handleId,
    sampled: sample.features.length < vector.geojson.features.length,
    sampleFeatureCount: sample.features.length,
    handleSizeBytes: options.sizeBytes,
  }
}

export function makeSampledVectorData(
  vector: ParsedVectorData,
  sizeBytes: number,
): ParsedVectorData {
  const sample = sampleFeatureCollection(vector.geojson, LARGE_LAYER_SAMPLE_FEATURES)
  const { dataHandle, ...rest } = vector
  void dataHandle
  return {
    ...rest,
    geojson: sample,
    sampled: sample.features.length < vector.featureCount || Boolean(vector.sampled),
    sampleFeatureCount: sample.features.length,
    handleSizeBytes: vector.handleSizeBytes ?? sizeBytes,
  }
}

export function stripVectorHandle(vector: ParsedVectorData): ParsedVectorData {
  const { dataHandle, ...rest } = vector
  void dataHandle
  return rest
}

function sampleFeatureCollection(
  geojson: GeoJSONFeatureCollection,
  maxFeatures: number,
): GeoJSONFeatureCollection {
  const features: GeoJSONFeature[] = []
  let approxBytes = 0
  for (const feature of geojson.features) {
    if (features.length >= maxFeatures) break
    const nextBytes = estimateJsonBytes(feature)
    if (features.length > 0 && approxBytes + nextBytes > LARGE_LAYER_SAMPLE_BYTES) break
    features.push(feature)
    approxBytes += nextBytes
  }
  return {
    type: 'FeatureCollection',
    features,
  }
}

function estimateJsonBytes(value: unknown): number {
  try {
    return JSON.stringify(value).length
  } catch {
    return 1024
  }
}
