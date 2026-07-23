"""FastAPI server with WebSocket hub for JSON-RPC 2.0 communication."""

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from opengis_backend.runtime.config import settings
from opengis_backend.rpc.handler import RpcHandler
from opengis_backend.skills.discovery import UserSkillDiscovery, add_source_path
from opengis_backend.tools.registry import ToolRegistry
from opengis_backend.worker import get_worker_manager

logger = logging.getLogger(__name__)

# Global instances
tool_registry = ToolRegistry()

# 生成 WebSocket 认证 token（防御性深度防护）
WS_TOKEN = secrets.token_urlsafe(32)
logger.info("WebSocket auth token generated")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    logger.info("Initializing backend...")
    await tool_registry.discover_and_load()
    logger.info("Loaded %d executable tools", len(tool_registry.list_all()))
    # 输出 WebSocket 认证 token（必须在 OPENGIS_READY 之前，确保 Electron 先收到 token）
    print(f"OPENGIS_WS_TOKEN={WS_TOKEN}", flush=True)
    # Signal to Electron that backend is ready.
    # This MUST go to stdout as a plain line — pythonManager.ts grep's for it.
    print("OPENGIS_READY", flush=True)
    logger.info("Backend ready. OPENGIS_READY emitted.")
    yield
    # Shutdown
    logger.info("Shutting down backend...")
    get_worker_manager().pause_all(reason="app_shutdown")


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


@app.get("/api/tools")
async def list_tools():
    """List executable function-call tools registered by the backend."""
    tools = [s.to_dict() for s in tool_registry.list_all()]
    return {"tools": tools}


@app.get("/api/user-skills")
async def list_user_skills(workspace_path: str | None = None):
    """List user-loadable instruction skills discovered on disk."""
    skills = UserSkillDiscovery(workspace_path=workspace_path).list()
    return {"skills": [item.to_dict() for item in skills]}


@app.post("/api/user-skills/sources")
async def add_user_skill_source(body: dict):
    """Persist an additional user skill source path."""
    result = add_source_path(
        str(body.get("path") or body.get("source_path") or ""),
        workspace_path=body.get("workspace_path") or None,
        scope=str(body.get("scope") or "workspace"),
    )
    return result


@app.get("/api/system/logs-dir")
async def get_logs_dir():
    """Return the absolute path of the active log directory (None if not configured)."""
    from opengis_backend.runtime.logging import get_log_dir
    log_dir = get_log_dir()
    return {"log_dir": str(log_dir) if log_dir else None}


@app.get("/api/rasters/{raster_id}/metadata")
async def get_raster_metadata(raster_id: str):
    """Return backend tile-raster metadata and current render style."""
    from opengis_backend.integrations.gis.raster_service import get_registered_raster

    try:
        return get_registered_raster(raster_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/rasters/{raster_id}/style")
async def update_raster_style(raster_id: str, body: dict):
    """Update a backend tile-raster style.

    The tile URL carries a style revision query param. Updating the revision
    lets the frontend invalidate MapLibre's tile cache without recreating the
    whole layer.
    """
    from opengis_backend.integrations.gis.raster_service import update_registered_raster_style

    try:
        return update_registered_raster_style(raster_id, dict(body or {})).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/rasters/{raster_id}/tiles/{z}/{x}/{y}.png")
async def get_raster_tile(raster_id: str, z: int, x: int, y: int):
    """Render a local raster as an XYZ PNG tile."""
    from opengis_backend.integrations.gis.raster_service import render_registered_raster_tile

    try:
        tile = render_registered_raster_tile(raster_id, z, x, y)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.debug("Raster tile render failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(
        content=tile,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/assets/{asset_id}/{file_path:path}")
async def get_asset_file(asset_id: str, file_path: str):
    """Serve a file from a registered 3D Tiles set or point cloud.

    The frontend's deck.gl renderer fetches tileset.json plus its child tiles
    (b3dm/i3dm/pnts/glb) — or a .las/.laz point cloud — through this endpoint.
    Files are resolved under the asset's registered root with a
    directory-traversal guard.
    """
    from opengis_backend.integrations.gis.tiles3d_service import (
        guess_media_type,
        resolve_asset_file,
    )

    try:
        target = resolve_asset_file(asset_id, file_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return FileResponse(
        str(target),
        media_type=guess_media_type(target),
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    from opengis_backend.rpc.http import dispatch_http

    return await dispatch_http(body, tool_registry)


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
    handler = RpcHandler(websocket, tool_registry)

    # Track background tasks so we can clean up on disconnect.
    background_tasks: set[asyncio.Task] = set()

    def _task_done(task: asyncio.Task) -> None:
        background_tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Unhandled error in WS message task: %s", task.exception())

    try:
        while True:
            data = await websocket.receive_text()
            # Dispatch each message concurrently so that interrupt
            # requests are not blocked by a running agent task.
            task = asyncio.create_task(handler.handle_message(data))
            background_tasks.add(task)
            task.add_done_callback(_task_done)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        # A websocket disconnect/reload must stop the active agent, not just
        # cancel the request coroutine. Otherwise the worker thread can keep
        # driving the previous plan run and leak stale events/state into the
        # next renderer connection.
        try:
            await handler.shutdown()
        except Exception:
            logger.debug("websocket handler shutdown failed", exc_info=True)
        # Cancel any lingering background tasks on disconnect.
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
