/**
 * deck.gl 叠加层管理器 —— 3D Tiles / 点云的统一渲染通道。
 *
 * 背景：MapLibre GL 原生无法渲染 OGC 3D Tiles（tileset.json + b3dm/i3dm/pnts）
 * 与裸点云（las/laz）。我们通过 deck.gl 的 `MapboxOverlay`（interleaved 模式，
 * 与 MapLibre 共享同一个 WebGL context 和相机矩阵）叠加渲染这类数据。
 *
 * 设计要点：
 * - **单一 overlay**：整张地图只创建一个 `MapboxOverlay`，所有 tiles3d /
 *   pointcloud 图层作为它的子 deck 图层聚合进去。每次增删改都重算
 *   `overlay.setProps({ layers })`。
 * - **懒加载**：deck.gl / loaders.gl 体积较大，全部走动态 import，只有真正
 *   用到 3D 数据时才加载，不拖累首屏。
 * - **按 defId 建索引**：一个 MapLayerDefinition 对应一个 deck 图层。
 *   MapEngine 在移除 / 切换可见性时按 defId 通知本管理器。
 * - **interleaved=true**：保证 3D Tiles 与 MapLibre 图层（含 fill-extrusion）
 *   正确做深度遮挡，且现有 preserveDrawingBuffer 截图/导出能一并抓到。
 */
import type maplibregl from 'maplibre-gl'

type Tiles3DSpec = {
  type: 'tiles3d'
  tilesetUrl: string
  visible: boolean
  pointSize?: number
  color?: string
  maximumScreenSpaceError?: number
  onTilesetLoad?: (info: { bbox?: [number, number, number, number]; pointCount?: number }) => void
}

type PointCloudSpec = {
  type: 'pointcloud'
  url: string
  format: 'las' | 'laz'
  coordinate?: 'lnglat' | 'meter-offset'
  origin?: [number, number, number?]
  visible: boolean
  pointSize?: number
  color?: string
}

type LayerSpec = Tiles3DSpec | PointCloudSpec

/** 把 #rrggbb 转成 deck.gl 的 [r,g,b] (0-255)；解析失败返回白色。 */
function hexToRgb(hex?: string): [number, number, number] {
  if (!hex) return [255, 255, 255]
  let t = hex.trim()
  if (t.startsWith('#')) t = t.slice(1)
  if (t.length === 3) t = t.split('').map((c) => c + c).join('')
  if (t.length < 6) return [255, 255, 255]
  const r = parseInt(t.slice(0, 2), 16)
  const g = parseInt(t.slice(2, 4), 16)
  const b = parseInt(t.slice(4, 6), 16)
  if ([r, g, b].some((v) => Number.isNaN(v))) return [255, 255, 255]
  return [r, g, b]
}

class DeckOverlayManager {
  private map: maplibregl.Map | null = null
  private overlay: any | null = null
  private specs = new Map<string, LayerSpec>()
  /** 动态 import 的 deck.gl / loaders.gl 模块缓存。 */
  private mods: {
    MapboxOverlay: any
    Tile3DLayer: any
    PointCloudLayer: any
    Tiles3DLoader: any
    LASLoader: any
    COORDINATE_SYSTEM: any
  } | null = null
  private loadingMods: Promise<void> | null = null

  /** 是否已有任何 3D 图层。MapEngine 可据此决定是否需要初始化 overlay。 */
  hasAny(): boolean {
    return this.specs.size > 0
  }

  private async ensureMods(): Promise<void> {
    if (this.mods) return
    if (!this.loadingMods) {
      this.loadingMods = (async () => {
        const [core, mapbox, geo, layers, tiles3d, las] = await Promise.all([
          import('@deck.gl/core'),
          import('@deck.gl/mapbox'),
          import('@deck.gl/geo-layers'),
          import('@deck.gl/layers'),
          import('@loaders.gl/3d-tiles'),
          import('@loaders.gl/las'),
        ])
        this.mods = {
          MapboxOverlay: (mapbox as any).MapboxOverlay,
          Tile3DLayer: (geo as any).Tile3DLayer,
          PointCloudLayer: (layers as any).PointCloudLayer,
          Tiles3DLoader: (tiles3d as any).Tiles3DLoader,
          LASLoader: (las as any).LASLoader,
          COORDINATE_SYSTEM: (core as any).COORDINATE_SYSTEM,
        }
      })()
    }
    await this.loadingMods
  }

  private async ensureOverlay(map: maplibregl.Map): Promise<void> {
    await this.ensureMods()
    if (this.map !== map) {
      // 地图实例变了（重建）——丢弃旧 overlay 引用。
      this.overlay = null
      this.map = map
    }
    if (!this.overlay && this.mods) {
      this.overlay = new this.mods.MapboxOverlay({ interleaved: true, layers: [] })
      // MapboxOverlay 实现了 IControl，MapLibre 可直接 addControl。
      ;(map as any).addControl(this.overlay)
    }
  }

  /** 新增或更新一个 3D Tiles 图层。 */
  upsertTiles3D(defId: string, map: maplibregl.Map, spec: Omit<Tiles3DSpec, 'type'>): void {
    this.specs.set(defId, { type: 'tiles3d', ...spec })
    void this.attachAndSync(map)
  }

  /** 新增或更新一个点云图层。 */
  upsertPointCloud(defId: string, map: maplibregl.Map, spec: Omit<PointCloudSpec, 'type'>): void {
    this.specs.set(defId, { type: 'pointcloud', ...spec })
    void this.attachAndSync(map)
  }

  /** 切换某图层可见性。 */
  setVisibility(defId: string, visible: boolean): void {
    const spec = this.specs.get(defId)
    if (!spec) return
    spec.visible = visible
    this.sync()
  }

  /** 移除某图层。 */
  remove(defId: string): void {
    if (this.specs.delete(defId)) {
      this.sync()
    }
  }

  private async attachAndSync(map: maplibregl.Map): Promise<void> {
    try {
      await this.ensureOverlay(map)
      this.sync()
    } catch (err) {
      console.error('[deckOverlay] failed to attach deck.gl overlay:', err)
    }
  }

  /** 根据当前 specs 重算 deck 图层数组并推给 overlay。 */
  private sync(): void {
    if (!this.overlay || !this.mods) return
    const { Tile3DLayer, PointCloudLayer, Tiles3DLoader, LASLoader, COORDINATE_SYSTEM } = this.mods
    const layers: any[] = []

    for (const [defId, spec] of this.specs) {
      if (spec.type === 'tiles3d') {
        layers.push(
          new Tile3DLayer({
            id: `deck-tiles3d-${defId}`,
            data: spec.tilesetUrl,
            loader: Tiles3DLoader,
            visible: spec.visible,
            pickable: true,
            pointSize: spec.pointSize ?? 1.5,
            _subLayerProps: spec.color
              ? { scenegraph: { _lighting: 'pbr' } }
              : undefined,
            loadOptions: {
              // 3D Tiles 的性能护栏：SSE 越大越省。
              '3d-tiles': {
                maximumScreenSpaceError: spec.maximumScreenSpaceError ?? 16,
              },
              tileset: {
                maximumScreenSpaceError: spec.maximumScreenSpaceError ?? 16,
              },
            },
            onTilesetLoad: (tileset: any) => {
              try {
                const center = tileset?.cartographicCenter
                const bbox = Array.isArray(tileset?.boundingVolume?.halfAxes)
                  ? undefined
                  : undefined
                spec.onTilesetLoad?.({
                  bbox:
                    center && Number.isFinite(center[0]) && Number.isFinite(center[1])
                      ? ([center[0], center[1], center[0], center[1]] as [number, number, number, number])
                      : bbox,
                  pointCount: tileset?.stats?.get?.('Points')?.count,
                })
              } catch {
                /* onTilesetLoad 只是尽力而为，出错不影响渲染 */
              }
            },
          }),
        )
      } else {
        const useMeterOffset = spec.coordinate === 'meter-offset' && spec.origin
        layers.push(
          new PointCloudLayer({
            id: `deck-pointcloud-${defId}`,
            data: spec.url,
            loaders: [LASLoader],
            visible: spec.visible,
            pickable: true,
            pointSize: spec.pointSize ?? 1.5,
            coordinateSystem: useMeterOffset
              ? COORDINATE_SYSTEM.METER_OFFSETS
              : COORDINATE_SYSTEM.LNGLAT,
            coordinateOrigin: useMeterOffset ? (spec.origin as number[]) : undefined,
            getColor: spec.color ? hexToRgb(spec.color) : [255, 255, 255],
            getNormal: [0, 0, 1],
          }),
        )
      }
    }

    this.overlay.setProps({ layers })
  }

  /** deck.gl 的对象拾取（供 identify 后续接入；MVP 未接线）。 */
  pick(x: number, y: number): any {
    if (!this.overlay) return null
    try {
      return this.overlay.pickObject({ x, y, radius: 4 })
    } catch {
      return null
    }
  }

  /** 地图销毁时清理。 */
  dispose(): void {
    try {
      if (this.overlay && this.map) {
        ;(this.map as any).removeControl(this.overlay)
      }
    } catch {
      /* ignore */
    }
    this.overlay = null
    this.map = null
    this.specs.clear()
  }
}

export const deckOverlay = new DeckOverlayManager()
