import { v4 as uuid } from 'uuid'
import { pythonClient } from '@/services/pythonClient'
import type {
  PivotAgentResult,
  PivotData,
  PivotDistributionBucket,
  PivotFieldDistribution,
  PivotFieldStat,
} from './types'

const MAX_ANALYSIS_ROWS = 2000
const MAX_ANALYSIS_COLUMNS = 48
const MAX_BUCKETS = 12
const PIVOT_RESULT_MARKER = '__OPENGIS_PIVOT_RESULT__'

export interface PivotAgentLog {
  id: number
  stream: 'info' | 'stdout' | 'stderr' | 'error'
  text: string
  ts: number
}

interface RunPivotAgentOptions {
  onLog?: (log: Omit<PivotAgentLog, 'id'>) => void
}

interface ScriptRunReply {
  ok: boolean
  output?: unknown
  duration_ms?: number | null
  error?: string
  logs?: string | null
}

export function computePivotAnalysis(data: PivotData): PivotAgentResult {
  if (data.raster) {
    const stats: PivotFieldStat[] = data.raster.rows.map((row) => ({
      field: String(row.band ?? row.Band ?? row['波段'] ?? 'Raster'),
      type: 'number',
      count: Number(row.valid_pixels ?? row['有效像元'] ?? 0) || 0,
      nullCount: Number(row.nodata_pixels ?? row['NoData 像元'] ?? 0) || 0,
      uniqueCount: 0,
      min: numericValue(row.min ?? row['最小值']),
      max: numericValue(row.max ?? row['最大值']),
      mean: numericValue(row.mean ?? row['均值']),
    }))
    return {
      stats,
      distributions: [],
      summary: `这是一个栅格数据集，共 ${data.raster.rows.length} 个波段。主要可查看每个波段的 min/max 与 NoData 情况，像元级分布需要读取原始 raster 采样后进一步计算。`,
      engine: 'typescript',
    }
  }

  const table = data.table
  if (!table) {
    return { stats: [], distributions: [], summary: '没有可分析的数据表。', engine: 'typescript' }
  }

  const rows = table.rows.slice(0, MAX_ANALYSIS_ROWS)
  const columns = table.columns.slice(0, MAX_ANALYSIS_COLUMNS)
  const stats = columns.map((field) => computeFieldStat(field, rows))
  const distributions = stats
    .filter((s) => s.count > 0)
    .slice(0, 16)
    .map((s) => computeDistribution(s.field, s.type, rows))

  const numericCount = stats.filter((s) => s.type === 'number').length
  const categoricalCount = stats.filter((s) => s.type === 'string' || s.type === 'boolean').length
  const sampledHint = table.sampled ? `当前基于 ${rows.length.toLocaleString()} 条样本进行分析。` : `当前分析 ${rows.length.toLocaleString()} 条记录。`
  const summary = `${sampledHint} 共识别 ${stats.length} 个字段，其中数值字段 ${numericCount} 个、分类/文本字段 ${categoricalCount} 个。优先关注空值多、唯一值异常高或数值范围异常的字段。`

  return { stats, distributions, summary, engine: 'typescript' }
}

