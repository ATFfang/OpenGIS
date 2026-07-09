/**
 * Chat / Agent UI types — owned by the frontend, decoupled from any
 * upstream Agent framework. Reflects what the OpenGIS Python agent runtime
 * emits over IPC.
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

// ── UI message taxonomy (matches the agent event stream) ──
export type SayType =
  | 'text'             // Plain text response from the agent
  | 'reasoning'        // Thinking / planning step
  | 'user_feedback'    // Echoes the user's input
  | 'tool'            // A tool invocation summary
  | 'code'             // A Python code block emitted by the agent
  | 'code_result'      // Stdout/stderr from executing a code block
  | 'image'            // Inline image (e.g. matplotlib plot saved by save_plot)
  | 'command'          // A frontend command (map.addLayer etc.)
  | 'plan'             // A TODO / plan checklist emitted by update_plan
  | 'progress'         // Execution progress indicator
  | 'subagent'         // Isolated sub-agent delegation card (run_subagent / run_subagents)
  | 'thinking'         // transient work indicator, not rendered as assistant content
  | 'error'
  | 'max_steps_reached'
  | 'screenshot'

export type AskType =
  | 'tool'
  | 'command'
  | 'resume_task'

// ── Plan / TODO checklist (emitted by the backend `update_plan` tool) ──
export type PlanStepStatus =
  | 'pending'
  | 'in_progress'
  | 'done'
  | 'skipped'
  | 'failed'

export interface PlanStep {
  id: string
  title: string
  status: PlanStepStatus
  note?: string
}

export interface PlanData {
  /** Stable id; repeated updates with the same id replace the same card. */
  planId: string
  title?: string
  steps: PlanStep[]
  runId?: string
  /** True when this plan represents a workflow run shown in compact mode. */
  workflow?: boolean
  /** Wall-clock of the latest update, for subtle "updated" affordances. */
  updatedAt?: number
}

// ── Sub-agent delegation card (emitted by run_subagent / run_subagents) ──
// We deliberately surface ONLY a content-free status (task title + state),
// never the child agent's internal steps — the whole point of a sub-agent
// is to keep that mess out of the main context.
export type SubagentTaskStatus = 'running' | 'done' | 'failed' | 'cancelled'

export interface SubagentTask {
  title: string
  status: SubagentTaskStatus
}

export interface SubagentData {
  /** Stable id; repeated updates with the same id replace the same card. */
  subagentId: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  /** True when this is a parallel fan-out (run_subagents with >1 task). */
  parallel: boolean
  tasks: SubagentTask[]
  okCount?: number
  total?: number
  runId?: string
  /** Wall-clock of first/last update — drives the elapsed-time chip. */
  startedAt?: number
  updatedAt?: number
}

export type MessagePartType =
  | 'text'
  | 'reasoning'
  | 'tool'
  | 'tool_output'
  | 'code'
  | 'artifact'
  | 'approval'
  | 'plan'
  | 'progress'
  | 'error'
  | 'turn'

export type MessagePartStatus =
  | 'pending'
  | 'running'
  | 'streaming'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface MessagePart {
  id: string
  type: MessagePartType
  status: MessagePartStatus
  text?: string
  tool?: string
  callId?: string
  call_id?: string
  runId?: string
  run_id?: string
  data?: Record<string, unknown>
  createdAt?: number
  created_at?: number
}

export interface UIMessage {
  ts: number
  type: 'say' | 'ask'
  say?: SayType
  ask?: AskType
  text?: string
  partial?: boolean
  images?: string[]
  files?: string[]
  /** Directory used to resolve relative Markdown image paths. */
  markdownBaseDir?: string

  // Tool invocation
  toolName?: string
  toolCallId?: string
  toolArgs?: Record<string, unknown>
  toolStatus?: 'pending' | 'running' | 'completed' | 'failed'

  // GIS-specific result fields surfaced into the chat
  geojson?: unknown
  chartConfig?: unknown
  tableData?: unknown[]

  // Agent code step metadata — filled for say='code' / 'code_result'.
  // `scriptPath` is relative to the opened workspace (or the run directory
  // when no workspace is open) and is safe to pass to openFileAsTab.
  stepNumber?: number
  scriptPath?: string
  scriptAbsPath?: string
  runId?: string
  codeError?: string | null
  /** Execution duration in milliseconds (filled for code_result). */
  durationMs?: number

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

  // Plan / TODO checklist — filled for say='plan'. Upserted by planId so
  // repeated update_plan() calls within a run update the same card.
  planData?: PlanData

  // Sub-agent delegation card — filled for say='subagent'. Upserted by
  // subagentId so the running → done transition animates in place.
  subagentData?: SubagentData

  // Interactive screenshot card — filled for say='screenshot'.
  screenshotData?: {
    requestId: string
    savePath: string
    prompt: string
  }

  // Model attribution
  modelInfo?: {
    modelId?: string
    providerId?: string
    mode?: string
  }

  /**
   * Event-sourced projection used by ChatView.
   */
  parts?: MessagePart[]
}
