/**
 * rpc.ui.map.* handlers
 *
 * Stage 1：参数校验 + 抛 `-32603 not implemented`。
 * Stage 3.5：Python 真正会调的 7 个 handler 改为真实现
 *   - rpc.ui.map.add_layer_from_geojson
 *   - rpc.ui.map.remove_layer
 *   - rpc.ui.map.zoom_to_bbox
 *   - rpc.ui.map.fly_to
 *   - rpc.ui.map.set_basemap
 *   - rpc.ui.map.set_layer_style
 *   - rpc.ui.map.set_layer_visibility
 * Stage 3.7：补齐读接口 + add_layer(path)，让 TS 成为 Single Source of Truth
 *   - rpc.ui.map.add_layer          （按文件路径加载 GeoJSON / CSV）
 *   - rpc.ui.map.zoom_to_layer      （从 LayerStore 查 bbox，无需 Python 记账）
 *   - rpc.ui.map.list_layers        （返回当前所有图层精简 meta）
 *   - rpc.ui.map.get_layer          （按 id 查单层 meta + 字段列表）
 *   - rpc.ui.map.query_features     （内存过滤 attribute/bbox/point + limit）
 * Stage 3.11 (2026-04-24)：本地 TIFF / 多渲染模式 / 导出
 *   - rpc.ui.map.add_raster_from_url  （XYZ/WMTS tile；COG 仍留 notImplemented）
 *   - rpc.ui.map.add_raster_from_file （本地 GeoTIFF 文件；TS 前端解析）
 *   - rpc.ui.map.set_layer_renderer   （切换 renderType：fill/graduated/heatmap/…）
 *   - rpc.ui.map.export_map           （导出 PNG/JPG，可选 DPI 与 save_path）
 *
 * A2 决策下这些 handler 返回值在 Stage 3 里不会被 Python 消费
 *（Python 端发 notification 不等 response），但仍然构造完整返回值，
 *   为将来切回 request 模式（或 TS 侧另一个调用方）备好契约。
 */

import type { RpcHandler } from '../registry';
import { RpcError } from '../errors';
import { notImplemented, parseParams } from './_util';
import {
  bboxToTuple,
  computeBBox,
  detectGeometryType,
  normalizeToFeatureCollection,
} from './_map_util';
import { useMapStore } from '@/stores/mapStore';
import { mapEngine } from '@/features/map/engine/MapEngine';
import { exportMap } from '@/features/map/export';
import {
  BUILTIN_BASEMAPS,
  getDefaultStyle,
  type BBox,
  type DataSourceMeta,
  type GeoJSONFeature,
  type GeoJSONFeatureCollection,
  type LayerStyle,
  type MapLayerDefinition,
  type ParsedRasterData,
  type ParsedVectorData,
} from '@/services/geo';
import { parseGeoJSON, parseCSV, parseGeoTIFF, extractFields } from '@/services/geo/parsers';
import {
  AddLayerFromGeoJsonSchema,
  AddLayerSchema,
  AddImageOverlaySchema,
  AddRasterFromFileSchema,
  AddRasterFromUrlSchema,
  ExportMapSchema,
  FlyToSchema,
  GetLayerSchema,
  ListLayersSchema,
  QueryFeaturesSchema,
  RemoveLayerSchema,
  SetBasemapSchema,
  SetLayerRendererSchema,
  SetLayerStyleSchema,
  SetLayerVisibilitySchema,
  ZoomToBBoxSchema,
  ZoomToLayerSchema,
} from './schemas';
import { pathToImageUrl } from './_image_url';

// ---------------------------------------------------------------------------
// Basemap 别名表
// ---------------------------------------------------------------------------
//
// Python 端（display.py::set_basemap）发来的是 LLM 可读的短名
// ("osm" / "satellite" / "dark" / "light")，但 BUILTIN_BASEMAPS 里的
// 实际 id 是 "osm-streets" / "osm-raster" / "carto-dark" / "carto-light"。
// 做一层别名映射，让两边各自保持各自的习惯。
const BASEMAP_ALIAS: Record<string, string> = {
  osm: 'osm-streets',
  satellite: 'osm-raster',
  dark: 'carto-dark',
  light: 'carto-light',
};