export async function runPivotAgent(
  data: PivotData,
  workspacePath?: string | null,
  options: RunPivotAgentOptions = {},
): Promise<PivotAgentResult> {
  const emitLog = (text: string, stream: PivotAgentLog['stream'] = 'info') => {
    options.onLog?.({ stream, text, ts: Date.now() })
  }

  const fallback = computePivotAnalysis(data)
  if (!pythonClient.isConnected) {
    emitLog('Python 后端未连接，使用前端统计兜底。\n')
    return fallback
  }

  const payload = buildPythonPayload(data)
  const code = buildPythonAnalysisScript(payload)
  const runId = `pivot_${uuid().slice(0, 8)}`
  const payloadMode = (payload as { mode?: string; path?: string }).mode
  const payloadPath = (payload as { mode?: string; path?: string }).path
  emitLog(`开始 Agent 数据透视任务  run_id=${runId}\n`)
  emitLog(payloadMode === 'path'
    ? `Agent 将基于文件路径读取数据：${payloadPath}\n`
    : '目标没有可用文件路径，Agent 将基于前端提供的内存样本分析。\n')
  emitLog(`任务参数：mode=${payloadMode ?? 'unknown'} kind=${(payload as { kind?: string }).kind ?? 'unknown'} code=${code.length.toLocaleString()} chars timeout=45s\n`)

  let resolveDoneNotification: ((reply: ScriptRunReply) => void) | null = null
  const doneNotification = new Promise<ScriptRunReply>((resolve) => {
    resolveDoneNotification = resolve
  })

  const off = pythonClient.onNotification((method, params) => {
    if (!params || typeof params !== 'object') return
    const p = params as Record<string, unknown>
    if (p.run_id !== runId) return
    if (method === 'rpc.code.script_started') {
      emitLog('Python 运行环境已启动。\n')
    } else if (method === 'rpc.code.stdout' && typeof p.text === 'string') {
      if (p.text.includes(PIVOT_RESULT_MARKER)) {
        emitLog('收到 Python 分析结果 marker。\n')
      } else {
        emitLog(p.text, 'stdout')
      }
    } else if (method === 'rpc.code.stderr' && typeof p.text === 'string') {
      emitLog(p.text, 'stderr')
    } else if (method === 'rpc.code.script_done') {
      const ok = p.ok === true
      const duration = typeof p.duration_ms === 'number' ? `${p.duration_ms}ms` : '未知耗时'
      emitLog(
        ok ? `Python 分析完成 notification，耗时 ${duration}。\n` : `Python 分析失败 notification，耗时 ${duration}。\n`,
        ok ? 'info' : 'error',
      )
      resolveDoneNotification?.({
        ok,
        output: p.output,
        duration_ms: typeof p.duration_ms === 'number' ? p.duration_ms : null,
        error: typeof p.error === 'string' ? p.error : undefined,
        logs: typeof p.logs === 'string' ? p.logs : null,
      })
    }
  })

  try {
    emitLog('发送 rpc.code.run_script 请求，等待 response 或 script_done notification。\n')
    const rpcResponse = pythonClient.send<ScriptRunReply>('rpc.code.run_script', {
      run_id: runId,
      code,
      workspace_path: workspacePath ?? undefined,
      exec_timeout: 45,
    }, 70_000)
    rpcResponse.catch(() => {
      // If script_done wins the race, the transport response may still
      // settle later. Keep that late rejection from surfacing globally.
    })
    const raced = await Promise.race([
      rpcResponse.then((reply) => ({ source: 'response' as const, reply })),
      doneNotification.then((reply) => ({ source: 'notification' as const, reply })),
    ])
    const reply = raced.reply
    emitLog(`完成信号来源：${raced.source}，output=${summarizeUnknown(reply.output)} logs=${summarizeUnknown(reply.logs)}\n`)

    if (!reply.ok) {
      emitLog(`${reply.error ?? 'unknown error'}\n`, 'error')
      return { ...fallback, summary: `${fallback.summary} Python 分析失败，已使用前端统计兜底：${reply.error ?? 'unknown error'}` }
    }

    emitLog('开始解析 Agent 返回 JSON contract。\n')
    const parsed = normalizePythonOutput(reply.output, reply.logs)
    if (!parsed) {
      emitLog(`Python 返回结果无法解析，使用前端统计兜底。output=${summarizeUnknown(reply.output)} logs=${summarizeUnknown(reply.logs)}\n`, 'error')
      return fallback
    }
    emitLog(`前端解析完成：distributions=${parsed.distributions.length} stats=${parsed.stats.length} summary=${parsed.summary.length} chars。\n`)
    return {
      stats: Array.isArray(parsed.stats) ? parsed.stats : fallback.stats,
      distributions: Array.isArray(parsed.distributions) ? parsed.distributions : fallback.distributions,
      summary: typeof parsed.summary === 'string' ? parsed.summary : fallback.summary,
      durationMs: reply.duration_ms ?? null,
      engine: 'python',
    }
  } catch (err) {
    emitLog(`${err instanceof Error ? err.message : String(err)}\n`, 'error')
    return {
      ...fallback,
      summary: `${fallback.summary} Python 后台分析不可用，已使用前端统计兜底：${err instanceof Error ? err.message : String(err)}`,
    }
  } finally {
    emitLog('清理 Agent 透视事件订阅。\n')
    off()
  }
}

function buildPythonPayload(data: PivotData) {
  const target = data.target
  const layerPath = data.layer?.meta?.filePath
  if (target.kind === 'file') {
    return {
      mode: 'path',
      kind: data.dataKind,
      title: data.title,
      path: target.path,
      extension: target.extension,
      size: target.size,
    }
  }
  if (layerPath) {
    return {
      mode: 'path',
      kind: data.dataKind,
      title: data.title,
      path: layerPath,
      extension: data.layer?.meta?.extension ?? '',
      size: data.layer?.meta?.fileSize ?? null,
    }
  }
  if (data.raster) {
    return {
      mode: 'sample',
      kind: 'raster',
      title: data.title,
      raster_rows: data.raster.rows,
      meta: data.raster.meta,
    }
  }
  const table = data.table
  return {
    mode: 'sample',
    kind: data.dataKind,
    title: data.title,
    columns: table?.columns.slice(0, MAX_ANALYSIS_COLUMNS) ?? [],
    rows: table?.rows.slice(0, MAX_ANALYSIS_ROWS) ?? [],
    total_rows: table?.totalRows,
    sampled: table?.sampled ?? false,
  }
}

function buildPythonAnalysisScript(payload: unknown): string {
  const body = `
import csv, json, math, os, statistics, xml.etree.ElementTree as ET

payload = json.loads(${JSON.stringify(JSON.stringify(payload))})
MAX_ROWS = 5000
MAX_COLUMNS = 80
MAX_BUCKETS = 12

def log(stage, message):
    print(f"[pivot:{stage}] {message}", flush=True)

log("init", f"mode={payload.get('mode')} kind={payload.get('kind')} title={payload.get('title')}")

def is_null(v):
    return v is None or v == "" or (isinstance(v, float) and math.isnan(v))

def as_number(v):
    if is_null(v) or isinstance(v, bool):
        return None
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except Exception:
        return None

def coerce(v):
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    text = str(v).strip()
    if not text:
        return None
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        n = float(text)
        if math.isfinite(n):
            return n
    except Exception:
        pass
    return v

def normalize_record(record):
    out = {}
    for k, v in dict(record).items():
        if k is None:
            continue
        if hasattr(v, "item"):
            try:
                v = v.item()
            except Exception:
                pass
        if isinstance(v, (dict, list, tuple)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        out[str(k)] = coerce(v)
    return out

def find_record_array(value):
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = find_record_array(child)
            if found is not None:
                return found
    return None

def load_csv_table(path, ext):
    log("read", f"open csv/tsv path={path}")
    rows = []
    total_rows = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\\t" if ext == ".tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\\t;|")
            delimiter = dialect.delimiter
        except Exception:
            pass
        log("read", f"csv delimiter={repr(delimiter)}")
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            total_rows += 1
            if len(rows) < MAX_ROWS:
                rows.append(normalize_record(row))
    log("read", f"csv rows_sampled={len(rows)} total_rows={total_rows}")
    return rows, total_rows, "table"

def load_json_table(path, ext):
    log("read", f"open json path={path}")
    with open(path, "r", encoding="utf-8") as f:
        value = json.load(f)
    if isinstance(value, dict) and value.get("type") == "FeatureCollection":
        features = value.get("features") or []
        log("read", f"geojson features={len(features)}")
        rows = []
        for i, feature in enumerate(features[:MAX_ROWS]):
            props = normalize_record(feature.get("properties") or {})
            props["__fid"] = feature.get("id", i)
            props["__geometry"] = ((feature.get("geometry") or {}).get("type"))
            rows.append(props)
        return rows, len(features), "vector"
    records = find_record_array(value)
    if records is None:
        raise RuntimeError("JSON 不是 GeoJSON，也没有找到可表格化的对象数组。")
    log("read", f"json records={len(records)}")
    return [normalize_record(r) for r in records[:MAX_ROWS]], len(records), "table"

def load_kml_table(path):
    log("read", f"open kml path={path}")
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(path).getroot()
    placemarks = root.findall(".//kml:Placemark", ns) or root.findall(".//Placemark")
    log("read", f"kml placemarks={len(placemarks)}")
    rows = []
    for i, pm in enumerate(placemarks[:MAX_ROWS]):
        row = {"__fid": i}
        name = pm.find("kml:name", ns) or pm.find("name")
        if name is not None and name.text:
            row["name"] = name.text
        desc = pm.find("kml:description", ns) or pm.find("description")
        if desc is not None and desc.text:
            row["description"] = desc.text
        for data in pm.findall(".//kml:Data", ns) + pm.findall(".//Data"):
            key = data.attrib.get("name")
            val = data.find("kml:value", ns) or data.find("value")
            if key:
                row[key] = coerce(val.text if val is not None else None)
        for data in pm.findall(".//kml:SimpleData", ns) + pm.findall(".//SimpleData"):
            key = data.attrib.get("name")
            if key:
                row[key] = coerce(data.text)
        rows.append(normalize_record(row))
    return rows, len(placemarks), "vector"

def load_geodataframe_table(path):
    try:
        log("import", "import geopandas")
        import geopandas as gpd
    except Exception as exc:
        raise RuntimeError(f"读取该矢量格式需要 geopandas/fiona/pyogrio：{exc}")
    log("read", f"geopandas.read_file path={path}")
    gdf = gpd.read_file(path)
    total_rows = len(gdf)
    log("read", f"geodataframe rows={total_rows} columns={len(gdf.columns)}")
    if "geometry" in gdf.columns:
        geom_types = gdf.geometry.geom_type.astype(str).tolist()
        df = gdf.drop(columns=["geometry"])
    else:
        geom_types = []
        df = gdf
    rows = []
    for i, record in enumerate(df.head(MAX_ROWS).to_dict(orient="records")):
        row = normalize_record(record)
        row["__fid"] = i
        if i < len(geom_types):
            row["__geometry"] = geom_types[i]
        rows.append(row)
    return rows, total_rows, "vector"

def load_raster_stats(path):
    try:
        log("import", "import rasterio")
        import rasterio
    except Exception as exc:
        raise RuntimeError(f"读取栅格统计需要 rasterio：{exc}")
    stats = []
    log("read", f"rasterio.open path={path}")
    with rasterio.open(path) as src:
        log("read", f"raster width={src.width} height={src.height} bands={src.count} crs={src.crs}")
        for band_index in range(1, src.count + 1):
            log("stats", f"read raster band={band_index}")
            arr = src.read(band_index, masked=True)
            valid = int(arr.count())
            nulls = int(arr.size - valid)
            if valid:
                values = arr.compressed()
                mn = float(values.min())
                mx = float(values.max())
                mean = float(values.mean())
            else:
                mn = mx = mean = None
            stats.append({
                "field": f"band_{band_index}",
                "type": "number",
                "count": valid,
                "nullCount": nulls,
                "uniqueCount": 0,
                "min": mn,
                "max": mx,
                "mean": mean,
            })
    return stats

def load_rows_from_path(payload):
    path = payload.get("path")
    ext = (payload.get("extension") or os.path.splitext(path or "")[1]).lower()
    if not path:
        raise RuntimeError("missing file path")
    log("route", f"path={path} ext={ext}")
    if ext in (".csv", ".tsv"):
        return load_csv_table(path, ext)
    if ext in (".json", ".geojson"):
        return load_json_table(path, ext)
    if ext == ".kml":
        return load_kml_table(path)
    if ext in (".shp", ".gpkg", ".gml"):
        return load_geodataframe_table(path)
    if ext in (".tif", ".tiff"):
        return None, None, "raster"
    raise RuntimeError(f"暂不支持该格式的 Agent 透视：{ext}")

def field_stat(field, rows):
    values = [r.get(field) for r in rows]
    non_null = [v for v in values if not is_null(v)]
    nums = [as_number(v) for v in non_null]
    nums = [v for v in nums if v is not None]
    unique = len({str(v) for v in non_null})
    if non_null and len(nums) >= max(3, int(len(non_null) * 0.7)):
        return {
            "field": field, "type": "number", "count": len(non_null),
            "nullCount": len(values) - len(non_null), "uniqueCount": unique,
            "min": min(nums), "max": max(nums), "mean": sum(nums) / len(nums),
        }
    bools = [v for v in non_null if isinstance(v, bool) or str(v).lower() in ("true", "false")]
    typ = "boolean" if non_null and len(bools) == len(non_null) else "string"
    return {
        "field": field, "type": typ, "count": len(non_null),
        "nullCount": len(values) - len(non_null), "uniqueCount": unique,
        "min": min([str(v) for v in non_null]) if non_null else None,
        "max": max([str(v) for v in non_null]) if non_null else None,
    }

def distribution(field, typ, rows, max_buckets=12):
    vals = [r.get(field) for r in rows if not is_null(r.get(field))]
    if not vals:
        return {"field": field, "type": typ, "buckets": []}
    if typ == "number":
        nums = [as_number(v) for v in vals]
        nums = [v for v in nums if v is not None]
        if not nums:
            return {"field": field, "type": typ, "buckets": []}
        lo, hi = min(nums), max(nums)
        if lo == hi:
            buckets = [{"label": str(round(lo, 4)), "count": len(nums), "probability": 1}]
        else:
            bucket_count = min(max_buckets, max(4, int(math.sqrt(len(nums)))))
            counts = [0] * bucket_count
            for n in nums:
                idx = min(bucket_count - 1, int((n - lo) / (hi - lo) * bucket_count))
                counts[idx] += 1
            buckets = []
            for i, c in enumerate(counts):
                a = lo + (hi - lo) * i / bucket_count
                b = lo + (hi - lo) * (i + 1) / bucket_count
                buckets.append({"label": f"{a:.3g}-{b:.3g}", "count": c, "probability": c / len(nums)})
        return {"field": field, "type": typ, "buckets": buckets}
    counts = {}
    for v in vals:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:max_buckets]
    total = len(vals)
    return {"field": field, "type": typ, "buckets": [{"label": k, "count": c, "probability": c / total} for k, c in top]}

if payload.get("mode") == "path" and (payload.get("extension") or "").lower() in (".tif", ".tiff"):
    log("stage", "start raster stats")
    stats = load_raster_stats(payload.get("path"))
    log("stage", f"raster stats done bands={len(stats)}")
    result = {
        "distributions_json": [],
        "stats_json": stats,
        "summary_json": f"{payload.get('title')} 已由 Agent 读取文件路径并完成栅格统计，共 {len(stats)} 个波段。"
    }
elif payload.get("kind") == "raster":
    log("stage", "start raster sample stats")
    stats = []
    for row in payload.get("raster_rows", []):
        stats.append({
            "field": str(row.get("band", "Raster")),
            "type": "number",
            "count": int(row.get("valid_pixels") or 0),
            "nullCount": int(row.get("nodata_pixels") or 0),
            "uniqueCount": 0,
            "min": row.get("min"),
            "max": row.get("max"),
            "mean": row.get("mean"),
        })
    result = {
        "distributions_json": [],
        "stats_json": stats,
        "summary_json": f"栅格 {payload.get('title')} 已完成后台统计。共 {len(stats)} 个波段，重点查看各波段 min/max、NoData 与有效像元数量。"
    }
else:
    if payload.get("mode") == "path":
        log("stage", "start loading tabular/vector rows from path")
        rows, total_rows, loaded_kind = load_rows_from_path(payload)
        rows = rows or []
    else:
        log("stage", "use frontend sample rows")
        rows = payload.get("rows", [])
        total_rows = payload.get("total_rows") or len(rows)
        loaded_kind = payload.get("kind")
    log("stage", f"rows_ready sampled_rows={len(rows)} total_rows={total_rows} loaded_kind={loaded_kind}")
    columns = list({k for row in rows for k in row.keys()})[:MAX_COLUMNS]
    log("stage", f"columns_detected={len(columns)}")
    log("stats", "compute field statistics")
    stats = [field_stat(c, rows) for c in columns]
    log("dist", "compute field distributions")
    dists = [distribution(s["field"], s["type"], rows) for s in stats[:16] if s.get("count", 0) > 0]
    numeric = len([s for s in stats if s.get("type") == "number"])
    categorical = len([s for s in stats if s.get("type") in ("string", "boolean")])
    sampled = "样本" if (payload.get("mode") != "path" and payload.get("sampled")) or (total_rows and total_rows > len(rows)) else "全量"
    result = {
        "distributions_json": dists,
        "stats_json": stats,
        "summary_json": f"{payload.get('title')} 已由 Agent 读取{('文件路径' if payload.get('mode') == 'path' else '内存样本')}并完成 {sampled} 透视：分析 {len(rows)} / {total_rows or len(rows)} 条记录、{len(stats)} 个字段，其中数值字段 {numeric} 个，分类/文本字段 {categorical} 个。建议优先检查空值率高和唯一值异常的字段。"
}

log("serialize", f"contract distributions={len(result.get('distributions_json', []))} stats={len(result.get('stats_json', []))} summary_chars={len(result.get('summary_json', ''))}")
__pivot_result_json = json.dumps(result, ensure_ascii=False, allow_nan=False)
log("serialize", f"json_chars={len(__pivot_result_json)}")
`.trim()
  return `exec(${JSON.stringify(body)}, globals()) or __pivot_result_json`
}

