/** Python backend client. 通过 WebSocket 连接 Python 后端的 JSON-RPC 客户端。 */
import { v4 as uuid } from 'uuid'
import {
  getMethodChannel,
  type JsonRpcNotification,
  type JsonRpcRequest,
  type JsonRpcResponse,
} from '@/types/protocol'

type JsonRpcCallback = (result: any, error?: any) => void
type NotificationHandler = (method: string, params: any) => void

/**
 * Minimal surface of the Dispatcher that the client needs. Avoids a hard
 * import cycle with `src/services/rpc/dispatcher.ts`.
 */
export interface DispatcherLike {
  handleRequest(req: JsonRpcRequest): Promise<JsonRpcResponse>
  /**
   * Route a notification (no `id`) to the handler registry. Returns void;
   * any handler error is swallowed by the dispatcher's `onError` hook.
   *
   * Stage 3.8 fix: before this, ``_handleMessage`` only broadcast inbound
   * notifications to ``notificationHandlers`` and **never** hit the
   * dispatcher, so Python-pushed `rpc.ui.map.*` notifications silently
   * dropped (the map store's handlers were registered but unreachable).
   */
  handleNotification(notif: JsonRpcNotification): Promise<void>
}

/**
 * WebSocket client for JSON-RPC 2.0 communication with the Python backend.
 *
 * Supports:
 * - Request/response with automatic ID matching
 * - Server-push notifications (streaming, progress)
 * - Auto-reconnect with exponential backoff
 */
export class PythonClient {
  private ws: WebSocket | null = null
  private url: string = ''
  private pendingRequests: Map<string, JsonRpcCallback> = new Map()
  private notificationHandlers: Set<NotificationHandler> = new Set()
  private dispatcher: DispatcherLike | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private _isConnected = false

  get isConnected(): boolean {
    return this._isConnected
  }