function resolveBasemapId(raw: string): string {
  // 先查别名，没命中当作直接的 BUILTIN id 处理。
  return BASEMAP_ALIAS[raw] ?? raw;
}

export const mapHandlers: Record<string, RpcHandler> = {
  /**
   * 按文件路径加载矢量数据并转成 GeoJSON 图层。
   *
   * 仅在 Electron renderer 里可用（依赖 `window.electronAPI.readFile`）；
   * 支持 .geojson / .json / .csv。其它格式（shp/kml/gpkg/tif）目前走
   * AssetExplorer 拖拽通道，不从这里加 —— Python 端调 add_layer 时应该
   * 优先转成 GeoJSON 再走 add_layer_from_geojson。
   */
  'rpc.ui.map.add_layer': async (params) => {
    const parsed = parseParams(AddLayerSchema, params, 'rpc.ui.map.add_layer');
    const filePath = parsed.path;
    const ext = getExtensionLower(filePath);

    const api = (globalThis as any).window?.electronAPI;
    if (!api?.readFile) {
      throw RpcError.internal(
        'add_layer: electronAPI.readFile is unavailable (not running in Electron renderer)',
        { method: 'rpc.ui.map.add_layer' },
      );
    }

    let text: string;
    try {
      const result = await api.readFile(filePath);
      if (!result?.success || typeof result.content !== 'string') {
        throw new Error(result?.error || 'readFile returned no content');
      }
      text = result.content;
    } catch (err) {
      throw RpcError.invalidParams(
        `add_layer: failed to read file '${filePath}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_layer', path: filePath },
      );
    }

    let vector: ParsedVectorData;
    const displayName = parsed.name ?? basenameWithoutExt(filePath);
    try {
      if (ext === '.geojson' || ext === '.json') {
        vector = parseGeoJSON(text, displayName);
      } else if (ext === '.csv' || ext === '.tsv') {
        vector = parseCSV(text, displayName);
      } else {
        throw RpcError.invalidParams(
          `add_layer: unsupported extension '${ext}'. Supported: .geojson .json .csv .tsv. ` +
            `For other formats, convert to GeoJSON and use add_layer_from_geojson.`,
          { method: 'rpc.ui.map.add_layer', path: filePath },
        );
      }
    } catch (err) {
      if (err instanceof RpcError) throw err;
      throw RpcError.invalidParams(
        `add_layer: failed to parse '${filePath}': ${(err as Error).message}`,
        { method: 'rpc.ui.map.add_layer', path: filePath },
      );
    }

    const layerId = `layer_${Math.abs(hashString(displayName + filePath)) % 10 ** 8}`;
    const meta: DataSourceMeta = {
      fileName: filePath.split(/[\\/]/).pop() || displayName,
      extension: ext,
      sourceType: ext === '.csv' || ext === '.tsv' ? 'csv' : 'geojson',
      fileSize: text.length,
    };
    const style = getDefaultStyle(vector.geometryType);

    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: meta.sourceType,
      visible: parsed.visible ?? true,
      style,
      data: vector,
      meta,
      addedAt: Date.now(),
    };

    useMapStore.getState().addLayer(definition);

    return {
      layer_id: layerId,
      name: displayName,
      bbox: bboxToTuple(vector.bbox),
      feature_count: vector.featureCount,
      geometry_type: vector.geometryType,
      crs: vector.crs,
      fields: vector.fields.map((f) => ({ name: f.name, type: f.type })),
    };
  },

  /**
   * XYZ / WMTS tile raster 图层。前端走 MapLibre 的 raster tile source，
   * 在 rasterRenderer 里消费 `ParsedRasterData.source='tile-xyz'`。
   * COG 仍标 notImplemented —— 需要 Cloud-Optimized GeoTIFF 的字节范围请求
   * 解析，未来接 `georaster-layer-for-leaflet` 或 cogeo-server 代理。
   */
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
    const layerId = `raster_${Math.abs(hashString(parsed.name + parsed.url)) % 10 ** 8}`;
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

  /**
   * 从本地文件加载 GeoTIFF。调用方给出 Electron 能读到的绝对路径，TS
   * 读 ArrayBuffer → geotiff.js 解析 → 渲染到 Canvas → 封装为 image
   * source 喂给 MapEngine。**零 Python 依赖**。
   *
   * 支持的 CRS：EPSG:4326 / EPSG:3857。其它投影的 TIFF 需要调用方先
   * `gdalwarp` 转成 3857 或 4326，否则几何对不准底图。
   */
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

    const layerId = `raster_${Math.abs(hashString(displayName + parsed.path)) % 10 ** 8}`;
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

  /**
   * 把任意本地图片（matplotlib PNG / 截图 / Logo 等，**没有真实地理坐标**）
   * 当作 ImageOverlay 钉到地图上。和 add_raster_from_file 的区别：
   *
   *   - 不走 geotiff.js 解析，因此**没有 CRS / 波段统计 / nodata** 概念。
   *   - 默认 bbox 用"当前视口中心 ± 2°"（瞎猜的矩形），用户可通过
   *     LayerPanel 的透明度滑块和后续的拖拽 handle 调整。
   *   - 复用现成的 `rasterRenderer` 的 `source='image'` 分支——零新 renderer。
   *
   * 调用入口：chat 面板的"Pin to map"按钮。LLM 也可以主动调，但目前没有
   * 对应 skill（save_plot 只 push 到 chat，不直接 pin）。
   */
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

    const layerId = `image_${Math.abs(hashString(parsed.path)) % 10 ** 8}`;
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
  },

  /**
   * 切换现有图层的 renderType。必须同时传入对应的配置段（graduated /
   * categorized / heatmap / cluster / extrusion），否则 renderer 没法
   * 构造正确的 expression。fill/line/circle/raster 切换不需要配置段。
   */
  'rpc.ui.map.set_layer_renderer': (params) => {
    const parsed = parseParams(
      SetLayerRendererSchema,
      params,
      'rpc.ui.map.set_layer_renderer',
    );
    const store = useMapStore.getState();
    const layer = store.getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `set_layer_renderer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_renderer' },
      );
    }

    const nextStyle: Partial<LayerStyle> = { renderType: parsed.renderer };

    switch (parsed.renderer) {
      case 'graduated':
        if (!parsed.graduated) {
          throw RpcError.invalidParams(
            'set_layer_renderer: graduated config is required when renderer=graduated',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.graduated = parsed.graduated;
        break;
      case 'categorized':
        if (!parsed.categorized) {
          throw RpcError.invalidParams(
            'set_layer_renderer: categorized config is required when renderer=categorized',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.categorized = parsed.categorized;
        break;
      case 'heatmap':
        nextStyle.heatmap = parsed.heatmap ?? {};
        break;
      case 'cluster':
        nextStyle.cluster = parsed.cluster ?? {};
        break;
      case 'extrusion':
        if (!parsed.extrusion) {
          throw RpcError.invalidParams(
            'set_layer_renderer: extrusion config is required when renderer=extrusion',
            { method: 'rpc.ui.map.set_layer_renderer' },
          );
        }
        nextStyle.extrusion = parsed.extrusion;
        break;
      // fill/line/circle/raster: no extra config
      default:
        break;
    }

    store.updateLayerStyle(parsed.layer_id, nextStyle);
    return {
      layer_id: parsed.layer_id,
      renderer: parsed.renderer,
    };
  },

  /**
   * 导出当前地图视图为 PNG/JPG。默认返回 base64 data URL 供 Python 保存；
   * 若提供 save_path 且在 Electron 下，会直接写文件，返回文件路径。
   */
  'rpc.ui.map.export_map': async (params) => {
    const parsed = parseParams(ExportMapSchema, params, 'rpc.ui.map.export_map');
    const result = await exportMap({
      format: parsed.format ?? 'png',
      dpiScale: parsed.dpi_scale ?? 1,
      quality: parsed.quality ?? 0.92,
      autoDownload: false,
    });

    if (parsed.save_path) {
      // electronAPI.writeFile 目前签名是 (path, string)，没有 binary 通道。
      // 我们把 PNG/JPG blob 转成 base64，Python 或 main 进程拿到 data_url
      // 自行 decode 写盘。**不抛错**，只是在返回里附上 note。
      const arrayBuffer = await result.blob.arrayBuffer();
      const bytes = new Uint8Array(arrayBuffer);
      let binary = '';
      const CHUNK = 0x8000;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        binary += String.fromCharCode.apply(
          null,
          Array.from(bytes.subarray(i, i + CHUNK)) as any,
        );
      }
      const base64 = btoa(binary);
      const api = (globalThis as any).window?.electronAPI;
      // 尝试通过 writeFile(path, content) 落盘——但 PNG 不是文本，
      // 直接写会乱码。我们改用带 base64 解码能力的新 IPC；没有就回退
      // 返回 data_url 让调用方自己处理。
      if (api?.writeFileBinary) {
        try {
          await api.writeFileBinary(parsed.save_path, arrayBuffer);
          return {
            saved_to: parsed.save_path,
            width: result.width,
            height: result.height,
            format: result.format,
          };
        } catch (err) {
          throw RpcError.internal(
            `export_map: writeFileBinary failed: ${(err as Error).message}`,
            { method: 'rpc.ui.map.export_map' },
          );
        }
      }
      // 没 writeFileBinary 时，把 base64 一并返回给调用方，让它自己写
      return {
        data_url: `data:image/${result.format};base64,${base64}`,
        width: result.width,
        height: result.height,
        format: result.format,
        file_name: result.fileName,
        save_path_requested: parsed.save_path,
        note:
          'save_path was provided but electronAPI.writeFileBinary is not available; ' +
          'decode data_url in caller to write file.',
      };
    }

    return {
      data_url: result.dataUrl,
      width: result.width,
      height: result.height,
      format: result.format,
      file_name: result.fileName,
    };
  },

  /**
   * 聚焦到某个图层 —— 关键原语：LLM 不用传 bbox，TS 从 LayerStore
   * 里查 `data.bbox`。
   */
  'rpc.ui.map.zoom_to_layer': (params) => {
    const parsed = parseParams(ZoomToLayerSchema, params, 'rpc.ui.map.zoom_to_layer');
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `zoom_to_layer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.zoom_to_layer' },
      );
    }
    const bbox = layer.data.bbox;
    if (!bbox) {
      throw RpcError.invalidParams(
        `zoom_to_layer: layer '${parsed.layer_id}' has no bbox (empty or invalid geometry)`,
        { method: 'rpc.ui.map.zoom_to_layer' },
      );
    }
    const tuple = bboxToTuple(bbox);
    mapEngine.fitBounds(tuple, { padding: parsed.padding ?? 40 });
    return { layer_id: parsed.layer_id, bbox: tuple };
  },

  /**
   * 列出当前地图上所有图层的精简 meta。返回值里**不含 features**，避免
   * chat 协议被几百万 point 撑爆。需要 features 请走 query_features。
   */
  'rpc.ui.map.list_layers': (params) => {
    parseParams(ListLayersSchema, params, 'rpc.ui.map.list_layers');
    const layers = useMapStore.getState().layers;
    return {
      layers: layers.map(summarizeLayer),
      count: layers.length,
    };
  },

  /**
   * 按 id 查单层。返回 meta + 字段 schema，**不含 features**（同 list_layers）。
   */
  'rpc.ui.map.get_layer': (params) => {
    const parsed = parseParams(GetLayerSchema, params, 'rpc.ui.map.get_layer');
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `get_layer: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.get_layer' },
      );
    }
    return summarizeLayer(layer);
  },

  /**
   * 按属性 / bbox / point 过滤某图层的 features，内存里做，不走 MapLibre
   * queryRenderedFeatures（那是"当前视口可见"，语义不一样）。
   *
   * point 命中判定：点落在 feature 的 bbox 内即视为命中（粗略但足够 LLM
   * 用；精确 point-in-polygon 留给后续 geo 分析 skill）。
   */
  'rpc.ui.map.query_features': (params) => {
    const parsed = parseParams(QueryFeaturesSchema, params, 'rpc.ui.map.query_features');
    const layer = useMapStore.getState().getLayerById(parsed.layer_id);
    if (!layer) {
      throw RpcError.invalidParams(
        `query_features: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.query_features' },
      );
    }
    if (layer.data.kind !== 'vector') {
      throw RpcError.invalidParams(
        `query_features: layer '${parsed.layer_id}' is not a vector layer`,
        { method: 'rpc.ui.map.query_features' },
      );
    }

    const allFeatures = layer.data.geojson.features;
    const filter = parsed.filter ?? {};
    const attrFilters = filter.attribute ?? [];
    const bboxFilter = filter.bbox;
    const pointFilter = filter.point;
    const limit = parsed.limit ?? 1000;

    const matched: GeoJSONFeature[] = [];
    for (const f of allFeatures) {
      if (!matchesAttributes(f, attrFilters)) continue;
      if (bboxFilter && !featureIntersectsBBox(f, bboxFilter)) continue;
      if (pointFilter && !featureContainsPoint(f, pointFilter)) continue;
      matched.push(f);
      if (matched.length >= limit) break;
    }

    return {
      layer_id: parsed.layer_id,
      total_matched: matched.length,
      truncated: allFeatures.length > matched.length && matched.length === limit,
      features: matched,
    };
  },

  // ── Stage 3.5: 真实现 ───────────────────────────────────────────────

  /**
   * 从内联 GeoJSON 添加矢量图层。Python 端 `add_layer` 的 canonical 通道。
   *
   * zod schema 是 strip 模式——Python 多发的 `layer_id/color/opacity/bbox`
   * 不会被 reject，handler 这里从原始 params 里再读一次。
   */
  'rpc.ui.map.add_layer_from_geojson': (params) => {
    const parsed = parseParams(
      AddLayerFromGeoJsonSchema,
      params,
      'rpc.ui.map.add_layer_from_geojson',
    );
    const extras = (params ?? {}) as Record<string, unknown>;

    const fc = normalizeToFeatureCollection(parsed.geojson);
    if (!fc) {
      throw RpcError.invalidParams(
        'add_layer_from_geojson: geojson is not a FeatureCollection / Feature / bare geometry',
        { method: 'rpc.ui.map.add_layer_from_geojson' },
      );
    }
    if (fc.features.length === 0) {
      throw RpcError.invalidParams(
        'add_layer_from_geojson: geojson has no features',
        { method: 'rpc.ui.map.add_layer_from_geojson' },
      );
    }

    const geometryType = detectGeometryType(fc);
    const bbox = computeBBox(fc);
    const displayName = parsed.name;
    const layerId =
      typeof extras.layer_id === 'string' && extras.layer_id
        ? (extras.layer_id as string)
        : `layer_${Math.abs(hashString(displayName)) % 10 ** 8}`;

    const data: ParsedVectorData = {
      kind: 'vector',
      geojson: fc,
      geometryType,
      featureCount: fc.features.length,
      bbox,
      crs: 'EPSG:4326',
      fields: extractFields(fc),
    };

    // Agent 造的图层不落文件，仍造一个虚 meta 让 LayerPanel/AssetExplorer
    // 的 `l.meta.fileName` 守卫不 trip。
    const meta: DataSourceMeta = {
      fileName: `${displayName}.geojson`,
      extension: '.geojson',
      sourceType: 'geojson',
      fileSize: 0,
    };

    const style = getDefaultStyle(geometryType);
    if (typeof extras.color === 'string' && extras.color) {
      style.color = extras.color as string;
    }
    if (typeof extras.opacity === 'number') {
      style.opacity = extras.opacity as number;
    }

    const definition: MapLayerDefinition = {
      id: layerId,
      name: displayName,
      sourceType: 'geojson',
      visible: parsed.visible ?? true,
      style,
      data,
      meta,
      addedAt: Date.now(),
    };

    useMapStore.getState().addLayer(definition);

    return {
      layer_id: layerId,
      bbox: bboxToTuple(bbox),
      feature_count: fc.features.length,
      geometry_type: geometryType,
      crs: 'EPSG:4326',
      fields: data.fields.map((f) => ({ name: f.name, type: f.type })),
    };
  },

  'rpc.ui.map.remove_layer': (params) => {
    const parsed = parseParams(RemoveLayerSchema, params, 'rpc.ui.map.remove_layer');
    const store = useMapStore.getState();
    const exists = !!store.getLayerById(parsed.layer_id);
    store.removeLayer(parsed.layer_id);
    return { layer_id: parsed.layer_id, removed: exists };
  },

  'rpc.ui.map.zoom_to_bbox': (params) => {
    const parsed = parseParams(ZoomToBBoxSchema, params, 'rpc.ui.map.zoom_to_bbox');
    const bbox: [number, number, number, number] = [
      parsed.bbox[0],
      parsed.bbox[1],
      parsed.bbox[2],
      parsed.bbox[3],
    ];
    mapEngine.fitBounds(bbox, { padding: parsed.padding ?? 40 });
    return { bbox };
  },

  'rpc.ui.map.fly_to': (params) => {
    const parsed = parseParams(FlyToSchema, params, 'rpc.ui.map.fly_to');
    const center: [number, number] = [parsed.center[0], parsed.center[1]];
    mapEngine.flyTo(center, parsed.zoom);
    return { center, zoom: parsed.zoom ?? null };
  },

  'rpc.ui.map.set_basemap': (params) => {
    const parsed = parseParams(SetBasemapSchema, params, 'rpc.ui.map.set_basemap');

    if (typeof parsed.basemap === 'string') {
      const targetId = resolveBasemapId(parsed.basemap);
      const basemap = BUILTIN_BASEMAPS.find((b) => b.id === targetId);
      if (!basemap) {
        throw RpcError.invalidParams(
          `Unknown basemap id: ${parsed.basemap} (resolved to ${targetId})`,
          {
            method: 'rpc.ui.map.set_basemap',
            known: BUILTIN_BASEMAPS.map((b) => b.id),
            aliases: Object.keys(BASEMAP_ALIAS),
          },
        );
      }
      useMapStore.getState().setBasemap(basemap);
      return { basemap_id: basemap.id };
    }

    // { style_url: '...' } 形式：Stage 3.5 不支持自定义 style_url，
    // 留给后续 Stage。清晰地抛，不要静默降级。
    throw RpcError.invalidParams(
      'set_basemap: custom { style_url } basemaps are not supported yet',
      { method: 'rpc.ui.map.set_basemap', stage: 'stage-3.5' },
    );
  },

  'rpc.ui.map.set_layer_style': (params) => {
    const parsed = parseParams(SetLayerStyleSchema, params, 'rpc.ui.map.set_layer_style');
    const store = useMapStore.getState();
    if (!store.getLayerById(parsed.layer_id)) {
      throw RpcError.invalidParams(
        `set_layer_style: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_style' },
      );
    }

    // Python display.py::update_layer_style 发来的 paint 里只有
    // `fill-color` / `fill-opacity`（或 `circle-color` / `line-color` 等
    // 同义 key 取决于 type）。我们从 paint 里提 color/opacity 投影到
    // LayerStore 的 `style.color / style.opacity` 字段，并忠实保留
    // 原 style 给 MapEngine 将来细粒度消费。
    const paint = (parsed.style.paint ?? {}) as Record<string, unknown>;
    const color =
      firstDefinedString(paint, ['fill-color', 'line-color', 'circle-color']) ?? undefined;
    const opacity =
      firstDefinedNumber(paint, ['fill-opacity', 'line-opacity', 'circle-opacity']) ?? undefined;

    if (color !== undefined) {
      store.updateLayerStyle(parsed.layer_id, { color });
    }
    if (opacity !== undefined) {
      store.setLayerOpacity(parsed.layer_id, opacity);
    }

    return { layer_id: parsed.layer_id, applied: { color, opacity } };
  },

  'rpc.ui.map.set_layer_visibility': (params) => {
    const parsed = parseParams(
      SetLayerVisibilitySchema,
      params,
      'rpc.ui.map.set_layer_visibility',
    );
    const store = useMapStore.getState();
    if (!store.getLayerById(parsed.layer_id)) {
      throw RpcError.invalidParams(
        `set_layer_visibility: layer_id '${parsed.layer_id}' not found`,
        { method: 'rpc.ui.map.set_layer_visibility' },
      );
    }
    store.setLayerVisibility(parsed.layer_id, parsed.visible);
    return { layer_id: parsed.layer_id, visible: parsed.visible };
  },
};

// ---------------------------------------------------------------------------
// 内部工具
// ---------------------------------------------------------------------------

function firstDefinedString(
  obj: Record<string, unknown>,
  keys: string[],
): string | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v) return v;
  }
  return null;
}

