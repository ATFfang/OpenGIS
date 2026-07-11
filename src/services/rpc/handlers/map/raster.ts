import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { notImplemented, parseParams } from '../_util';
import { bboxToTuple } from '../_map_util';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { type BBox, type DataSourceMeta, type MapLayerDefinition, type ParsedRasterData, type RasterStyleSettings } from '@/services/geo';
import { parseGeoTIFF } from '@/services/geo/parsers';
import { getRasterBuffer } from '@/services/geo/rasterSourceRegistry';
import { AddImageOverlaySchema, AddRasterFromFileSchema, AddRasterFromUrlSchema, GetRasterInfoSchema, SetRasterStyleSchema } from '../schemas';
import { pathToImageUrl } from '../_image_url';
import { basenameWithoutExt, getExtensionLower } from './shared';
import { v4 as uuidv4 } from 'uuid';

export const rasterHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.add_raster_from_url': (params) => {
    const parsed = parseParams(
      AddRasterFromUrlSchema,
      params,
      'rpc.ui.map.add_raster_from_url',
    );
    if (parsed.tile_type === 'cog') {
      notImplemented('rpc.ui.map.add_raster_from_url (tile_type=cog)');
    }
    const bbox: BBox = parsed.bounds
      ? { minX: parsed.bounds[0], minY: parsed.bounds[1], maxX: parsed.bounds[2], maxY: parsed.bounds[3] }
      : { minX: -180, minY: -85.05, maxX: 180, maxY: 85.05 };
    const data: ParsedRasterData = {
      kind: 'raster',
      source: parsed.tile_type === 'wmts' ? 'tile-wmts' : 'tile-xyz',
      tileUrl: parsed.url,
      bbox,
      width: rasterInfoNumber(parsed.raster_info, 'width') ?? 0,
      height: rasterInfoNumber(parsed.raster_info, 'height') ?? 0,
      bandCount: rasterInfoNumber(parsed.raster_info, 'band_count') ?? 3,
      crs: rasterInfoString(parsed.raster_info, 'crs') ?? 'EPSG:3857',
      sourcePath: parsed.raster_source_path,
      rasterId: parsed.raster_id,
      rasterStyle: parsed.raster_style as RasterStyleSettings | undefined,
      bandStats: rasterBandStats(parsed.raster_info),
      rerenderable: !!parsed.raster_id,
    };
    const layerId = `raster_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName: parsed.name,
      extension: '.tile',
      sourceType: 'geotiff',
      fileSize: 0,
    };
    const definition: MapLayerDefinition = {
      id: layerId,
      name: parsed.name,
      sourceType: 'geotiff',
      visible: true,
      style: {
        renderType: 'raster',
        color: '#000',
        opacity: 1,
        strokeColor: '#000',
        strokeWidth: 0,
        raster: parsed.raster_style as RasterStyleSettings | undefined,
      },
      data,
      meta,
      addedAt: Date.now(),
    };
    useMapStore.getState().addLayer(definition);
    return {
      layer_id: layerId,
      bbox: bboxToTuple(bbox),
      source: data.source,
      raster_id: parsed.raster_id ?? null,
      raster_style: data.rasterStyle ?? null,
    };
  },

  'rpc.ui.map.add_raster_from_file': async (params) => {
    const parsed = parseParams(
      AddRasterFromFileSchema,
      params,
      'rpc.ui.map.add_raster_from_file',
    );

    const api = (globalThis as any).window?.electronAPI;
    if (!api?.readFileAsBuffer) {
      throw RpcError.internal(
        'add_raster_from_file: electronAPI.readFileAsBuffer is unavailable (not running in Electron renderer)',
        { method: 'rpc.ui.map.add_raster_from_file' },
      );
    }

    let buffer: ArrayBuffer;
    try {
      const result = await api.readFileAsBuffer(parsed.path);
      // preload 可能返回 `{ success, buffer }` 或 `{ ok, buffer }`；
      // API.md §1.1 写 `{ ok, buffer? | error? }`，但历史代码用 success。
      // 两者都兼容。
      const ok = result?.ok ?? result?.success ?? false;
      if (!ok || !result.buffer) {
        throw new Error(result?.error || 'readFileAsBuffer returned no buffer');
      }
      buffer =
        result.buffer instanceof ArrayBuffer
          ? result.buffer
          : new Uint8Array(result.buffer).buffer;
    } catch (err) {
      throw RpcError.invalidParams(
        `add_raster_from_file: failed to read '${parsed.path}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_raster_from_file', path: parsed.path },
      );
    }

    const fileName = parsed.path.split(/[\\/]/).pop() ?? 'raster.tif';
    const displayName = parsed.name ?? basenameWithoutExt(parsed.path);

    let raster: ParsedRasterData;
    try {
      raster = await parseGeoTIFF(buffer, fileName, {
        sourcePath: parsed.path,
        rasterStyle: {
          ...(parsed.raster && typeof parsed.raster === 'object' ? parsed.raster : {}),
          opacity: parsed.opacity ?? (parsed.raster as RasterStyleSettings | undefined)?.opacity ?? 1,
        },
      });
    } catch (err) {
      throw RpcError.invalidParams(
        `add_raster_from_file: failed to parse GeoTIFF '${parsed.path}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_raster_from_file', path: parsed.path },
      );
    }

    const layerId = parsed.layer_id ?? `raster_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName,
      extension: '.tif',
      sourceType: 'geotiff',
      fileSize: buffer.byteLength,
      filePath: parsed.path,
    };
    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: 'geotiff',
      visible: parsed.visible ?? true,
      style: {
        renderType: 'raster',
        color: '#000',
        opacity: parsed.opacity ?? 1,
        strokeColor: '#000',
        strokeWidth: 0,
        raster: raster.rasterStyle,
      },
      data: raster,
      meta,
      addedAt: Date.now(),
    };
    useMapStore.getState().addLayer(definition);

    return {
      layer_id: layerId,
      name: displayName,
      bbox: bboxToTuple(raster.bbox),
      width: raster.width,
      height: raster.height,
      band_count: raster.bandCount,
      crs: raster.crs,
      nodata: raster.noDataValue ?? null,
      band_stats: raster.bandStats ?? null,
      raster_style: raster.rasterStyle ?? null,
      rerenderable: raster.rerenderable ?? false,
    };
  },

  'rpc.ui.map.set_raster_style': async (params) => {
    const parsed = parseParams(
      SetRasterStyleSchema,
      params,
      'rpc.ui.map.set_raster_style',
    );
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_raster_style: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_raster_style' },
      );
    }
    if (layer.data.kind !== 'raster') {
      throw RpcError.invalidParams(
        `set_raster_style: layer '${parsed.layer_id}' is not a raster layer`,
        { method: 'rpc.ui.map.set_raster_style' },
      );
    }
    const sourcePath = layer.data.sourcePath ?? layer.meta.filePath;
    if (layer.data.rasterId && (layer.data.source === 'tile-xyz' || layer.data.source === 'tile-wmts')) {
      return updateBackendTileRasterStyle(layer, parsed.raster);
    }
    if ((!sourcePath || !isGeoTiffPath(sourcePath)) && !layer.data.sourceBufferId) {
      throw RpcError.invalidParams(
        `set_raster_style: layer '${parsed.layer_id}' is not a re-renderable raster layer`,
        { method: 'rpc.ui.map.set_raster_style', layer_id: parsed.layer_id },
      );
    }

    const api = (globalThis as any).window?.electronAPI;
    if (sourcePath && !api?.readFileAsBuffer) {
      throw RpcError.internal(
        'set_raster_style: electronAPI.readFileAsBuffer is unavailable',
        { method: 'rpc.ui.map.set_raster_style' },
      );
    }

    let buffer: ArrayBuffer | undefined;
    try {
      if (sourcePath) {
        const result = await api.readFileAsBuffer(sourcePath);
        const ok = result?.ok ?? result?.success ?? false;
        if (!ok || !result.buffer) {
          throw new Error(result?.error || 'readFileAsBuffer returned no buffer');
        }
        buffer =
          result.buffer instanceof ArrayBuffer
            ? result.buffer
            : new Uint8Array(result.buffer).buffer;
      } else {
        buffer = getRasterBuffer(layer.data.sourceBufferId);
      }
      if (!buffer) throw new Error('No original TIFF source is available for re-rendering');
    } catch (err) {
      throw RpcError.invalidParams(
        `set_raster_style: failed to read original raster source: ${(err as Error).message}`,
        { method: 'rpc.ui.map.set_raster_style', path: sourcePath ?? null, source_buffer_id: layer.data.sourceBufferId ?? null },
      );
    }

    const fileName = sourcePath?.split(/[\\/]/).pop() ?? layer.meta.fileName ?? 'raster.tif';
    const nextRasterStyle: RasterStyleSettings = {
      ...(layer.style.raster ?? layer.data.rasterStyle ?? {}),
      ...parsed.raster,
    };
    let raster: ParsedRasterData;
    try {
      raster = await parseGeoTIFF(buffer, fileName, {
        sourcePath,
        sourceBufferId: layer.data.sourceBufferId,
        rasterStyle: nextRasterStyle,
      });
    } catch (err) {
      throw RpcError.invalidParams(
        `set_raster_style: failed to re-render GeoTIFF '${sourcePath}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.set_raster_style', path: sourcePath },
      );
    }

    const nextLayer: MapLayerDefinition = {
      ...layer,
      data: raster,
      style: {
        ...layer.style,
        opacity: nextRasterStyle.opacity ?? layer.style.opacity,
        raster: raster.rasterStyle,
      },
      meta: {
        ...layer.meta,
        filePath: sourcePath,
      },
    };
    useMapStore.getState().addLayer(nextLayer);

    return rasterInfoResult(nextLayer);
  },

  'rpc.ui.map.get_raster_info': (params) => {
    const parsed = parseParams(
      GetRasterInfoSchema,
      params,
      'rpc.ui.map.get_raster_info',
    );
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `get_raster_info: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.get_raster_info' },
      );
    }
    if (layer.data.kind !== 'raster') {
      throw RpcError.invalidParams(
        `get_raster_info: layer '${parsed.layer_id}' is not a raster layer`,
        { method: 'rpc.ui.map.get_raster_info' },
      );
    }
    return rasterInfoResult(layer);
  },

  'rpc.ui.map.add_image_overlay': async (params) => {
    const parsed = parseParams(
      AddImageOverlaySchema,
      params,
      'rpc.ui.map.add_image_overlay',
    );

    const url = await pathToImageUrl(parsed.path);
    const fileName = parsed.path.split(/[\\/]/).pop() ?? 'image.png';
    const displayName = parsed.name ?? basenameWithoutExt(parsed.path);

    // bbox 优先：调用方给 → 用之；否则用当前视口中心 ± 2°，再不行就
    // 默认中国大概范围。最差也不要 NaN。
    let bbox: [number, number, number, number];
    if (parsed.bbox) {
      bbox = parsed.bbox as [number, number, number, number];
    } else {
      const map = mapEngine.getMap?.();
      const center = map?.getCenter();
      if (center && Number.isFinite(center.lng) && Number.isFinite(center.lat)) {
        const cx = center.lng;
        const cy = center.lat;
        bbox = [cx - 2, cy - 2, cx + 2, cy + 2];
      } else {
        // 中国大致范围
        bbox = [73, 18, 135, 53];
      }
    }
    const [minX, minY, maxX, maxY] = bbox;

    // ImageSource 需要 NW → NE → SE → SW 四角顺时针。
    const imageCoordinates: [
      [number, number],
      [number, number],
      [number, number],
      [number, number],
    ] = [
      [minX, maxY], // NW
      [maxX, maxY], // NE
      [maxX, minY], // SE
      [minX, minY], // SW
    ];

    const data: ParsedRasterData = {
      kind: 'raster',
      source: 'image',
      imageUrl: url,
      bbox: { minX, minY, maxX, maxY },
      width: 0,
      height: 0,
      bandCount: 3,
      crs: 'EPSG:4326',
      imageCoordinates,
      sourcePath: parsed.raster_source_path,
      rasterId: rasterInfoString(parsed.raster_info, 'raster_id') ?? undefined,
      rasterStyle: parsed.raster_style as RasterStyleSettings | undefined,
      bandStats: rasterBandStats(parsed.raster_info),
      rerenderable: false,
    };

    const layerId = `image_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName,
      extension: getExtensionLower(parsed.path) || '.png',
      sourceType: 'geotiff',
      fileSize: 0,
      filePath: parsed.raster_source_path ?? parsed.path,
    };
    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: 'geotiff',
      visible: true,
      style: {
        renderType: 'raster',
        color: '#000',
        opacity: parsed.opacity ?? 1,
        strokeColor: '#000',
        strokeWidth: 0,
        raster: parsed.raster_style as RasterStyleSettings | undefined,
      },
      data,
      meta,
      addedAt: Date.now(),
    };
    useMapStore.getState().addLayer(definition);

    // 顺手 fitBounds 到刚加的 overlay
    mapEngine.fitBounds(bbox, { padding: 60 });

    return {
      layer_id: layerId,
      name: displayName,
      bbox,
      source_path: parsed.raster_source_path ?? parsed.path,
      raster_info: parsed.raster_info ?? null,
    };
  }
};

function isGeoTiffPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith('.tif') || lower.endsWith('.tiff');
}

function rasterInfoResult(layer: MapLayerDefinition): Record<string, unknown> {
  if (layer.data.kind !== 'raster') {
    return { layer_id: layer.id, kind: layer.data.kind };
  }
  return {
    layer_id: layer.id,
    name: layer.name,
    source: layer.data.source,
    source_path: layer.data.sourcePath ?? layer.meta.filePath ?? null,
    bbox: bboxToTuple(layer.data.bbox),
    width: layer.data.width,
    height: layer.data.height,
    band_count: layer.data.bandCount,
    crs: layer.data.crs,
    nodata: layer.data.noDataValue ?? null,
    band_stats: layer.data.bandStats ?? null,
    raster_style: layer.style.raster ?? layer.data.rasterStyle ?? null,
    raster_id: layer.data.rasterId ?? null,
    rerenderable: !!layer.data.rerenderable,
  };
}

async function updateBackendTileRasterStyle(
  layer: MapLayerDefinition,
  rasterUpdates: RasterStyleSettings,
): Promise<Record<string, unknown>> {
  if (layer.data.kind !== 'raster' || !layer.data.rasterId || !layer.data.tileUrl) {
    throw RpcError.invalidParams(
      `set_raster_style: layer '${layer.id}' is not a backend tile raster layer`,
      { method: 'rpc.ui.map.set_raster_style', layer_id: layer.id },
    );
  }

  const styleUrl = backendRasterStyleUrl(layer.data.tileUrl, layer.data.rasterId);
  const nextRasterStyle: RasterStyleSettings = {
    ...(layer.style.raster ?? layer.data.rasterStyle ?? {}),
    ...rasterUpdates,
  };
  const response = await fetch(styleUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(nextRasterStyle),
  });
  if (!response.ok) {
    throw RpcError.internal(
      `set_raster_style: backend raster style update failed (${response.status})`,
      { method: 'rpc.ui.map.set_raster_style', layer_id: layer.id },
    );
  }
  const payload = await response.json();
  const revision = typeof payload?.style_revision === 'number' ? payload.style_revision : Date.now();
  const nextTileUrl = withTileRevision(layer.data.tileUrl, revision);
  const nextLayer: MapLayerDefinition = {
    ...layer,
    data: {
      ...layer.data,
      tileUrl: nextTileUrl,
      rasterStyle: payload?.style ?? nextRasterStyle,
      bandStats: rasterBandStats(payload?.info) ?? layer.data.bandStats,
      rerenderable: true,
    },
    style: {
      ...layer.style,
      opacity: nextRasterStyle.opacity ?? layer.style.opacity,
      raster: payload?.style ?? nextRasterStyle,
    },
  };
  useMapStore.getState().addLayer(nextLayer);
  return rasterInfoResult(nextLayer);
}

function backendRasterStyleUrl(tileUrl: string, rasterId: string): string {
  const url = new URL(tileUrl);
  return `${url.origin}/api/rasters/${encodeURIComponent(rasterId)}/style`;
}

function withTileRevision(tileUrl: string, revision: number): string {
  const url = new URL(tileUrl);
  url.searchParams.set('rev', String(revision));
  return url.toString();
}

function rasterInfoNumber(info: unknown, key: string): number | undefined {
  const value = typeof info === 'object' && info !== null ? (info as Record<string, unknown>)[key] : undefined;
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function rasterInfoString(info: unknown, key: string): string | undefined {
  const value = typeof info === 'object' && info !== null ? (info as Record<string, unknown>)[key] : undefined;
  return typeof value === 'string' && value ? value : undefined;
}

function rasterBandStats(info: unknown): Array<{ min: number; max: number }> | undefined {
  const value = typeof info === 'object' && info !== null ? (info as Record<string, unknown>).band_stats : undefined;
  if (!Array.isArray(value)) return undefined;
  const stats = value
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const min = (item as Record<string, unknown>).min;
      const max = (item as Record<string, unknown>).max;
      return typeof min === 'number' && typeof max === 'number' ? { min, max } : null;
    })
    .filter((item): item is { min: number; max: number } => !!item);
  return stats.length ? stats : undefined;
}
