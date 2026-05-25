/**
 * Chat / Agent UI types — owned by the frontend, decoupled from any
 * upstream Agent framework. Reflects what the OpenGIS Python CodeAgent
 * actually emits over IPC.
 */

// ── Provider list (mirrors what the backend's litellm understands) ──
export type ApiProvider =
  | 'openai'
  | 'openai-compatible'
  | 'anthropic'
  | 'deepseek'
  | 'qwen'
  | 'doubao'
  | 'moonshot'
  | 'gemini'
  | 'ollama'
  | 'lmstudio'
  | 'zhipu'
  | 'groq'
  | 'mistral'
  | 'xai'
  | 'minimax'
  | 'openrouter'
  | 'custom'

export const DEFAULT_API_PROVIDER: ApiProvider = 'openai'

// ── UI message taxonomy (matches CodeAgent event stream) ──
export type SayType =
  | 'text'             // Plain text response from the agent
  | 'reasoning'        // Thinking / planning step
  | 'user_feedback'    // Echoes the user's input
  | 'tool'            // A tool/skill invocation summary
  | 'code'             // A Python code block emitted by the agent
  | 'code_result'      // Stdout/stderr from executing a code block
  | 'image'            // Inline image (e.g. matplotlib plot saved by save_plot)
  | 'command'          // A frontend command (map.addLayer etc.)
  | 'progress'         // Execution progress indicator
  | 'thinking'         // 🧠 DEPRECATED — "Calling LLM" indicator, UI no longer renders it. Kept for old-data compatibility.
  | 'error'
  | 'followup'
  | 'completion_result'
  | 'mistake_limit_reached'
  | 'max_steps_reached'
  | 'api_req_started'
  | 'mcp_server_response'

export type AskType =
  | 'followup'
  | 'tool'
  | 'command'
  | 'completion_result'
  | 'resume_task'

export interface UIMessage {
  ts: number
  type: 'say' | 'ask'
  say?: SayType
  ask?: AskType
  text?: string
  partial?: boolean
  images?: string[]
  files?: string[]

  // Tool / skill invocation
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolStatus?: 'pending' | 'running' | 'completed' | 'failed'

  // GIS-specific result fields surfaced into the chat
  geojson?: unknown
  chartConfig?: unknown
  tableData?: unknown[]

  // CodeAgent step metadata — filled for say='code' / 'code_result'.
  // `scriptPath` is relative to the opened workspace (or the run directory
  // when no workspace is open) and is safe to pass to openFileAsTab.
  stepNumber?: number
  scriptPath?: string
  scriptAbsPath?: string
  runId?: string
  codeError?: string | null

  // Whether a backend command finished (for command-type messages)
  commandCompleted?: boolean

  // Soft-stop payload — filled for say='max_steps_reached'.
  maxStepsInfo?: {
    maxSteps: number
    stepCount: number
    summary: string
  }

  // Progress indicator — filled for say='progress'.
  progressStage?: string
  progressDetail?: string

  // Model attribution
  modelInfo?: {
    modelId?: string
    providerId?: string
    mode?: string
  }
}

// API-request metadata stored as JSON inside an `api_req_started` say message.
export interface ApiReqInfo {
  request?: string
  tokensIn?: number
  tokensOut?: number
  cacheWrites?: number
  cacheReads?: number
  cost?: number
  cancelReason?: string
  streamingFailedMessage?: string
}