function firstDefinedNumber(
  obj: Record<string, unknown>,
  keys: string[],
): number | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

/** FNV-1a 简易哈希——只用来给 layer_id 造默认短串，冲突不关键。 */
function hashString(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  // 映射到 32-bit signed 范围
  return h | 0;
}

/** 从路径里拿小写扩展名（含点），没扩展名返回空串。 */
function getExtensionLower(filePath: string): string {
  const base = filePath.split(/[\\/]/).pop() ?? '';
  const i = base.lastIndexOf('.');
  return i >= 0 ? base.slice(i).toLowerCase() : '';
}

/** 从路径里拿不带扩展名的文件名，作为默认 displayName。 */
function basenameWithoutExt(filePath: string): string {
  const base = filePath.split(/[\\/]/).pop() ?? 'layer';
  const i = base.lastIndexOf('.');
  return i > 0 ? base.slice(0, i) : base;
}

/**
 * 给 list_layers / get_layer 用的精简表示。故意**不带 features**，
 * 只留 bbox/feature_count/fields 这些 LLM 决策需要的元信息。
 */
function summarizeLayer(layer: MapLayerDefinition) {
  const isVector = layer.data.kind === 'vector';
  return {
    layer_id: layer.id,
    name: layer.name,
    source_type: layer.sourceType,
    visible: layer.visible,
    bbox: bboxToTuple(layer.data.bbox),
    feature_count: isVector ? (layer.data as ParsedVectorData).featureCount : 0,
    geometry_type: isVector ? (layer.data as ParsedVectorData).geometryType : null,
    crs: layer.data.crs,
    fields: isVector
      ? (layer.data as ParsedVectorData).fields.map((f) => ({ name: f.name, type: f.type }))
      : [],
    added_at: layer.addedAt,
  };
}

