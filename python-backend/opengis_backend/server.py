"""FastAPI server with WebSocket hub for JSON-RPC 2.0 communication."""

import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from opengis_backend.config import settings
from opengis_backend.rpc_handler import RpcHandler
from opengis_backend.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Global instances
skill_registry = SkillRegistry()

# 生成 WebSocket 认证 token（防御性深度防护）
WS_TOKEN = secrets.token_urlsafe(32)
logger.info("WebSocket auth token generated")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    logger.info("Initializing backend...")
    await skill_registry.discover_and_load()
    logger.info("Loaded %d skills", len(skill_registry.list_all()))
    # 输出 WebSocket 认证 token（必须在 OPENGIS_READY 之前，确保 Electron 先收到 token）
    print(f"OPENGIS_WS_TOKEN={WS_TOKEN}", flush=True)
    # Signal to Electron that backend is ready.
    # This MUST go to stdout as a plain line — pythonManager.ts grep's for it.
    print("OPENGIS_READY", flush=True)
    logger.info("Backend ready. OPENGIS_READY emitted.")
    yield
    # Shutdown
    logger.info("Shutting down backend...")


app = FastAPI(
    title="OpenGIS Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow Electron renderer and local dev servers.
# For production, set ALLOWED_ORIGINS env var (comma-separated).
# Electron production uses app:// protocol; dev uses http://localhost.
_cors_env = os.environ.get("ALLOWED_ORIGINS", "")
if _cors_env:
    _allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    # Default: Electron app protocol + local dev servers
    _allowed_origins = [
        "app://opengis",
        "http://localhost",
        "http://127.0.0.1",
    ]
    # Also allow common Vite dev ports
    for _port in (5173, 5174, 5175, 3000):
        _allowed_origins.append(f"http://localhost:{_port}")
        _allowed_origins.append(f"http://127.0.0.1:{_port}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


# ─── REST Endpoints ───


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/skills")
async def list_skills():
    """List all available skills."""
    return {
        "skills": [s.to_dict() for s in skill_registry.list_all()]
    }


@app.get("/api/system/logs-dir")
async def get_logs_dir():
    """Return the absolute path of the active log directory (None if not configured)."""
    from opengis_backend.logging_setup import get_log_dir
    log_dir = get_log_dir()
    return {"log_dir": str(log_dir) if log_dir else None}


@app.post("/api/rpc")
async def http_rpc(body: dict):
    """JSON-RPC 2.0 over HTTP — mirrors the WebSocket channel for easy testing.

    Accepts the same JSON format as the WebSocket:
        POST /api/rpc
        {"jsonrpc":"2.0","method":"rpc.agent.test_connection","params":{...},"id":1}

    Returns the same JSON-RPC response format.
    For streaming methods (chat.*), only the initial acknowledgement is returned;
    background notifications are not delivered over HTTP.
    """
    from opengis_backend.rpc_http import dispatch_http

    return await dispatch_http(body, skill_registry)


# ─── WebSocket Endpoint ───

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(None, alias="token"),
):
    """Main WebSocket endpoint for JSON-RPC 2.0 communication.

    Requires token authentication via query parameter:
      ws://localhost:port/ws?token=xxx
    """
    # 验证 token
    if not token or token != WS_TOKEN:
        logger.warning("WebSocket connection rejected: invalid or missing token")
        await websocket.accept()
        await websocket.send_text(
            '{"jsonrpc":"2.0","error":{"code":-32001,"message":"Unauthorized: invalid or missing token"},"id":null}'
        )
        await websocket.close()
        return

    await websocket.accept()
    logger.info("WebSocket client connected (authenticated)")
    handler = RpcHandler(websocket, skill_registry)

    try:
        while True:
            data = await websocket.receive_text()
            await handler.handle_message(data)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass
