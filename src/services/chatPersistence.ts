/**
 * Chat Persistence Service
 *
 * Persists conversations to `<workspace>/.opengis/conversations/` as JSON files.
 * Each conversation is stored as a separate file: `<conversation_id>.json`.
 *
 * Design:
 * - Uses Electron IPC (file:read, file:write, file:read-dir, file:mkdir)
 * - Debounced writes to avoid excessive disk I/O during streaming
 * - Graceful degradation: if no workspace is open, persistence is disabled
 */

import type { Conversation } from '@/stores/chatStore'

const CONVERSATIONS_DIR = '.opengis/conversations'
const MAX_PERSISTED_OUTPUT_CHARS = 128 * 1024

// ─── Debounce utility ──────────────────────────────────────────────
const _pendingWrites = new Map<string, ReturnType<typeof setTimeout>>()
const DEBOUNCE_MS = 1000

function getConversationsDir(workspacePath: string): string {
  // Normalize to forward slashes (works on both Windows and Unix in Electron/Node)
  const base = workspacePath.replace(/\\/g, '/')
  return `${base}/${CONVERSATIONS_DIR}`
}

function getConversationFilePath(workspacePath: string, conversationId: string): string {
  return `${getConversationsDir(workspacePath)}/${conversationId}.json`
}

// ─── Public API ────────────────────────────────────────────────────

/**
 * Load all conversations from the workspace.
 * Returns an empty array if the directory doesn't exist or no workspace is open.
 */
export async function loadConversations(workspacePath: string | null): Promise<Conversation[]> {
  if (!workspacePath || !window.electronAPI) return []

  const dir = getConversationsDir(workspacePath)

  // Ensure directory exists
  try {
    await window.electronAPI.ensureDirectory(dir)
  } catch {
    return []
  }

  // Read directory contents
  const dirResult = await window.electronAPI.readDirectory(dir)
  if (!dirResult?.success || !dirResult.entries) return []

  const conversations: Conversation[] = []

  for (const entry of dirResult.entries) {
    if (entry.type !== 'file' || !entry.name.endsWith('.json')) continue

    try {
      const fileResult = await window.electronAPI.readFile(entry.path)
      if (fileResult?.success && fileResult.content) {
        const data = JSON.parse(fileResult.content)
        // Validate basic structure
        if (data.id && Array.isArray(data.messages)) {
          conversations.push({
            id: data.id,
            title: data.title || 'Untitled',
            messages: data.messages,
            createdAt: data.createdAt || Date.now(),
            updatedAt: data.updatedAt || Date.now(),
          })
        }
      }
    } catch (e) {
      console.warn(`[chatPersistence] Failed to load conversation file: ${entry.name}`, e)
    }
  }

  // Sort by updatedAt descending (most recent first)
  conversations.sort((a, b) => b.updatedAt - a.updatedAt)
  return conversations
}

/**
 * Persist a single conversation to disk (debounced).
 * No-op if no workspace is open.
 */
export function persistConversation(workspacePath: string | null, conversation: Conversation): void {
  if (!workspacePath || !window.electronAPI) return

  const key = conversation.id

  // Cancel any pending write for this conversation
  const existing = _pendingWrites.get(key)
  if (existing) clearTimeout(existing)

  // Schedule a debounced write
  const timer = setTimeout(() => {
    _pendingWrites.delete(key)
    _writeConversation(workspacePath, conversation)
  }, DEBOUNCE_MS)

  _pendingWrites.set(key, timer)
}

/**
 * Immediately persist a conversation (no debounce).
 * Used when the app is about to close or workspace is switching.
 */
export async function persistConversationImmediate(
  workspacePath: string | null,
  conversation: Conversation
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return

  // Cancel any pending debounced write
  const existing = _pendingWrites.get(conversation.id)
  if (existing) {
    clearTimeout(existing)
    _pendingWrites.delete(conversation.id)
  }

  await _writeConversation(workspacePath, conversation)
}

/**
 * Delete a conversation file from disk.
 */
export async function deleteConversationFile(
  workspacePath: string | null,
  conversationId: string
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return

  const filePath = getConversationFilePath(workspacePath, conversationId)
  try {
    await window.electronAPI.deleteFile(filePath)
  } catch {
    // Ignore — file might not exist yet
  }
}

/**
 * Flush all pending writes immediately.
 * Call this before workspace switch or app close.
 */
export async function flushAllPendingWrites(
  workspacePath: string | null,
  conversations: Conversation[]
): Promise<void> {
  if (!workspacePath || !window.electronAPI) return

  // Clear all pending timers
  for (const [key, timer] of _pendingWrites.entries()) {
    clearTimeout(timer)
    _pendingWrites.delete(key)
  }

  // Write all conversations that might have pending changes
  const writePromises = conversations.map((c) => _writeConversation(workspacePath, c))
  await Promise.allSettled(writePromises)
}

// ─── Internal ──────────────────────────────────────────────────────

async function _writeConversation(workspacePath: string, conversation: Conversation): Promise<void> {
  if (!window.electronAPI) return

  const dir = getConversationsDir(workspacePath)
  const filePath = getConversationFilePath(workspacePath, conversation.id)

  try {
    // Ensure directory exists
    await window.electronAPI.ensureDirectory(dir)

    // Serialize and write
    const persisted = sanitizeConversationForDisk(conversation)
    const data = JSON.stringify(
      {
        id: persisted.id,
        title: persisted.title,
        messages: persisted.messages,
        createdAt: persisted.createdAt,
        updatedAt: persisted.updatedAt,
      },
      null,
      2
    )

    await window.electronAPI.writeFile(filePath, data)
  } catch (e) {
    console.error(`[chatPersistence] Failed to write conversation ${conversation.id}:`, e)
  }
}

function truncatePersistedText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text
  const head = Math.floor(maxChars * 0.35)
  const tail = maxChars - head
  const omitted = text.length - maxChars
  return (
    text.slice(0, head)
    + `\n\n... [persisted chat output truncated: ${omitted.toLocaleString()} chars omitted] ...\n\n`
    + text.slice(-tail)
  )
}

function sanitizeConversationForDisk(conversation: Conversation): Conversation {
  return {
    ...conversation,
    messages: conversation.messages.map((message) => {
      if (
        typeof message.text === 'string'
        && (message.say === 'tool' || message.say === 'code_result')
        && message.text.length > MAX_PERSISTED_OUTPUT_CHARS
      ) {
        return {
          ...message,
          text: truncatePersistedText(message.text, MAX_PERSISTED_OUTPUT_CHARS),
        }
      }
      return message
    }),
  }
}
