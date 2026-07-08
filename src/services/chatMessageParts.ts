import type { MessagePart, MessagePartStatus, MessagePartType, UIMessage } from '@/types/chat'

const LEGACY_PROJECTION = '__legacy_projection'

function part(
  message: UIMessage,
  type: MessagePartType,
  suffix: string,
  options: Partial<MessagePart> = {},
): MessagePart {
  return {
    id: `${message.ts}:${suffix}`,
    type,
    status: options.status ?? statusFromMessage(message),
    text: options.text,
    tool: options.tool,
    callId: options.callId,
    call_id: options.call_id,
    runId: options.runId ?? message.runId,
    run_id: options.run_id ?? message.runId,
    data: {
      [LEGACY_PROJECTION]: true,
      ...(options.data ?? {}),
    },
    createdAt: options.createdAt ?? message.ts,
    created_at: options.created_at ?? message.ts,
  }
}

function statusFromMessage(message: UIMessage): MessagePartStatus {
  if (message.partial) return 'streaming'
  if (message.toolStatus === 'running') return 'running'
  if (message.toolStatus === 'failed' || message.codeError) return 'failed'
  if (message.toolStatus === 'completed') return 'completed'
  return 'completed'
}

function isLegacyProjection(parts: MessagePart[] | undefined): boolean {
  return !parts?.length || parts.every((p) => p.data?.[LEGACY_PROJECTION] === true)
}

export function projectMessageParts(message: UIMessage): MessagePart[] {
  const type = message.type === 'ask' ? message.ask : message.say
  switch (type) {
    case 'user_feedback':
      return [
        part(message, 'text', 'user', {
          status: 'completed',
          text: message.text || '',
          data: {
            role: 'user',
            images: message.images,
            files: message.files,
          },
        }),
      ]
    case 'text':
      return [
        part(message, 'text', 'text', {
          text: message.text || '',
          data: {
            markdownBaseDir: message.markdownBaseDir,
            files: message.files,
          },
        }),
      ]
    case 'reasoning':
      return [
        part(message, 'reasoning', 'reasoning', {
          text: message.text || '',
        }),
      ]
    case 'tool':
    case 'command':
      return [
        part(message, 'tool', `tool:${message.toolCallId || message.toolName || 'unknown'}`, {
          status: message.toolStatus ?? statusFromMessage(message),
          text: message.text || '',
          tool: message.toolName,
          callId: message.toolCallId,
          call_id: message.toolCallId,
          data: {
            args: message.toolArgs,
            durationMs: message.durationMs,
            commandCompleted: message.commandCompleted,
          },
        }),
      ]
    case 'code':
      return [
        part(message, 'code', `code:${message.stepNumber ?? 'unknown'}`, {
          text: message.text || '',
          data: {
            stepNumber: message.stepNumber,
            scriptPath: message.scriptPath,
            scriptAbsPath: message.scriptAbsPath,
          },
        }),
      ]
    case 'code_result':
      return [
        part(message, 'tool_output', `code-result:${message.stepNumber ?? 'unknown'}`, {
          status: message.codeError ? 'failed' : 'completed',
          text: message.text || '',
          tool: 'execute_code',
          data: {
            stepNumber: message.stepNumber,
            error: message.codeError,
            durationMs: message.durationMs,
          },
        }),
      ]
    case 'image':
      return [
        part(message, 'artifact', 'image', {
          text: message.text || '',
          data: {
            kind: 'image',
            images: message.images,
            files: message.files,
          },
        }),
      ]
    case 'plan':
      return [
        part(message, 'plan', `plan:${message.planData?.planId || 'default'}`, {
          status: message.planData?.steps?.some((s) => s.status === 'in_progress') ? 'running' : 'completed',
          data: { planData: message.planData },
        }),
      ]
    case 'subagent':
      return [
        part(message, 'progress', `subagent:${message.subagentData?.subagentId || 'default'}`, {
          status: message.subagentData?.status === 'running' ? 'running' : statusFromSubagent(message),
          data: { subagentData: message.subagentData, kind: 'subagent' },
        }),
      ]
    case 'screenshot':
      return [
        part(message, 'approval', `screenshot:${message.screenshotData?.requestId || 'default'}`, {
          status: 'pending',
          data: { screenshotData: message.screenshotData, kind: 'screenshot' },
        }),
      ]
    case 'progress':
      return [
        part(message, 'progress', `progress:${message.progressStage || 'processing'}`, {
          status: message.partial ? 'running' : 'completed',
          text: message.progressDetail || '',
          data: {
            stage: message.progressStage,
            detail: message.progressDetail,
          },
        }),
      ]
    case 'error':
      return [
        part(message, 'error', 'error', {
          status: 'failed',
          text: message.text || 'Unknown error',
        }),
      ]
    case 'completion_result':
      return [
        part(message, 'text', 'completion', {
          text: message.text || '',
          data: { kind: 'completion_result' },
        }),
      ]
    case 'max_steps_reached':
      return [
        part(message, 'progress', 'max-steps', {
          status: 'completed',
          data: { maxStepsInfo: message.maxStepsInfo, kind: 'max_steps_reached' },
        }),
      ]
    case 'api_req_started':
    case 'api_req_finished':
      return [
        part(message, 'turn', type, {
          text: message.text || '',
          data: { kind: type },
        }),
      ]
    case 'mcp_server_response':
      return [
        part(message, 'tool_output', 'mcp-server-response', {
          text: message.text || '',
          data: { kind: 'mcp_server_response' },
        }),
      ]
    case 'followup':
      return [
        part(message, 'approval', 'followup', {
          status: 'pending',
          text: message.text || '',
          data: { kind: 'followup' },
        }),
      ]
    default:
      return []
  }
}

function statusFromSubagent(message: UIMessage): MessagePartStatus {
  const status = message.subagentData?.status
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return 'completed'
}

export function withProjectedMessageParts(message: UIMessage): UIMessage {
  if (!isLegacyProjection(message.parts)) return message
  return { ...message, parts: projectMessageParts(message) }
}

export function messagePartsForRender(message: UIMessage): MessagePart[] {
  return isLegacyProjection(message.parts) ? projectMessageParts(message) : (message.parts ?? [])
}