function normalizePythonOutput(output: unknown, logs?: string | null): PivotAgentResult | null {
  const fromLogs = parseMarkedResult(logs)
  if (fromLogs) return fromLogs
  if (!output) return null
  if (typeof output === 'object') return normalizeAgentContract(output)
  if (typeof output === 'string') {
    const marked = parseMarkedResult(output)
    if (marked) return marked
    try {
      return normalizeAgentContract(JSON.parse(output))
    } catch {
      return null
    }
  }
  return null
}

function parseMarkedResult(text?: string | null): PivotAgentResult | null {
  if (!text) return null
  const idx = text.lastIndexOf(PIVOT_RESULT_MARKER)
  if (idx < 0) return null
  const line = text.slice(idx + PIVOT_RESULT_MARKER.length).split(/\r?\n/, 1)[0]?.trim()
  if (!line) return null
  try {
    return normalizeAgentContract(JSON.parse(line))
  } catch {
    return null
  }
}

function normalizeAgentContract(value: unknown): PivotAgentResult | null {
  if (!value || typeof value !== 'object') return null
  const obj = value as Record<string, unknown>
  const distributions = obj.distributions_json ?? obj.distributions
  const stats = obj.stats_json ?? obj.stats
  const summary = obj.summary_json ?? obj.summary
  if (!Array.isArray(distributions) || !Array.isArray(stats) || typeof summary !== 'string') {
    return null
  }
  return {
    distributions: distributions as PivotFieldDistribution[],
    stats: stats as PivotFieldStat[],
    summary,
    engine: 'python',
  }
}

