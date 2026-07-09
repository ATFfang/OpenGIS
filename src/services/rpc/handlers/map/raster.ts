import type { RpcHandler } from '../../registry';
import { RpcError } from '../../errors';
import { notImplemented, parseParams } from '../_util';
import { bboxToTuple } from '../_map_util';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { type BBox, type DataSourceMeta, type MapLayerDefinition, type ParsedRasterData } from '@/services/geo';
import { parseGeoTIFF } from '@/services/geo/parsers';
import { AddImageOverlaySchema, AddRasterFromFileSchema, AddRasterFromUrlSchema } from '../schemas';
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
      width: 0,
      height: 0,
      bandCount: 3,
      crs: 'EPSG:3857',
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
      raster = await parseGeoTIFF(buffer, fileName);
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
    };
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
    };

    const layerId = `image_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName,
      extension: getExtensionLower(parsed.path) || '.png',
      sourceType: 'geotiff',
      fileSize: 0,
      filePath: parsed.path,
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
    };
  }
};