// ── query_features 过滤原语 ────────────────────────────────────────

type AttrFilter = { field: string; op: '=' | '!=' | '>' | '<' | 'contains'; value: unknown };

function matchesAttributes(feature: GeoJSONFeature, filters: AttrFilter[]): boolean {
  if (filters.length === 0) return true;
  const props = (feature.properties ?? {}) as Record<string, unknown>;
  for (const f of filters) {
    const v = props[f.field];
    switch (f.op) {
      case '=':
        // 宽松相等：数值与字符串表示视为相等，避免 "5" != 5 带来的坑
        if (String(v) !== String(f.value)) return false;
        break;
      case '!=':
        if (String(v) === String(f.value)) return false;
        break;
      case '>':
        if (!(typeof v === 'number' && typeof f.value === 'number' && v > f.value)) {
          if (Number(v) <= Number(f.value) || !Number.isFinite(Number(v))) return false;
        }
        break;
      case '<':
        if (!(typeof v === 'number' && typeof f.value === 'number' && v < f.value)) {
          if (Number(v) >= Number(f.value) || !Number.isFinite(Number(v))) return false;
        }
        break;
      case 'contains':
        if (typeof v !== 'string' || typeof f.value !== 'string' || !v.includes(f.value)) {
          return false;
        }
        break;
    }
  }
  return true;
}

/**
 * feature 的 geometry bbox 和 query bbox 有交集即视为命中。
 * 只查 feature 的 bbox 而不做精确相交——对 LLM 决策足够。
 */
function featureIntersectsBBox(
  feature: GeoJSONFeature,
  queryBBox: [number, number, number, number],
): boolean {
  const fc: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [feature],
  };
  const b = computeBBox(fc);
  const [qMinX, qMinY, qMaxX, qMaxY] = queryBBox;
  return !(b.maxX < qMinX || b.minX > qMaxX || b.maxY < qMinY || b.minY > qMaxY);
}

/**
 * point 命中 feature 的 bbox 即视为命中 —— 对 Point feature 来说是等价的，
 * 对 Polygon 来说是近似；精确 point-in-polygon 留给 geo 分析 skill。
 */
function featureContainsPoint(
  feature: GeoJSONFeature,
  point: [number, number],
): boolean {
  const fc: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [feature],
  };
  const b = computeBBox(fc);
  const [x, y] = point;
  return x >= b.minX && x <= b.maxX && y >= b.minY && y <= b.maxY;
}
