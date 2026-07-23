/**
 * 3D Tiles / 点云 图层的 RPC handler。
 *
 * 与 raster.ts 类似：agent（Python 后端）先把本地文件通过文件服务端点
 * 暴露成 HTTP URL，再调用这里的 handler 构造 MapLayerDefinition 塞进
 * mapStore。真正的渲染由 deck.gl overlay 完成（见 renderers/deckOverlay.ts）。
 */
import type { RpcHandler } from '../../registry';
import { parseParams } from '../_util';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import {
  type BBox,
  type DataSourceMeta,
  type MapLayerDefinition,
  type ParsedPointCloudData,
  type ParsedTiles3DData,
} from '@/services/geo';
import { AddPointCloudSchema, AddTiles3DSchema } from '../schemas';
import { v4 as uuidv4 } from 'uuid';

function tupleToBBox(t?: [number, number, number, number]): BBox | undefined {
  if (!t) return undefined;
  return { minX: t[0], minY: t[1], maxX: t[2], maxY: t[3] };
}

export const tiles3dHandlers: Record<string, RpcHandler> = {
  'rpc.ui.map.add_tiles3d': (params) => {
    const parsed = parseParams(AddTiles3DSchema, params, 'rpc.ui.map.add_tiles3d');
    const bbox = tupleToBBox(parsed.bbox);

    const data: ParsedTiles3DData = {
      kind: 'tiles3d',
      tilesetUrl: parsed.tileset_url,
      bbox,
      modelMatrix: parsed.model_matrix,
    };

    const layerId = parsed.layer_id ?? `tiles3d_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName: `${parsed.name}.tileset`,
      extension: '.json',
      sourceType: '3dtiles',
      fileSize: 0,
    };
    const definition: MapLayerDefinition = {
      id: layerId,
      name: parsed.name,
      sourceType: '3dtiles',
      visible: parsed.visible ?? true,
      style: {
        renderType: 'tiles3d',
        color: parsed.color ?? '#ffffff',
        opacity: 1,
        strokeColor: '#000',
        strokeWidth: 0,
        tiles3d: {
          pointSize: parsed.point_size,
          color: parsed.color,
          maximumScreenSpaceError: parsed.max_screen_space_error,
        },
      },
      data,
      meta,
      addedAt: Date.now(),
    };
    useMapStore.getState().addLayer(definition);

    if (bbox) {
      mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY], { padding: 60 });
    }

    return {
      layer_id: layerId,
      name: parsed.name,
      tileset_url: parsed.tileset_url,
      bbox: parsed.bbox ?? null,
    };
  },

  'rpc.ui.map.add_pointcloud': (params) => {
    const parsed = parseParams(AddPointCloudSchema, params, 'rpc.ui.map.add_pointcloud');
    const bbox = tupleToBBox(parsed.bbox);
    const origin = parsed.origin as [number, number, number?] | undefined;

    const data: ParsedPointCloudData = {
      kind: 'pointcloud',
      url: parsed.url,
      format: parsed.format ?? 'laz',
      coordinate: parsed.coordinate ?? (origin ? 'meter-offset' : 'lnglat'),
      origin,
      bbox,
    };

    const layerId = parsed.layer_id ?? `pointcloud_${uuidv4()}`;
    const meta: DataSourceMeta = {
      fileName: `${parsed.name}.${data.format}`,
      extension: `.${data.format}`,
      sourceType: 'pointcloud',
      fileSize: 0,
    };
    const definition: MapLayerDefinition = {
      id: layerId,
      name: parsed.name,
      sourceType: 'pointcloud',
      visible: parsed.visible ?? true,
      style: {
        renderType: 'pointcloud',
        color: parsed.color ?? '#ffffff',
        opacity: 1,
        strokeColor: '#000',
        strokeWidth: 0,
        pointcloud: {
          pointSize: parsed.point_size,
          color: parsed.color,
        },
      },
      data,
      meta,
      addedAt: Date.now(),
    };
    useMapStore.getState().addLayer(definition);

    if (bbox) {
      mapEngine.fitBounds([bbox.minX, bbox.minY, bbox.maxX, bbox.maxY], { padding: 60 });
    }

    return {
      layer_id: layerId,
      name: parsed.name,
      url: parsed.url,
      format: data.format,
      bbox: parsed.bbox ?? null,
    };
  },
};