  /**
   * Wait for the WebSocket to become ready, with a timeout.
   * Useful when the backend is still starting or reconnecting.
   */
  private waitForConnection(timeoutMs: number = 10000): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        resolve()
        return
      }

      let timer: ReturnType<typeof setTimeout>
      const check = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          clearInterval(check)
          clearTimeout(timer)
          resolve()
        }
      }, 100)

      timer = setTimeout(() => {
        clearInterval(check)
        reject(new Error('WebSocket connection timeout'))
      }, timeoutMs)
    })
  }

  /**
   * Register the RPC dispatcher used to service inbound requests from the
   * Python backend (methods with ``rpc.`` / ``chat.`` / ``event.`` prefix
   * and an ``id`` field). Without a dispatcher, inbound requests fall
   * through to the notification handlers for backwards compatibility.
   *
   * Stage 3.4: this is the wire-up point that finally connects the
   * Stage-1 Dispatcher + Registry to the real WebSocket.
   */
  setDispatcher(dispatcher: DispatcherLike | null): void {
    this.dispatcher = dispatcher
  }

  /**
   * Connect to the Python backend WebSocket server.
   * Supports token-based authentication via query parameter.
   */
  connect(port: number, token?: string): void {
    let url = `ws://localhost:${port}/ws`
    if (token) {
      url += `?token=${encodeURIComponent(token)}`
    }
    this.url = url
    this._connect()
  }

  /**
   * Disconnect from the server.
   * Rejects all pending requests to prevent hanging promises.
   */
  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.reconnectAttempts = this.maxReconnectAttempts // Prevent reconnect
    
    // Reject all pending requests to prevent hanging promises
    for (const [id, callback] of this.pendingRequests.entries()) {
      try {
        callback(null, new Error('WebSocket disconnected'))
      } catch (e) {
        console.error(`[PythonClient] Error rejecting pending request ${id}:`, e)
      }
    }
    this.pendingRequests.clear()
    
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this._isConnected = false
  }

  /**
   * Send a JSON-RPC request and wait for the response.
   *
   * @param timeoutMs  Override the per-request timeout. Defaults are tuned
   *                   per method family: `chat.user_message` gets 10 minutes
   *                   because CodeAgent runs are multi-step + LLM-bound,
   *                   everything else gets 60 seconds.
   */
  async send<T = any>(
    method: string,
    params: Record<string, any> = {},
    timeoutMs?: number,
  ): Promise<T> {
    // Wait for WebSocket to be ready (handles startup / reconnect races)
    try {
      await this.waitForConnection(10000)
    } catch {
      throw new Error('WebSocket not connected — backend may be starting up')
    }

    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'))
        return
      }

      const id = uuid()
      const request = {
        jsonrpc: '2.0',
        id,
        method,
        params,
      }

      // Store timeout ID so we can clear it when the request completes.
      let timeoutId: ReturnType<typeof setTimeout> | null = null

      const cleanup = () => {
        if (timeoutId !== null) {
          clearTimeout(timeoutId)
          timeoutId = null
        }
        this.pendingRequests.delete(id)
      }

      this.pendingRequests.set(id, (result, error) => {
        cleanup()
        if (error) {
          reject(new Error(error.message || 'RPC Error'))
        } else {
          resolve(result as T)
        }
      })

      this.ws.send(JSON.stringify(request))

      // Pick a timeout that reflects the call's cost class.
      //  - chat.user_message drives the agent loop which may run many
      //    LLM-bound steps; anything under ~10 minutes is too tight.
      //  - rpc.code.run_script is user-authored Python inside the
      //    subprocess sandbox; a heavy training script can easily take
      //    minutes. Match chat's 10-min ceiling so the TS side doesn't
      //    time out before the Python-side executor's own exec_timeout
      //    kicks in.
      //  - rpc.skill.execute can run heavy GIS ops; give it a generous budget.
      //  - everything else (config, ping, metadata lookups) is fast.
      const isChat = method === 'chat.user_message'
      const isSkill = method.startsWith('rpc.skill.')
      const isScriptRun = method === 'rpc.code.run_script'
      const effectiveTimeout =
        timeoutMs ??
        (isChat || isScriptRun
          ? 10 * 60 * 1000 // 10 min
          : isSkill
          ? 5 * 60 * 1000 // 5 min
          : 60 * 1000) // 60 sec

      timeoutId = setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          cleanup()
          reject(new Error(`Request timeout: ${method}`))
        }
      }, effectiveTimeout)
    })
  }

  /**
   * Register a handler for server-push notifications.
   */
  onNotification(handler: NotificationHandler): () => void {
    this.notificationHandlers.add(handler)
    return () => this.notificationHandlers.delete(handler)
  }

  // ─── Private methods ───

  private _connect(): void {
    try {
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        console.log('[PythonClient] Connected to', this.url)
        this._isConnected = true
        this.reconnectAttempts = 0
      }

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          this._handleMessage(data)
        } catch (error) {
          console.error('[PythonClient] Failed to parse message:', error)
        }
      }

      this.ws.onclose = () => {
        console.log('[PythonClient] Disconnected')
        this._isConnected = false
        this._scheduleReconnect()
      }

      this.ws.onerror = (error) => {
        console.error('[PythonClient] WebSocket error:', error)
      }
    } catch (error) {
      console.error('[PythonClient] Connection failed:', error)
      this._scheduleReconnect()
    }
  }

  private _handleMessage(data: any): void {
    // JSON-RPC Response (has id + matches a pending outbound request)
    if (data.id !== undefined && this.pendingRequests.has(data.id)) {
      const callback = this.pendingRequests.get(data.id)!
      this.pendingRequests.delete(data.id)
      callback(data.result, data.error)
      return
    }

    // JSON-RPC Request from Python → TS (has id + method + routable prefix)
    // Stage 3.4: dispatch to the Registry via the wired-in Dispatcher.
    if (data.id !== undefined && typeof data.method === 'string') {
      const channel = getMethodChannel(data.method)
      if (this.dispatcher && channel !== null) {
        const req: JsonRpcRequest = {
          jsonrpc: '2.0',
          id: data.id,
          method: data.method,
          params: data.params ?? {},
        }
        this.dispatcher
          .handleRequest(req)
          .then((response) => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
              this.ws.send(JSON.stringify(response))
            }
          })
          .catch((err) => {
            console.error('[PythonClient] Dispatcher threw unexpectedly:', err)
            // Dispatcher is supposed to catch RpcError itself; this is
            // purely defensive so a rogue exception does not tear down
            // the client.
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
              this.ws.send(
                JSON.stringify({
                  jsonrpc: '2.0',
                  id: data.id,
                  error: {
                    code: -32603,
                    message: err instanceof Error ? err.message : String(err),
                  },
                }),
              )
            }
          })
        return
      }
      // No dispatcher wired or unknown channel → fall through so legacy
      // notification handlers still see the method name.
    }

    // JSON-RPC Notification (no id).
    //
    // Stage 3.8: two sinks, in order.
    //   1. If a dispatcher is wired AND the method sits on one of the
    //      canonical channels (rpc./chat./event.), route it to the
    //      dispatcher. This is how `rpc.ui.map.add_layer_from_geojson`
    //      (and friends) finally reach the handlers in
    //      `src/services/rpc/handlers/`.
    //   2. Always also fan out to `notificationHandlers`. Legacy Python
    //      pushes like `chat.stream_delta` / `chat.code_block` / script
    //      events are consumed by store-level subscribers that were
    //      registered via `onNotification(...)` — they must keep seeing
    //      every notification.
    //
    // Running both is safe: canonical-channel methods are registered
    // exclusively on the dispatcher's registry; non-canonical methods
    // (e.g. the `map.addLayer` fallback during dev) have no dispatcher
    // handler and go straight to the notification fan-out.
    if (data.method) {
      const channel = getMethodChannel(data.method)
      if (this.dispatcher && channel !== null) {
        const notif: JsonRpcNotification = {
          jsonrpc: '2.0',
          method: data.method,
          params: data.params ?? {},
        }
        this.dispatcher.handleNotification(notif).catch((err) => {
          // Dispatcher is supposed to swallow handler errors internally;
          // this is purely defensive so a rogue reject does not break
          // the message pump.
          console.error(
            '[PythonClient] Dispatcher.handleNotification rejected unexpectedly:',
            err,
          )
        })
      }

      for (const handler of this.notificationHandlers) {
        try {
          handler(data.method, data.params)
        } catch (error) {
          console.error('[PythonClient] Notification handler error:', error)
        }
      }
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[PythonClient] Max reconnect attempts reached')
      return
    }

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000)
    this.reconnectAttempts++

    console.log(`[PythonClient] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)

    this.reconnectTimer = setTimeout(() => {
      this._connect()
    }, delay)
  }
}

// Singleton instance
export const pythonClient = new PythonClient()