function summarizeUnknown(value: unknown): string {
  if (value === null || value === undefined) return String(value)
  let text: string
  try {
    text = typeof value === 'string' ? value : JSON.stringify(value)
  } catch {
    text = String(value)
  }
  return text.length > 240 ? `${text.slice(0, 240)}...` : text
}

function computeFieldStat(field: string, rows: Record<string, unknown>[]): PivotFieldStat {
  const values = rows.map((row) => row[field])
  const nonNull = values.filter((v) => !isNullValue(v))
  const numeric = nonNull.map(toNumber).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  const unique = new Set(nonNull.map((v) => String(v)))
  const numericLikely = nonNull.length > 0 && numeric.length >= Math.max(3, Math.floor(nonNull.length * 0.7))

  if (numericLikely) {
    const sum = numeric.reduce((acc, v) => acc + v, 0)
    return {
      field,
      type: 'number',
      count: nonNull.length,
      nullCount: values.length - nonNull.length,
      uniqueCount: unique.size,
      min: Math.min(...numeric),
      max: Math.max(...numeric),
      mean: sum / numeric.length,
    }
  }

  const booleanLikely = nonNull.length > 0 && nonNull.every((v) => typeof v === 'boolean' || ['true', 'false'].includes(String(v).toLowerCase()))
  const strings = nonNull.map((v) => String(v))
  return {
    field,
    type: booleanLikely ? 'boolean' : 'string',
    count: nonNull.length,
    nullCount: values.length - nonNull.length,
    uniqueCount: unique.size,
    min: strings.length ? strings.reduce((a, b) => a.localeCompare(b) <= 0 ? a : b) : undefined,
    max: strings.length ? strings.reduce((a, b) => a.localeCompare(b) >= 0 ? a : b) : undefined,
  }
}

function computeDistribution(field: string, type: PivotFieldStat['type'], rows: Record<string, unknown>[]): PivotFieldDistribution {
  const values = rows.map((row) => row[field]).filter((v) => !isNullValue(v))
  if (values.length === 0) return { field, type, buckets: [] }

  if (type === 'number') {
    const nums = values.map(toNumber).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    if (nums.length === 0) return { field, type, buckets: [] }
    const min = Math.min(...nums)
    const max = Math.max(...nums)
    if (min === max) {
      return { field, type, buckets: [{ label: formatValue(min), count: nums.length, probability: 1 }] }
    }
    const bucketCount = Math.min(MAX_BUCKETS, Math.max(4, Math.round(Math.sqrt(nums.length))))
    const counts = Array.from({ length: bucketCount }, () => 0)
    for (const value of nums) {
      const idx = Math.min(bucketCount - 1, Math.floor(((value - min) / (max - min)) * bucketCount))
      counts[idx]++
    }
    const buckets = counts.map((count, index) => {
      const a = min + ((max - min) * index) / bucketCount
      const b = min + ((max - min) * (index + 1)) / bucketCount
      return { label: `${formatValue(a)}-${formatValue(b)}`, count, probability: count / nums.length }
    })
    return { field, type, buckets }
  }

  const counts = new Map<string, number>()
  for (const value of values) {
    const key = String(value)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const buckets: PivotDistributionBucket[] = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_BUCKETS)
    .map(([label, count]) => ({ label, count, probability: count / values.length }))
  return { field, type, buckets }
}

function isNullValue(value: unknown): boolean {
  return value === null || value === undefined || value === '' || (typeof value === 'number' && Number.isNaN(value))
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'boolean' || value === null || value === undefined) return null
  const text = String(value).trim()
  if (!text) return null
  const n = Number(text)
  return Number.isFinite(n) ? n : null
}

function numericValue(value: unknown): number | undefined {
  const n = toNumber(value)
  return n === null ? undefined : n
}

function formatValue(value: number): string {
  if (Math.abs(value) >= 1000 || Math.abs(value) < 0.01) return value.toExponential(2)
  return Number(value.toFixed(3)).toString()
}
