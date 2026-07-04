/**
 * RPC 参数 zod schema — 所有 handler 复用
 *
 * 为什么集中放一处：
 *   - 和 INTERFACE.md §1 一一对应，便于对账
 *   - Stage 3 若要把 schema 序列化成 JSON Schema 给 Python 参考，一次导出即可
 *
 * 命名规则：`<RpcMethodBaseName>Schema`，如 `AddLayerSchema`。
 */

import { z } from 'zod';

// ─────────────────────────────────────────────────────────────────────
// 通用
// ─────────────────────────────────────────────────────────────────────

export const BBoxSchema = z
  .tuple([z.number(), z.number(), z.number(), z.number()])
  .describe('[minX, minY, maxX, maxY]');

export const LayerStyleSchema = z.object({
  type: z.enum(['circle', 'line', 'fill', 'raster', 'symbol']),
  paint: z.record(z.unknown()).optional(),
  layout: z.record(z.unknown()).optional(),
});

const LayerIdSchema = z.string().min(1);

// ─────────────────────────────────────────────────────────────────────
// §1.1 rpc.ui.map.*
// ─────────────────────────────────────────────────────────────────────

export const AddLayerSchema = z.object({
  path: z.string().min(1),
  name: z.string().optional(),
  style: LayerStyleSchema.optional(),
  visible: z.boolean().optional(),
});

export const AddLayerFromGeoJsonSchema = z.object({
  geojson: z.unknown(),
  name: z.string().min(1),
  style: LayerStyleSchema.optional(),
  visible: z.boolean().optional(),
});

export const AddRasterFromUrlSchema = z.object({
  url: z.string().url(),
  name: z.string().min(1),
  tile_type: z.enum(['xyz', 'wmts', 'cog']),
  bounds: BBoxSchema.optional(),
});

// ─────────────────────────────────────────────────────────────────────
// §1.1bis 2026-04-24 新增：TIFF / renderer 切换 / 导出
// ─────────────────────────────────────────────────────────────────────

/**
 * 按文件路径加载 GeoTIFF。只支持 EPSG:4326 / 3857；其它 CRS 需要调用方
 * 预先 warp。TS 前端自己解析（geotiff.js），不走 Python。
 */
export const AddRasterFromFileSchema = z.object({
  path: z.string().min(1),
  layer_id: z.string().min(1).optional(),
  name: z.string().optional(),
  visible: z.boolean().optional(),
  opacity: z.number().min(0).max(1).optional(),
});

/** 分级专题 / 分类专题 / 热力图 / 聚合 / 3D 拔起 的配置 —— 与 LayerStyle 对应字段一致 */
const GraduatedSchema = z.object({
  field: z.string().min(1),
  method: z.string().transform((v) => v.replace(/_/g, '-')).pipe(
    z.enum(['quantile', 'equal-interval', 'jenks', 'manual']),
  ),
  classes: z.number().int().min(2).max(12).optional(),
  breaks: z.array(z.number()).optional(),
  palette: z.array(z.string()).optional(),
});

const CategorizedSchema = z.object({
  field: z.string().min(1),
  colors: z.record(z.string()).optional(),
  maxCategories: z.number().int().min(1).max(64).optional(),
  otherColor: z.string().optional(),
});

const HeatmapSchema = z.object({
  weightField: z.string().optional(),
  radius: z.number().positive().optional(),
  intensity: z.number().min(0).optional(),
});

const ClusterSchema = z.object({
  radius: z.number().positive().optional(),
  maxZoom: z.number().min(0).max(22).optional(),
});

const ExtrusionSchema = z.object({
  heightField: z.string().min(1),
  heightMultiplier: z.number().positive().optional(),
  baseField: z.string().optional(),
});

/**
 * 切换一个现有图层的 renderType（渲染模式）。若要把 renderType 从 fill 换
 * 到 graduated，必须同时提供对应的配置段（graduated/categorized/...）。
 */
export const SetLayerRendererSchema = z.object({
  layer_id: LayerIdSchema,
  renderer: z.enum([
    'fill',
    'line',
    'circle',
    'heatmap',
    'graduated',
    'categorized',
    'cluster',
    'extrusion',
    'raster',
  ]),
  graduated: GraduatedSchema.optional(),
  categorized: CategorizedSchema.optional(),
  heatmap: HeatmapSchema.optional(),
  cluster: ClusterSchema.optional(),
  extrusion: ExtrusionSchema.optional(),
});

/**
 * 导出当前地图。返回 base64（默认）或写到 save_path。
 */
export const ExportMapSchema = z.object({
  format: z.enum(['png', 'jpg']).optional(),
  dpi_scale: z.number().min(1).max(4).optional(),
  quality: z.number().min(0).max(1).optional(),
  /** 可选：如果提供且在 Electron 下，会把结果写到该路径，不返回 base64。 */
  save_path: z.string().optional(),
  /** 可选：导出前切换到指定底图（basemap id），导出后恢复原底图。 */
  basemap_id: z.string().optional(),
  /** 可选：导出前只显示指定图层（layer id 列表），导出后恢复。传空数组 = 隐藏所有图层。 */
  visible_layers: z.array(z.string()).optional(),
  /** 可选：导出前隐藏底图（纯白/纯黑背景），导出后恢复。 */
  hide_basemap: z.boolean().optional(),
});

/**
 * 把任意本地图片（matplotlib PNG 等，无地理坐标）覆盖到地图上。
 * 若调用方不提供 bbox，handler 会以"当前视口中心 ± 2°"造一个默认矩形，
 * 用户在前端可以拖拽 / 缩放调整。
 */
export const AddImageOverlaySchema = z.object({
  path: z.string().min(1),
  name: z.string().optional(),
  bbox: BBoxSchema.optional(),
  opacity: z.number().min(0).max(1).optional(),
});


export const RemoveLayerSchema = z.object({ layer_id: LayerIdSchema });

export const SetLayerStyleSchema = z.object({
  layer_id: LayerIdSchema,
  style: LayerStyleSchema,
});

export const SetLayerVisibilitySchema = z.object({
  layer_id: LayerIdSchema,
  visible: z.boolean(),
});

export const ZoomToLayerSchema = z.object({
  layer_id: LayerIdSchema,
  padding: z.number().optional(),
});

export const ZoomToBBoxSchema = z.object({
  bbox: BBoxSchema,
  padding: z.number().optional(),
});

export const FlyToSchema = z.object({
  center: z.tuple([z.number(), z.number()]),
  zoom: z.number().optional(),
  pitch: z.number().optional(),
  bearing: z.number().optional(),
});

export const SetBasemapSchema = z.object({
  basemap: z.union([
    z.enum(['osm', 'satellite', 'dark', 'light']),
    z.object({ style_url: z.string().url() }),
  ]),
});

export const SetBasemapVisibilitySchema = z.object({
  visible: z.boolean(),
});

export const ListLayersSchema = z.object({}).passthrough();

export const GetLayerSchema = z.object({ layer_id: LayerIdSchema });

export const QueryFeaturesSchema = z.object({
  layer_id: LayerIdSchema,
  filter: z
    .object({
      attribute: z
        .array(
          z.object({
            field: z.string(),
            op: z.enum(['=', '!=', '>', '<', 'contains']),
            value: z.unknown(),
          }),
        )
        .optional(),
      bbox: BBoxSchema.optional(),
      point: z.tuple([z.number(), z.number()]).optional(),
    })
    .optional(),
  limit: z.number().int().positive().optional(),
});

// ─────────────────────────────────────────────────────────────────────
// §1.2 rpc.ui.chat.*
// ─────────────────────────────────────────────────────────────────────

export const ShowTextSchema = z.object({
  text: z.string(),
  level: z.enum(['info', 'warning', 'error']).optional(),
});

export const ShowImageSchema = z.object({
  path: z.string().min(1),
  caption: z.string().optional(),
  run_id: z.string().optional(),
});

export const ShowTableSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  caption: z.string().optional(),
  max_rows: z.number().int().positive().optional(),
});

/**
 * 计划 / TODO 清单更新。后端 `update_plan` skill 调用本 method，每次携带
 * 完整的步骤列表（声明式全量替换）。前端按 `plan_id` upsert 同一张卡片。
 */
