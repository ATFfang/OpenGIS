/**
 * Chat message types — mirrors Cline's ClineMessage structure,
 * adapted for OpenGIS's GIS-focused Agent workflow.
 */

// Message event types that the agent can emit
export type AgentSayType =
  | 'text'              // Streaming text response
  | 'reasoning'         // Thinking/reasoning content
  | 'user_feedback'     // User message echo
  | 'tool'              // Tool execution details
  | 'command'           // Shell command execution
  | 'error'             // Error message
  | 'completion_result' // Task completed
  | 'api_req_started'   // API request started (cost tracking)
  | 'api_req_finished'  // API request finished
  | 'task_progress'     // Progress update
  | 'mcp_server_response' // MCP server response

// Message types that require user input
export type AgentAskType =
  | 'followup'          // Agent asks a question
  | 'tool'              // Tool approval request
  | 'command'           // Command approval request
  | 'completion_result' // Task completion confirmation
  | 'resume_task'       // Resume after pause

export interface AgentMessage {
  ts: number                    // Timestamp (unique ID)
  type: 'say' | 'ask'
  say?: AgentSayType
  ask?: AgentAskType
  text?: string
  partial?: boolean             // Is this a streaming partial message?
  images?: string[]
  files?: string[]

  // Tool-specific fields
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolStatus?: 'pending' | 'running' | 'completed' | 'failed'

  // GIS-specific result fields
  geojson?: unknown
  chartConfig?: unknown
  tableData?: unknown[]

  // Command-specific
  commandCompleted?: boolean
}

export interface ApiReqInfo {
  cost?: number
  cancelReason?: string
  streamingFailedMessage?: string
}

export interface ToolInfo {
  tool: string
  path?: string
  content?: string
  diff?: string
  regex?: string
  description?: string
}

export interface Conversation {
  id: string
  title: string
  messages: AgentMessage[]
  createdAt: number
  updatedAt: number
}

// Chat view state types
export interface ChatState {
  inputValue: string
  setInputValue: (value: string) => void
  selectedImages: string[]
  setSelectedImages: React.Dispatch<React.SetStateAction<string[]>>
  selectedFiles: string[]
  setSelectedFiles: React.Dispatch<React.SetStateAction<string[]>>
  sendingDisabled: boolean
  setSendingDisabled: (value: boolean) => void
  enableButtons: boolean
  expandedRows: Set<number>
  setExpandedRows: React.Dispatch<React.SetStateAction<Set<number>>>
  textAreaRef: React.RefObject<HTMLTextAreaElement>
  isTextAreaFocused: boolean
  activeQuote: string | null
  setActiveQuote: (quote: string | null) => void
  handleFocusChange: (isFocused: boolean) => void
}
