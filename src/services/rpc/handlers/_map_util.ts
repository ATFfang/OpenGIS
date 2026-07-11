/**
 * Map-handler 共享几何工具
 *
 * Multiple handlers accept agent-friendly GeoJSON shapes. These helpers
 * normalize FeatureCollection / Feature / bare geometry inputs and compute
 * shared bbox / geometry metadata without touching frontend state.
 */

import type {
  BBox,
  GeoJSONFeature,
  GeoJSONFeatureCollection,
} from '@/services/geo';
import { detectGeometryType as detectFeatureCollectionGeometryType } from '@/services/geo/geometry';

/**
 * 接受 agent 端发来的任何 GeoJSON 形状（FeatureCollection / Feature /
 * 裸 geometry），归一化成 FeatureCollection。
 *
 * 无法识别的形状返回 null，让 handler 层抛 invalidParams / 记录日志，
 * 而不是直接崩。
 */
export function normalizeToFeatureCollection(
  raw: unknown,
): GeoJSONFeatureCollection | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;

  if (obj.type === 'FeatureCollection' && Array.isArray(obj.features)) {
    return obj as unknown as GeoJSONFeatureCollection;
  }

  if (obj.type === 'Feature' && obj.geometry) {
    return {
      type: 'FeatureCollection',
      features: [obj as unknown as GeoJSONFeature],
    };
  }

  if (typeof obj.type === 'string' && 'coordinates' in obj) {
    return {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: obj as unknown as GeoJSONFeature['geometry'],
          properties: {},
        },
      ],
    };
  }

  return null;
}

export function detectGeometryType(
  fc: GeoJSONFeatureCollection,
): ReturnType<typeof detectFeatureCollectionGeometryType> {
  return detectFeatureCollectionGeometryType(fc);
}

export function computeBBox(fc: GeoJSONFeatureCollection): BBox {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  const visit = (coords: unknown): void => {
    if (
      Array.isArray(coords) &&
      typeof coords[0] === 'number' &&
      typeof coords[1] === 'number'
    ) {
      const [x, y] = coords as [number, number];
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      return;
    }
    if (Array.isArray(coords)) coords.forEach(visit);
  };

  for (const feature of fc.features) {
    if (feature.geometry?.coordinates) visit(feature.geometry.coordinates);
  }

  if (!isFinite(minX)) {
    return { minX: -180, minY: -90, maxX: 180, maxY: 90 };
  }
  return { minX, minY, maxX, maxY };
}

/**
 * BBox → `[minX, minY, maxX, maxY]` 元组（mapEngine.fitBounds 和
 * INTERFACE.md 回传 bbox 都用这种形状）。
 */
export function bboxToTuple(bbox: BBox): [number, number, number, number] {
  return [bbox.minX, bbox.minY, bbox.maxX, bbox.maxY];
}
