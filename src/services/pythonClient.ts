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

const DYNAMIC_LAYER_UPDATE_METHOD = 'rpc.ui.map.dynamic_layer_update'
const DYNAMIC_LAYER_FRAME_MS = 100

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
   * Route Python-pushed notifications through both the handler registry and
   * external listeners. This keeps map/worker/chat side effects centralized
   * while preserving subscription hooks for UI code.
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
  private dynamicLayerNotifications: Map<string, JsonRpcNotification[]> = new Map()
  private dynamicLayerFlushTimer: ReturnType<typeof setTimeout> | null = null
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
   * and an ``id`` field). Notifications are also broadcast through
   * ``onNotification`` for stores and extension hosts that subscribe to the
   * shared event stream.
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
    this.clearDynamicLayerQueue()
    this.reconnectAttempts = this.maxReconnectAttempts // Prevent reconnect
    
    this.rejectPendingRequests(new Error('WebSocket disconnected'))
    
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this._isConnected = false
  }

  private rejectPendingRequests(error: Error): void {
    for (const [id, callback] of this.pendingRequests.entries()) {
      try {
        callback(null, error)
      } catch (e) {
        console.error(`[PythonClient] Error rejecting pending request ${id}:`, e)
      }
    }
    this.pendingRequests.clear()
  }

  /**
   * Send a JSON-RPC request and wait for the response.
   *
   * @param timeoutMs  Override the per-request timeout. Defaults are tuned
   *                   per method family: `chat.user_message` gets 10 minutes
   *                   because agent runs are multi-step + LLM-bound,
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
      //  - rpc.tool.execute can run heavy GIS ops; give it a generous budget.
      //  - everything else (config, ping, metadata lookups) is fast.
      const isChat = method === 'chat.user_message'
      const isTool = method.startsWith('rpc.tool.')
      const isScriptRun = method === 'rpc.code.run_script'
      const effectiveTimeout =
        timeoutMs ??
        (isChat
          ? 10 * 60 * 1000 // 10 min
          : isScriptRun
          ? 10 * 60 * 1000 // 10 min
          : isTool
          ? 5 * 60 * 1000 // 5 min
          : 60 * 1000) // 60 sec

      // Only set a timeout if effectiveTimeout > 0
      if (effectiveTimeout > 0) {
        timeoutId = setTimeout(() => {
          if (this.pendingRequests.has(id)) {
            cleanup()
            reject(new Error(`Request timeout: ${method}`))
          }
        }, effectiveTimeout)
      }
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
        this._isConnected = false
        this.rejectPendingRequests(new Error('WebSocket disconnected'))
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

    // JSON-RPC Request from Python → TS (has id + method + routable prefix).
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
      // No dispatcher wired or unknown channel: fall through to the
      // broadcast notification stream so observers still see the method.
    }

    // JSON-RPC Notification (no id).
    //
    // Two sinks, in order.
    //   1. If a dispatcher is wired AND the method sits on one of the
    //      canonical channels (rpc./chat./event.), route it to the
    //      dispatcher. This is how `rpc.ui.map.add_layer_from_geojson`
    //      (and friends) finally reach the handlers in
    //      `src/services/rpc/handlers/`.
    //   2. Always also fan out to `notificationHandlers`. Store-level
    //      features such as chat MessageParts, script output, worker logs,
    //      pivots, and extensions subscribe through `onNotification(...)`.
    //
    // Running both is safe: canonical-channel methods are registered
    // exclusively on the dispatcher's registry; non-canonical methods
    // (e.g. the `map.addLayer` fallback during dev) have no dispatcher
    // handler and go straight to the notification fan-out.
    if (data.method) {
      if (data.method === DYNAMIC_LAYER_UPDATE_METHOD && data.id === undefined) {
        this.enqueueDynamicLayerNotification({
          jsonrpc: '2.0',
          method: data.method,
          params: data.params ?? {},
        })
        return
      }
      this.routeNotification({
        jsonrpc: '2.0',
        method: data.method,
        params: data.params ?? {},
      })
    }
  }

  private enqueueDynamicLayerNotification(notif: JsonRpcNotification): void {
    const params = (notif.params ?? {}) as Record<string, any>
    const layerId = typeof params.layer_id === 'string' && params.layer_id
      ? params.layer_id
      : `__unknown__:${this.dynamicLayerNotifications.size}`
    const existing = this.dynamicLayerNotifications.get(layerId) ?? []
    const mode = typeof params.mode === 'string' ? params.mode : undefined
    const hasFullPayload = mode === 'full' || (params.geojson != null && params.diff == null)
    // A full frame supersedes prior queued frames for the same layer. Diff
    // frames are incremental and must keep order; dropping them can turn a
    // valid full+diff burst into an empty/no-op layer on the frontend.
    this.dynamicLayerNotifications.set(layerId, hasFullPayload ? [notif] : [...existing, notif])
    if (this.dynamicLayerFlushTimer !== null) return
    this.dynamicLayerFlushTimer = setTimeout(() => {
      this.dynamicLayerFlushTimer = null
      this.flushDynamicLayerNotifications()
    }, DYNAMIC_LAYER_FRAME_MS)
  }

  private flushDynamicLayerNotifications(): void {
    const pending = [...this.dynamicLayerNotifications.values()].flat()
    this.dynamicLayerNotifications.clear()
    for (const notif of pending) {
      this.routeNotification(notif)
    }
  }

  private clearDynamicLayerQueue(): void {
    if (this.dynamicLayerFlushTimer !== null) {
      clearTimeout(this.dynamicLayerFlushTimer)
      this.dynamicLayerFlushTimer = null
    }
    this.dynamicLayerNotifications.clear()
  }

  private routeNotification(notif: JsonRpcNotification): void {
    const channel = getMethodChannel(notif.method)
    if (this.dispatcher && channel !== null) {
      this.dispatcher.handleNotification(notif).catch((err) => {
        console.error(
          '[PythonClient] Dispatcher.handleNotification rejected unexpectedly:',
          err,
        )
      })
    }

    for (const handler of this.notificationHandlers) {
      try {
        handler(notif.method, notif.params)
      } catch (error) {
        console.error('[PythonClient] Notification handler error:', error)
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

    this.reconnectTimer = setTimeout(() => {
      this._connect()
    }, delay)
  }
}

// Singleton instance
export const pythonClient = new PythonClient()