export const PlanStepStatusSchema = z.enum([
  'pending',
  'in_progress',
  'done',
  'skipped',
  'failed',
]);

export const PlanUpdateSchema = z.object({
  plan_id: z.string().min(1),
  title: z.string().optional(),
  steps: z
    .array(
      z.object({
        id: z.string().min(1),
        title: z.string().min(1),
        status: PlanStepStatusSchema,
        note: z.string().optional(),
      }),
    )
    .min(1)
    .max(50),
  run_id: z.string().optional(),
  /** When true, the plan is from a workflow — suppress detailed events. */
  workflow: z.boolean().optional(),
});

/**
 * 子智能体（sub-agent）运行状态卡。后端 run_subagent / run_subagents skill
 * 在委派子任务时调用本 method，前端按 `subagent_id` upsert 同一张卡片。
 * 只携带任务标题与状态，不携带子智能体的内部步骤/输出（上下文隔离的本意）。
 */
export const SubagentTaskStatusSchema = z.enum(['running', 'done', 'failed', 'cancelled']);

export const SubagentUpdateSchema = z.object({
  subagent_id: z.string().min(1),
  status: z.enum(['running', 'done', 'failed', 'cancelled']),
  parallel: z.boolean().optional(),
  tasks: z
    .array(
      z.object({
        title: z.string(),
        status: SubagentTaskStatusSchema,
      }),
    )
    .max(8),
  ok_count: z.number().int().nonnegative().optional(),
  total: z.number().int().nonnegative().optional(),
  run_id: z.string().optional(),
});

// ─────────────────────────────────────────────────────────────────────
// §1.3 rpc.ui.ask.*
// ─────────────────────────────────────────────────────────────────────

export const ApproveCodeSchema = z.object({
  request_id: z.string().optional(),
  tool_name: z.string().optional(),
  run_id: z.string().min(1),
  step: z.number().int().nonnegative(),
  code: z.string(),
  risky_operations: z.array(z.string()),
  explanation: z.string().optional(),
  timeout_seconds: z.number().positive().optional(),
});

export const AskChooseSchema = z.object({
  question: z.string().min(1),
  options: z.array(z.string()).min(1),
  multi_select: z.boolean().optional(),
  timeout_seconds: z.number().positive().optional(),
});

export const AskTextSchema = z.object({
  question: z.string().min(1),
  placeholder: z.string().optional(),
  default: z.string().optional(),
  timeout_seconds: z.number().positive().optional(),
});

export const AskConfirmSchema = z.object({
  request_id: z.string().optional(),
  tool_name: z.string().optional(),
  question: z.string().min(1),
  reason: z.string().optional(),
  danger: z.boolean().optional(),
  timeout_seconds: z.number().positive().optional(),
});

// ─────────────────────────────────────────────────────────────────────
// §1.4 rpc.ui.fs.*
// ─────────────────────────────────────────────────────────────────────

export const GetWorkspaceSchema = z.object({}).passthrough();

export const ListAssetsSchema = z.object({
  pattern: z.string().optional(),
});

export const RefreshAssetsSchema = z.object({
  path: z.string().min(1).optional(),
  reason: z.string().optional(),
}).passthrough();

export const OpenExternalSchema = z.object({
  path: z.string().min(1),
  mode: z.enum(['default', 'code_viewer', 'file_manager']).optional(),
});

// ─────────────────────────────────────────────────────────────────────
// §1.5 rpc.agent.* / rpc.workspace.* （TS→Py 发起方签名，handler 位置占位）
// ─────────────────────────────────────────────────────────────────────

export const AgentInterruptSchema = z.object({
  run_id: z.string().min(1),
});

export const AgentGetStatusSchema = z.object({}).passthrough();

export const AgentSetLlmConfigSchema = z.object({
  provider: z.string().min(1),
  model: z.string().min(1),
  api_key: z.string().min(1),
  base_url: z.string().url().optional(),
  temperature: z.number().optional(),
  max_tokens: z.number().int().positive().optional(),
});

export const WorkspaceRollbackSchema = z.object({
  commit: z.string().min(1),
});

export const AgentHelloSchema = z.object({
  python_version: z.string(),
  supported_protocol: z.string(),
});
