/**
 * useScriptRunner — React hook wrapping the `rpc.code.run_script`
 * request/response + streaming stdout notifications.
 *
 * Protocol (matches opengis_backend/sandbox/script_runner.py):
 *
 *   TS -> Python request:
 *     method: "rpc.code.run_script"
 *     params: { run_id, code, workspace_path?, exec_timeout? }
 *     result: { ok, run_id, output, logs, is_final_answer, duration_ms, error? }
 *
 *   Python -> TS notifications (no id):
 *     "rpc.code.script_started" { run_id }
 *     "rpc.code.stdout"         { run_id, text }
 *     "rpc.code.stderr"         { run_id, text }
 *     "rpc.code.script_done"    { run_id, ok, output, logs, duration_ms, error? }
 *
 * The hook exposes a simple state machine: idle → running → finished.
 * Output is kept as an array of (stream, text, ts) chunks so the UI
 * can render a terminal-like rolling log.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { v4 as uuid } from 'uuid'
import { pythonClient } from '@/services/pythonClient'

export type RunnerStatus = 'idle' | 'running' | 'finished'

export interface OutputChunk {
  /** Monotonic id so React can key it cheaply. */
  id: number
  stream: 'stdout' | 'stderr' | 'info' | 'error'
  text: string
  ts: number
}

export interface ScriptResult {
  ok: boolean
  output: unknown
  logs: string | null
  duration_ms: number | null
  error?: string | null
  /** Present if the Python side reported an error category. */
  error_type?: string | null
}

export interface UseScriptRunner {
  status: RunnerStatus
  chunks: OutputChunk[]
  /** Populated once the script finishes (ok or not). */
  result: ScriptResult | null
  /** Currently-running run id, or null. */
  runId: string | null
  run: (code: string, opts?: { workspacePath?: string | null; execTimeout?: number }) => Promise<void>
  stop: () => Promise<void>
  clearOutput: () => void
}

export function useScriptRunner(): UseScriptRunner {
  const [status, setStatus] = useState<RunnerStatus>('idle')
  const [chunks, setChunks] = useState<OutputChunk[]>([])
  const [result, setResult] = useState<ScriptResult | null>(null)
  const [runId, setRunId] = useState<string | null>(null)

  // We want stale-closure-free access to the active run id inside the
  // long-lived onNotification subscription.
  const runIdRef = useRef<string | null>(null)
  const nextChunkId = useRef(0)

  const pushChunk = useCallback((stream: OutputChunk['stream'], text: string) => {
    if (!text) return
    setChunks((prev) => [
      ...prev,
      { id: nextChunkId.current++, stream, text, ts: Date.now() },
    ])
  }, [])

  // Subscribe to pythonClient notifications exactly once per hook instance.
  // Note: pythonClient fan-outs to every subscriber, so multiple hook
  // instances coexist fine — each filters by its own runIdRef.
  useEffect(() => {
    const off = pythonClient.onNotification((method, params) => {
      if (!params || typeof params !== 'object') return
      const p = params as Record<string, unknown>
      const incomingRunId = typeof p.run_id === 'string' ? p.run_id : null
      // Drop cross-talk from other runs (chat agent, other panels).
      if (!incomingRunId || incomingRunId !== runIdRef.current) return

      switch (method) {
        case 'rpc.code.script_started':
          // Already set to 'running' by run(); this is just a handshake.
          break
        case 'rpc.code.stdout':
          if (typeof p.text === 'string') pushChunk('stdout', p.text)
          break
        case 'rpc.code.stderr':
          if (typeof p.text === 'string') pushChunk('stderr', p.text)
          break
        case 'rpc.code.script_done': {
          // The response-side of run() will also receive the full result
          // via the JSON-RPC reply. We don't mutate status here to avoid
          // a race with run(); script_done is purely informational.
          break
        }
        default:
          break
      }
    })
    return off
  }, [pushChunk])

  const run = useCallback(
    async (code: string, opts?: { workspacePath?: string | null; execTimeout?: number }) => {
      if (status === 'running') {
        // Guard against double-submit. The Python side also rejects
        // with a 'busy' error, but it's nicer to not even send.
        return
      }

      const id = uuid()
      runIdRef.current = id
      setRunId(id)
      setStatus('running')
      setResult(null)
      setChunks([])
      pushChunk('info', `▶ run started  (run_id=${id.slice(0, 8)})\n`)

      try {
        const reply = await pythonClient.send<ScriptResult & { run_id?: string }>(
          'rpc.code.run_script',
          {
            run_id: id,
            code,
            workspace_path: opts?.workspacePath ?? undefined,
            exec_timeout: opts?.execTimeout ?? undefined,
          },
        )
        setResult({
          ok: !!reply.ok,
          output: reply.output ?? null,
          logs: reply.logs ?? null,
          duration_ms: reply.duration_ms ?? null,
          error: reply.error ?? null,
        })
        if (reply.ok) {
          pushChunk(
            'info',
            `\n✓ done in ${Math.round(reply.duration_ms ?? 0)}ms\n`,
          )
        } else {
          pushChunk('error', `\n✗ failed: ${reply.error ?? '(no error message)'}\n`)
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err)
        setResult({
          ok: false,
          output: null,
          logs: null,
          duration_ms: null,
          error: msg,
        })
        pushChunk('error', `\n✗ transport error: ${msg}\n`)
      } finally {
        setStatus('finished')
        // We deliberately keep runIdRef pointed at the finished run so
        // any trailing stdout notification that arrives late (rare) is
        // still rendered under the same run. It will be reset on next run().
      }
    },
    [status, pushChunk],
  )

  const stop = useCallback(async () => {
    const id = runIdRef.current
    if (!id || status !== 'running') return
    try {
      await pythonClient.send('rpc.code.cancel_script', { run_id: id })
      pushChunk('info', '⏹ cancel requested…\n')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      pushChunk('error', `cancel failed: ${msg}\n`)
    }
  }, [status, pushChunk])

  const clearOutput = useCallback(() => {
    setChunks([])
    setResult(null)
  }, [])

  return { status, chunks, result, runId, run, stop, clearOutput }
}
