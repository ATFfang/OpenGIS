export interface WorkerLog {
  ts: number
  stream: string
  text: string
}

export interface WorkerResources {
  available?: boolean
  cpu_percent?: number | null
  rss_bytes?: number | null
  rss_mb?: number | null
  elapsed?: string | null
  sampled_at?: number
  error?: string
}

export interface ResidentWorker {
  id: string
  name: string
  description?: string
  status: string
  pid?: number | null
  folder?: string
  script_path?: string
  last_error?: string | null
  created_at?: number
  updated_at?: number
  started_at?: number | null
  stopped_at?: number | null
  returncode?: number | null
  resources?: WorkerResources
  logs?: WorkerLog[]
  manifest?: {
    schema_version?: number
    kind?: string
    entrypoint?: string
    layers?: Array<Record<string, unknown>>
  }
  package?: {
    schema_version?: number
    entrypoint?: string
    has_readme?: boolean
    has_config?: boolean
    src_files?: string[]
  }
}

export interface ResourceSample {
  ts: number
  cpu: number | null
  memory: number | null
}
