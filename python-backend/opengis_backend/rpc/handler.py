"""JSON-RPC 2.0 protocol handler for WebSocket communication."""

import asyncio
import json
import logging
import traceback
import uuid
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from opengis_backend.agent.telemetry.artifacts import ArtifactIndex
from opengis_backend.agent.engine import GISAgent
from opengis_backend.agent.telemetry.event_log import event_to_message_part
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType
from opengis_backend.agent.governance.permission import PermissionAction, PermissionDecision
from opengis_backend.agent.governance.permission_store import PermissionRequestStore, PermissionRuleStore
from opengis_backend.agent.governance.profile import AgentProfileStore
from opengis_backend.agent.session.queue import AgentQueue, AgentQueueItem, AgentQueueStatus
from opengis_backend.agent.session.session import AgentInboxItem, InboxStatus, SessionStore
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.agent.workflow.workflow_store import WorkflowDocumentStore
from opengis_backend.operations import OperationStore
from opengis_backend.runtime.config import settings
from opengis_backend.runtime.constants import (  # noqa: E402 module-level import
    CLEANUP_FUTURE_TIMEOUT,
    DEFAULT_EXEC_TIMEOUT,
    MAX_EXEC_TIMEOUT,
    MIN_EXEC_TIMEOUT,
    TITLE_GEN_TIMEOUT,
)
from opengis_backend.runs import RunArchive
from opengis_backend.sandbox.script_runner import ScriptRunner
from opengis_backend.tools.context import ToolContext
from opengis_backend.skills.discovery import UserSkillDiscovery, add_source_path
from opengis_backend.tools.registry import ToolRegistry
from opengis_backend.workspace import WorkspaceManager, WorkspaceManagerError
from opengis_backend.rpc.workflow_detection import detect_pasted_workflow_message, workflow_run_prompt

logger = logging.getLogger(__name__)

DYNAMIC_LAYER_UPDATE_METHOD = "rpc.ui.map.dynamic_layer_update"
DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS = 0.05


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _looks_like_websocket_closed(exc: BaseException) -> bool:
    text = f"{exc.__class__.__module__}.{exc.__class__.__name__}: {exc}"
    return (
        "ClientDisconnected" in text
        or "ConnectionClosed" in text
        or "WebSocketDisconnect" in text
        or "Cannot call \"send\"" in text
        or "Unexpected ASGI message" in text
    )


def _normalise_workspace(path: str | None) -> str:
    """Canonicalise a workspace path for use as a lock key.

    Returned value is the resolved absolute path with forward slashes,
    or the sentinel ``"<no-workspace>"`` for orphan runs. Using a
    sentinel (rather than None) means two concurrent workspace-less
    chats still serialise — they share the sidecar's subprocess pool
    and must not race on cwd / archive dirs.
    """
    if not path:
        return "<no-workspace>"
    try:
        from pathlib import Path

        return str(Path(path).expanduser().resolve()).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


_TITLED_FILE = ".opengis/titled_conversations.json"


def _load_titled_set(workspace: str) -> set[str]:
    """Load the set of conversation IDs that already have auto-generated titles."""
    from pathlib import Path

    try:
        path = Path(workspace) / _TITLED_FILE
        if not path.exists():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(data)
        return set()
    except Exception as e:
        logger.warning("Failed to load titled conversations from %s: %s", workspace, e)
        return set()


def _save_titled_set(workspace: str, titled: set[str]) -> None:
    """Persist the set of titled conversation IDs to disk."""
    from pathlib import Path

    try:
        path = Path(workspace) / _TITLED_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(list(titled), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Titled conversations saved: %d entries", len(titled))
    except Exception as e:
        logger.warning("Failed to save titled conversations to %s: %s", workspace, e)


# Binary file extensions that should NOT be injected as text context.
_BINARY_EXTENSIONS = frozenset(
    {
        ".tif",
        ".tiff",
        ".geotiff",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".shp",
        ".shx",
        ".dbf",
        ".prj",  # Shapefile components
        ".gpkg",
        ".sqlite",
        ".db",
        ".nc",
        ".hdf",
        ".hdf5",
        ".h5",  # NetCDF / HDF
        ".bin",
        ".dat",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".parquet",
        ".feather",
        ".arrow",
    }
)


class RpcHandler:
    """
    Handles JSON-RPC 2.0 messages over WebSocket.

    Supports:
    - Request/response (with id)
    - Notifications (without id, server → client push)
    - Streaming responses via notifications

    This class is the WebSocket-facing coordinator; typed payload models live
    under :mod:`opengis_backend.runtime.protocol_types`.
    """

    def __init__(
        self, websocket: WebSocket, tool_registry: ToolRegistry
    ):
        self.ws = websocket
        self._ws_write_lock = asyncio.Lock()  # Protect concurrent send_text
        self.tool_registry = tool_registry
        # Reusable agent instance — built lazily once we know the LLM config.
        self._agent: GISAgent | None = None
        self._current_agent_task: asyncio.Task | None = None
        self._current_agent_lock_key: str | None = None
        # D2: per-workspace serial lock. Key is the normalised workspace
        # path (or "<no-workspace>" for orphan runs). We reject rather
        # than queue a second Send on the same workspace to keep the
        # state model simple — mirrors the decision in MEMORY ADR-009.
        self._workspace_locks: dict[str, str] = {}  # workspace -> owner run_id
        self._workspace_lock_guard = asyncio.Lock()
        self._titled_conversations: set[str] | None = None  # lazy-loaded from disk on first use
        # Last LLM config hash — used to avoid unnecessary agent rebuilds.
        self._last_llm_config_hash: str | None = None
        # Workspace manager — shared across runs, stateless.
        self._workspace_manager = WorkspaceManager()
        # ScriptRunner — user-authored scripts bypass the LLM and run in
        # the same subprocess sandbox the agent uses. Instantiated lazily
        # so the notify_fn closure sees the final self._safe_notify.
        self._script_runner: ScriptRunner | None = None
        self._current_script_task: asyncio.Task | None = None
        # Capture the event loop the websocket lives on; sandbox-thread
        # callbacks need it to schedule notifications.
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._pending_client_requests: dict[str, asyncio.Future] = {}
        self._permission_requests = PermissionRequestStore()
        self._agent_queue = AgentQueue()
        self._queue_processors: dict[str, asyncio.Task] = {}
        self._closed = False
        self._worker_event_unsubscribe = None
        self._worker_dynamic_events: dict[str, list[dict[str, Any]]] = {}
        self._worker_dynamic_flush_handle: asyncio.TimerHandle | None = None
        self._method_handlers = {
            # rpc.* channel: synchronous command / query.
            "rpc.fs.load_file": self._handle_load_file,
            "rpc.fs.get_file_info": self._handle_get_file_info,
            "rpc.tool.list": self._handle_tool_list,
            "rpc.tool.execute": self._handle_tool_execute,
            "rpc.user_skill.list": self._handle_user_skill_list,
            "rpc.user_skill.load": self._handle_user_skill_load,
            "rpc.user_skill.add_source": self._handle_user_skill_add_source,
            "rpc.code.run_script": self._handle_run_script,
            "rpc.code.cancel_script": self._handle_cancel_script,
            "rpc.agent.interrupt": self._handle_agent_cancel,
            "rpc.agent.set_llm_config": self._handle_agent_configure,
            "rpc.agent.test_connection": self._handle_agent_test_connection,
            # user_instructions — global personalization prompt.
            "user_instructions.get": self._handle_ui_get,
            "user_instructions.set": self._handle_ui_set,
            # Workspace / run inspection and control.
            "rpc.workspace.revert_run": self._handle_workspace_revert_run,
            "rpc.runs.list": self._handle_runs_list,
            "rpc.runs.get": self._handle_runs_get,
            "rpc.runs.replay": self._handle_runs_replay,
            "rpc.agent.profiles.list": self._handle_agent_profiles_list,
            "rpc.agent.profiles.install_defaults": self._handle_agent_profiles_install_defaults,
            "rpc.agent.sessions.list": self._handle_agent_sessions_list,
            "rpc.agent.inbox.list": self._handle_agent_inbox_list,
            "rpc.agent.queue.submit": self._handle_agent_queue_submit,
            "rpc.agent.queue.run": self._handle_agent_queue_run,
            "rpc.agent.queue.get": self._handle_agent_queue_get,
            "rpc.agent.queue.resume": self._handle_agent_queue_resume,
            "rpc.agent.queue.retry": self._handle_agent_queue_retry,
            "rpc.agent.queue.cancel": self._handle_agent_queue_cancel,
            "rpc.agent.queue.process": self._handle_agent_queue_process,
            "rpc.agent.queue.list": self._handle_agent_queue_list,
            "rpc.agent.artifacts.list": self._handle_agent_artifacts_list,
            "rpc.agent.permissions.list": self._handle_agent_permissions_list,
            "rpc.agent.permissions.rules.list": self._handle_agent_permission_rules_list,
            "rpc.agent.permissions.rules.add": self._handle_agent_permission_rules_add,
            "rpc.agent.permissions.rules.remove": self._handle_agent_permission_rules_remove,
            "rpc.worker.list": self._handle_worker_list,
            "rpc.worker.get": self._handle_worker_get,
            "rpc.worker.restart": self._handle_worker_restart,
            "rpc.worker.pause": self._handle_worker_pause,
            "rpc.worker.delete": self._handle_worker_delete,
            "rpc.operations.list": self._handle_operations_list,
            "rpc.operations.get": self._handle_operations_get,
            "rpc.operations.run": self._handle_operations_run,
            # chat.* channel: long-running conversation turn (streams via notifications)
            "chat.user_message": self._handle_agent_chat,
            # debug channel: runtime log level control
            "rpc.debug.set_log_level": self._handle_set_log_level,
            "rpc.debug.get_log_level": self._handle_get_log_level,
            # workspace channel: template management
            "rpc.workspace.install_templates": self._handle_install_templates,
        }
        from opengis_backend.worker import get_worker_manager

        self._worker_event_unsubscribe = get_worker_manager().subscribe_events(
            self._notify_worker_event
        )

    def mark_closed(self) -> None:
        """Mark this websocket handler as closed and unblock pending UI requests."""
        self._closed = True
        if self._worker_dynamic_flush_handle is not None:
            self._worker_dynamic_flush_handle.cancel()
            self._worker_dynamic_flush_handle = None
        self._worker_dynamic_events.clear()
        if self._worker_event_unsubscribe is not None:
            try:
                self._worker_event_unsubscribe()
            except Exception:
                logger.debug("worker event unsubscribe failed", exc_info=True)
            self._worker_event_unsubscribe = None
        for fut in list(self._pending_client_requests.values()):
            if not fut.done():
                fut.cancel()
        self._pending_client_requests.clear()

    def _notify_worker_event(self, method: str, params: dict) -> None:
        if self._closed:
            return
        if self._loop and self._loop.is_running():
            if method == DYNAMIC_LAYER_UPDATE_METHOD:
                self._loop.call_soon_threadsafe(
                    self._enqueue_worker_dynamic_event,
                    dict(params or {}),
                )
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._safe_notify(method, params),
                self._loop,
            )
            fut.add_done_callback(self._log_worker_event_notify_result)

    def _enqueue_worker_dynamic_event(self, params: dict[str, Any]) -> None:
        if self._closed:
            return
        layer_id = params.get("layer_id")
        key = layer_id if isinstance(layer_id, str) and layer_id else f"__unknown__:{len(self._worker_dynamic_events)}"
        existing = self._worker_dynamic_events.get(key) or []
        mode = params.get("mode")
        has_full_payload = mode == "full" or ("geojson" in params and "diff" not in params)
        self._worker_dynamic_events[key] = [params] if has_full_payload else [*existing, params]
        if self._worker_dynamic_flush_handle is not None:
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        self._worker_dynamic_flush_handle = loop.call_later(
            DYNAMIC_LAYER_BACKEND_FLUSH_SECONDS,
            lambda: asyncio.create_task(self._flush_worker_dynamic_events()),
        )

    async def _flush_worker_dynamic_events(self) -> None:
        self._worker_dynamic_flush_handle = None
        if self._closed or not self._worker_dynamic_events:
            return
        pending = [
            params
            for frames in self._worker_dynamic_events.values()
            for params in frames
        ]
        self._worker_dynamic_events.clear()
        for params in pending:
            await self._send_notification(DYNAMIC_LAYER_UPDATE_METHOD, params)

    @staticmethod
    def _log_worker_event_notify_result(done: Any) -> None:
        try:
            exc = done.exception()
        except Exception:
            logger.debug("worker event notify was cancelled", exc_info=True)
            return
        if exc:
            logger.debug("worker event notify failed", exc_info=(type(exc), exc, exc.__traceback__))

    async def shutdown(self) -> None:
        """Best-effort cleanup for websocket disconnect/app shutdown."""
        self.mark_closed()
        try:
            await self._handle_agent_cancel({"reason": "websocket_disconnect"})
        except Exception:
            logger.debug("agent shutdown interrupt failed", exc_info=True)
        try:
            if self._script_runner is not None:
                self._script_runner.cancel()
        except Exception:
            logger.debug("script runner shutdown cancel failed", exc_info=True)

    async def handle_message(self, raw: str) -> None:
        """Parse and route a JSON-RPC message."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error(None, -32700, "Parse error: invalid JSON")
            return

        if data.get("jsonrpc") != "2.0":
            await self._send_error(data.get("id"), -32600, "Invalid Request: missing jsonrpc 2.0")
            return

        method = data.get("method")
        params = data.get("params", {})
        request_id = data.get("id")

        if request_id is not None and not method and request_id in self._pending_client_requests:
            fut = self._pending_client_requests.pop(request_id)
            if not fut.done():
                if data.get("error") is not None:
                    fut.set_exception(RuntimeError(str(data.get("error"))))
                else:
                    fut.set_result(data.get("result"))
            return

        if not method:
            await self._send_error(request_id, -32600, "Invalid Request: missing method")
            return

        handler = self._method_handlers.get(method)
        if not handler:
            await self._send_error(request_id, -32601, f"Method not found: {method}")
            return

        try:
            result = await handler(params)
            if request_id is not None:
                await self._send_result(request_id, result)
        except ValueError as e:
            # 参数错误 - 客户端可修正
            traceback.print_exc()
            if request_id is not None:
                await self._send_error(request_id, -32602, f"Invalid params: {str(e)}")
        except KeyError as e:
            # 缺少必需字段
            traceback.print_exc()
            if request_id is not None:
                await self._send_error(request_id, -32602, f"Missing field: {str(e)}")
        except Exception as e:
            # 未预期错误 - 记录完整 traceback
            traceback.print_exc()
            if request_id is not None:
                await self._send_error(request_id, -32603, f"Internal error: {str(e)}")

    # ─── Method Handlers ───

    async def _handle_load_file(self, params: dict) -> Any:
        path = params.get("path")
        if not path:
            raise ValueError("Missing required parameter: path")
        return {
            "status": "not_implemented",
            "message": f"File loading not yet implemented for: {path}",
        }

    async def _handle_get_file_info(self, params: dict) -> Any:
        path = params.get("path")
        if not path:
            raise ValueError("Missing required parameter: path")
        return {
            "status": "not_implemented",
            "message": f"File info not yet implemented for: {path}",
        }

    async def _handle_tool_list(self, params: dict) -> Any:
        tools = [s.to_dict() for s in self.tool_registry.list_all()]
        return {"tools": tools}

    async def _handle_tool_execute(self, params: dict) -> Any:
        name = params.get("name")
        args = params.get("args", {})
        if not name:
            raise ValueError("Missing required parameter: name")

        tool_timeout = 50.0
        # Tools triggered directly (not via agent) still get a ToolContext
        # so context-aware display tools work end-to-end.
        workspace_path = params.get("workspace_path") or params.get("workspace")
        ctx = ToolContext(
            notify_fn=self._safe_notify,
            request_fn=self._safe_request,
            meta={"workspace_path": workspace_path} if workspace_path else {},
        )
        try:
            result = await asyncio.wait_for(
                self.tool_registry.execute(name, args, context=ctx),
                timeout=tool_timeout,
            )
            return result
        except TimeoutError:
            return {
                "success": False,
                "error": (
                    f"Tool '{name}' timed out after {tool_timeout}s. "
                    "The operation may be too complex for the tool."
                ),
            }

    async def _handle_worker_list(self, params: dict) -> Any:
        include_logs = bool(params.get("include_logs", True))
        workspace_path = params.get("workspace_path") or None
        from opengis_backend.worker import get_worker_manager

        return {
            "workers": get_worker_manager().list_workers(
                include_logs=include_logs,
                workspace_path=str(workspace_path) if workspace_path else None,
            )
        }

    async def _handle_worker_get(self, params: dict) -> Any:
        worker_id = params.get("worker_id")
        if not worker_id:
            raise ValueError("Missing required parameter: worker_id")
        include_logs = bool(params.get("include_logs", True))
        workspace_path = params.get("workspace_path") or None
        from opengis_backend.worker import get_worker_manager

        return {
            "worker": get_worker_manager().get_worker(
                str(worker_id),
                include_logs=include_logs,
                workspace_path=str(workspace_path) if workspace_path else None,
            )
        }

    async def _handle_worker_pause(self, params: dict) -> Any:
        worker_id = params.get("worker_id")
        if not worker_id:
            raise ValueError("Missing required parameter: worker_id")
        reason = str(params.get("reason") or "ui_pause")
        workspace_path = params.get("workspace_path") or None
        from opengis_backend.worker import get_worker_manager

        return {
            "worker": get_worker_manager().pause_worker(
                str(worker_id),
                reason=reason,
                workspace_path=str(workspace_path) if workspace_path else None,
            )
        }

    async def _handle_worker_restart(self, params: dict) -> Any:
        worker_id = params.get("worker_id")
        if not worker_id:
            raise ValueError("Missing required parameter: worker_id")
        code = params.get("code")
        if code is not None:
            code = str(code)
        reason = str(params.get("reason") or "ui_restart")
        initial_health_timeout = float(params.get("initial_health_timeout") or 1.5)
        workspace_path = params.get("workspace_path") or None
        from opengis_backend.worker import get_worker_manager

        return {
            "worker": get_worker_manager().restart_worker(
                str(worker_id),
                code=code,
                reason=reason,
                initial_health_timeout=initial_health_timeout,
                workspace_path=str(workspace_path) if workspace_path else None,
            )
        }

    async def _handle_worker_delete(self, params: dict) -> Any:
        worker_id = params.get("worker_id")
        if not worker_id:
            raise ValueError("Missing required parameter: worker_id")
        workspace_path = params.get("workspace_path") or None
        from opengis_backend.worker import get_worker_manager

        return {
            "worker": get_worker_manager().delete_worker(
                str(worker_id),
                workspace_path=str(workspace_path) if workspace_path else None,
            )
        }

    async def _handle_user_skill_list(self, params: dict) -> Any:
        workspace_path = params.get("workspace_path") or None
        skills = UserSkillDiscovery(workspace_path=workspace_path).list()
        return {"skills": [item.to_dict() for item in skills]}

    async def _handle_user_skill_load(self, params: dict) -> Any:
        name = params.get("name")
        if not name:
            raise ValueError("Missing required parameter: name")
        workspace_path = params.get("workspace_path") or None
        info = UserSkillDiscovery(workspace_path=workspace_path).require(str(name))
        return {"skill": info.to_dict(include_content=True)}

    async def _handle_user_skill_add_source(self, params: dict) -> Any:
        source_path = params.get("path") or params.get("source_path")
        if not source_path:
            raise ValueError("Missing required parameter: path")
        workspace_path = params.get("workspace_path") or None
        scope = str(params.get("scope") or "workspace")
        result = add_source_path(str(source_path), workspace_path=workspace_path, scope=scope)
        skills = UserSkillDiscovery(workspace_path=workspace_path).list()
        return {**result, "skills": [item.to_dict() for item in skills]}

    # ─── Script Runner (user-authored scripts in the agent's sandbox) ───

    def _ensure_script_runner(self) -> ScriptRunner:
        if self._script_runner is None:
            self._script_runner = ScriptRunner(
                tool_registry=self.tool_registry,
                notify_fn=self._safe_notify,
                request_fn=self._safe_request,
            )
        return self._script_runner

    async def _handle_run_script(self, params: dict) -> Any:
        """Run a user-authored Python script in the subprocess sandbox.

        Params
        ------
        code           str — required.  The Python source to execute.
        workspace_path str — optional.  Child cwd; defaults to sidecar cwd.
        run_id         str — optional.  Correlation id (auto-gen if missing).
        exec_timeout   float — optional.  Per-run budget (s). Default 600.

        Returns
        -------
        dict with ok/run_id/output/logs/error/duration_ms, identical to
        the final ``rpc.code.script_done`` notification. Caller can use
        either the response or the notification — both are authoritative.
        """
        code = params.get("code")
        if not code or not isinstance(code, str):
            raise ValueError("Missing required parameter: code (str)")
        workspace_path = params.get("workspace_path") or None
        run_id = params.get("run_id") or None
        try:
            exec_timeout = float(params.get("exec_timeout", DEFAULT_EXEC_TIMEOUT))
        except (TypeError, ValueError):
            exec_timeout = DEFAULT_EXEC_TIMEOUT
        exec_timeout = min(max(exec_timeout, MIN_EXEC_TIMEOUT), MAX_EXEC_TIMEOUT)  # 1s..1h hard cap

        runner = self._ensure_script_runner()
        if runner.is_running:
            return {
                "ok": False,
                "error": "another_script_running",
                "message": (
                    "A script is already running. Cancel it first via "
                    "rpc.code.cancel_script before starting another."
                ),
            }

        async def _drive() -> dict:
            return await runner.run(
                code,
                run_id=run_id,
                workspace_path=workspace_path,
                exec_timeout=exec_timeout,
            )

        # Track the task so cancel_script can find it (though the runner
        # itself also tracks via _current_executor and can be interrupted
        # from any thread).
        self._current_script_task = asyncio.create_task(_drive())
        try:
            return await self._current_script_task
        finally:
            self._current_script_task = None

    async def _handle_cancel_script(self, params: dict) -> Any:
        """Interrupt the currently-running script, if any."""
        if self._script_runner is None or not self._script_runner.is_running:
            return {"status": "idle"}
        cancelled = self._script_runner.cancel()
        return {"status": "cancelling" if cancelled else "idle"}

    async def _handle_agent_configure(self, params: dict) -> Any:
        """
        Configure / re-configure the LLM used by the agent.

        Params: { protocol, model, api_key, base_url }
        Only rebuilds the agent if the config actually changed.
        """
        # Compute a hash of the incoming config to detect changes.
        # 注意：API Key 使用哈希值而非明文，防止日志泄露。
        import hashlib

        api_key = params.get("api_key", "")
        # 对 API Key 取 MD5 哈希（仅用于变更检测，不存储明文）
        key_hash = hashlib.md5(api_key.encode()).hexdigest() if api_key else ""
        config_parts = [
            params.get("protocol", "openai"),
            params.get("model", ""),
            key_hash,
            params.get("base_url", ""),
        ]
        config_str = "|".join(config_parts)
        config_hash = hashlib.md5(config_str.encode()).hexdigest()

        if "protocol" in params:
            settings.llm_protocol = params["protocol"]
        if "model" in params:
            settings.llm_model = params["model"]
        if "api_key" in params:
            settings.llm_api_key = params["api_key"]
        if "base_url" in params:
            settings.llm_base_url = params["base_url"]
        # Only rebuild agent if config actually changed.
        if config_hash != self._last_llm_config_hash:
            self._last_llm_config_hash = config_hash
            self._agent = None
            logger.info("LLM config changed, agent will be rebuilt on next chat.")
        else:
            logger.debug("LLM config unchanged, keeping existing agent.")

        return {"status": "configured", "model": settings.llm_model}

    async def _handle_agent_test_connection(self, params: dict) -> Any:
        """Test LLM connectivity by sending a minimal completion request.

        Params: { protocol?, model?, api_key?, base_url? }
        Falls back to current settings for any missing field.
        Returns: { ok: true } or { ok: false, error: "..." }
        """
        from opengis_backend.agent.llm import LLMConfig, build_llm_caller

        protocol = params.get("protocol", settings.llm_protocol)
        model = params.get("model", settings.llm_model)
        api_key = params.get("api_key", settings.llm_api_key)
        base_url = params.get("base_url", settings.llm_base_url)

        if not api_key:
            return {"ok": False, "error": "API key is required"}

        config = LLMConfig(
            protocol=protocol,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        caller = build_llm_caller(config)
        test_messages = [
            {"role": "user", "content": "Say OK"},
            ]
        try:
            reply = await asyncio.to_thread(caller, test_messages)
            content = reply.content or ""
            if content:
                return {"ok": True}
            else:
                return {"ok": False, "error": "Empty response from LLM"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _handle_agent_chat(self, params: dict) -> Any:
        """Start an agent conversation. Streams via JSON-RPC notifications."""
        workspace_path = params.get("workspace_path") or None
        lock_key = _normalise_workspace(workspace_path)
        busy = await self._reserve_workspace_lock(lock_key, source="chat")
        if busy is not None:
            return busy
        try:
            queue_item, agent_profile, session_store = self._build_queue_item_from_chat_params(params)
        except Exception:
            await self._release_workspace_lock(lock_key)
            raise
        return await self._execute_agent_queue_item(
            queue_item,
            lock_key=lock_key,
            agent_profile=agent_profile,
            session_store=session_store,
        )

    def _build_queue_item_from_chat_params(
        self,
        params: dict,
    ) -> tuple[AgentQueueItem, Any, SessionStore]:
        """Create durable inbox + queue records from chat-style params."""
        message = params.get("message")
        if not message:
            raise ValueError("Missing required parameter: message")

        # Optional user instructions — global personalization prompt
        user_instructions = params.get("user_instructions") or None

        # Optional workspace root — used by ScriptArchive to decide where
        # to persist step scripts. None means "no workspace open → fall
        # back to appdata/agent-runs/<run_id>/".
        workspace_path = params.get("workspace_path") or None
        profile_name = params.get("agent_profile") or params.get("profile_name") or None

        # Process attachments: detect workflow vs regular files vs tool groups.
        attachments = params.get("attachments") or []
        workflow_doc: WorkflowDocument | None = None
        regular_attachments: list[dict] = []
        tool_groups: list[str] | None = None

        for att in attachments:
            if att.get("type") == "workflow":
                # Parse the workflow file.
                try:
                    workflow_doc = self._parse_workflow_attachment(att)
                except Exception as e:
                    logger.warning("Failed to parse workflow attachment: %s", e)
                    # Fall back to treating it as a regular file.
                    regular_attachments.append(att)
            elif att.get("type") == "tool_group":
                # Extract tool groups to activate.
                groups = att.get("tool_groups") or []
                if groups:
                    # Merge: keep "core" always available.
                    tool_groups = list(set(groups + ["core"]))
            else:
                regular_attachments.append(att)

        if workflow_doc is None:
            pasted_workflow = detect_pasted_workflow_message(message)
            if pasted_workflow is not None:
                workflow_doc = pasted_workflow.workflow
                message = workflow_run_prompt(workflow_doc, pasted_workflow.context)

        if regular_attachments:
            message = self._inject_attachments(message, regular_attachments)

        workflow_meta: dict[str, Any] = {}
        if workflow_doc is not None:
            try:
                workflow_meta = WorkflowDocumentStore(workspace_path).save(workflow_doc)
            except Exception:
                logger.debug("workflow document persistence failed (non-fatal)", exc_info=True)

        agent_profile = None
        if profile_name:
            agent_profile = AgentProfileStore(workspace_path).get(str(profile_name))
        inbox_item = AgentInboxItem.create(
            prompt=message,
            conversation_id=params.get("conversation_id"),
            profile_name=agent_profile.name if agent_profile is not None else str(profile_name or "gis-build"),
            metadata={
                "workspace_path": workspace_path,
                "has_workflow": workflow_doc is not None,
                "tool_groups": tool_groups,
                "generate_title": bool(params.get("generate_title", False)),
                **workflow_meta,
            },
        )
        session_store = SessionStore(workspace_path)
        session_store.add_inbox(inbox_item)
        queue_item = self._agent_queue.enqueue(
            AgentQueueItem.create(
                inbox=inbox_item,
                message=message,
                workspace_path=workspace_path,
                workflow=workflow_doc,
                tool_groups=tool_groups,
                user_instructions=user_instructions,
                profile_name=profile_name,
                conversation_id=params.get("conversation_id"),
                metadata={
                    "has_workflow": workflow_doc is not None,
                    "tool_groups": tool_groups,
                    "workspace_path": workspace_path,
                    "generate_title": bool(params.get("generate_title", False)),
                    **workflow_meta,
                },
            )
        )
        session_store.update_inbox(
            inbox_item.id,
            status=InboxStatus.QUEUED,
            metadata={"queue_id": queue_item.id},
        )

        return queue_item, agent_profile, session_store

    def _resolve_queue_item(self, params: dict) -> AgentQueueItem | None:
        queue_id = params.get("queue_id")
        inbox_id = params.get("inbox_id")
        workspace = params.get("workspace_path")
        if queue_id:
            item = self._agent_queue.get(str(queue_id))
            if item is not None:
                return item
        if inbox_id:
            item = self._agent_queue.get_by_inbox_id(str(inbox_id))
            if item is not None:
                return item
        if not workspace:
            return None
        store = SessionStore(str(workspace))
        raw = None
        if inbox_id:
            raw = store.get_inbox(str(inbox_id))
        if raw is None and queue_id:
            raw = store.find_inbox_by_queue_id(str(queue_id))
        if raw is None:
            return None
        return self._agent_queue.ensure_from_inbox(raw)

    def _restore_queue_from_workspace(
        self,
        workspace: str,
        *,
        limit: int = 100,
    ) -> list[AgentQueueItem]:
        store = SessionStore(workspace)
        restored: list[AgentQueueItem] = []
        for raw in store.list_resumable_inbox(limit=limit):
            item = self._agent_queue.ensure_from_inbox(raw)
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            if not isinstance(metadata.get("queue_id"), str):
                store.update_inbox(raw["id"], metadata={"queue_id": item.id})
            restored.append(item)
        return restored

    async def _execute_agent_queue_item(
        self,
        queue_item: AgentQueueItem,
        *,
        lock_key: str,
        agent_profile: Any = None,
        session_store: SessionStore | None = None,
    ) -> dict[str, Any]:
        """Execute one queued agent item using the current streaming RPC contract."""
        agent = self._ensure_agent()
        session_store = session_store or SessionStore(queue_item.workspace_path)
        ctx = ToolContext(
            notify_fn=self._safe_notify,
            request_fn=self._safe_request,
            conversation_id=queue_item.conversation_id,
            meta={"workspace_path": queue_item.workspace_path} if queue_item.workspace_path else {},
        )
        ctx.meta["_approval_callback"] = self._approval_callback
        ctx.meta["_inbox_id"] = queue_item.inbox.id
        ctx.meta["_queue_id"] = queue_item.id
        if agent_profile is not None:
            ctx.meta["agent_profile"] = agent_profile

        # We don't know the run_id until ScriptArchive.for_run generates
        # one inside agent.run(); callers normally reserve the lock before
        # reaching this method, but direct/internal execution paths can still
        # enter without a reserved run id.
        async with self._workspace_lock_guard:
            self._workspace_locks.setdefault(lock_key, "pending")

        async def _drive():
            try:
                queue_item.mark(AgentQueueStatus.RUNNING)
                session_store.update_inbox(queue_item.inbox.id, status=InboxStatus.RUNNING)
                async for event in agent.run(
                    queue_item.message,
                    context=ctx,
                    workflow=queue_item.workflow,
                    active_tool_groups=queue_item.tool_groups,
                    user_instructions=queue_item.user_instructions,
                ):
                    # Promote the lock owner now that we know the run_id.
                    rid = (ctx.meta or {}).get("run_id")
                    if rid:
                        async with self._workspace_lock_guard:
                            should_promote = self._workspace_locks.get(lock_key) == "pending"
                            if should_promote:
                                self._workspace_locks[lock_key] = rid
                        if should_promote:
                            queue_item.mark(AgentQueueStatus.RUNNING, run_id=rid)
                            agent_session = (ctx.meta or {}).get("_agent_session")
                            session_store.update_inbox(
                                queue_item.inbox.id,
                                status=InboxStatus.RUNNING,
                                run_id=rid,
                                session_id=getattr(agent_session, "id", None),
                            )
                    await self._emit_agent_event(event)
                # After successful run, generate a conversation title
                # in the background (non-blocking) to avoid delaying
                # the stream_end event.
                agent_session = (ctx.meta or {}).get("_agent_session")
                queue_item.mark(
                    AgentQueueStatus.SUCCESS,
                    run_id=(ctx.meta or {}).get("run_id"),
                )
                session_store.update_inbox(
                    queue_item.inbox.id,
                    status=InboxStatus.SUCCESS,
                    run_id=(ctx.meta or {}).get("run_id"),
                    session_id=getattr(agent_session, "id", None),
                )
                if bool(queue_item.metadata.get("generate_title", False)):
                    asyncio.create_task(
                        self._generate_title_if_needed(
                            queue_item.message,
                            queue_item.conversation_id,
                            queue_item.workspace_path,
                        )
                    )
            except asyncio.CancelledError:
                queue_item.mark(
                    AgentQueueStatus.CANCELLED,
                    run_id=(ctx.meta or {}).get("run_id"),
                )
                session_store.update_inbox(
                    queue_item.inbox.id,
                    status=InboxStatus.CANCELLED,
                    run_id=(ctx.meta or {}).get("run_id"),
                )
                await self._send_notification("chat.cancelled", {})
                raise
            except Exception as e:
                traceback.print_exc()
                queue_item.mark(
                    AgentQueueStatus.ERROR,
                    run_id=(ctx.meta or {}).get("run_id"),
                    error=str(e),
                )
                session_store.update_inbox(
                    queue_item.inbox.id,
                    status=InboxStatus.ERROR,
                    run_id=(ctx.meta or {}).get("run_id"),
                    error=str(e),
                )
                part = event_to_message_part(
                    AgentEvent(
                        type=AgentEventType.ERROR,
                        data={
                            "error": self._humanize_error(str(e)),
                            "run_id": (ctx.meta or {}).get("run_id") or "",
                        },
                    )
                )
                if part is not None:
                    await self._send_notification("chat.message_part", {"part": part.to_dict()})

        # Track the task so agent.cancel can interrupt.
        self._current_agent_task = asyncio.create_task(_drive())
        self._current_agent_lock_key = lock_key
        try:
            await self._current_agent_task
        finally:
            self._current_agent_task = None
            if self._current_agent_lock_key == lock_key:
                self._current_agent_lock_key = None
            # D2: always release the lock, even on error/cancel.
            await self._release_workspace_lock(lock_key)

        status = {
            AgentQueueStatus.SUCCESS: "completed",
            AgentQueueStatus.ERROR: "error",
            AgentQueueStatus.CANCELLED: "cancelled",
        }.get(queue_item.status, queue_item.status.value)
        return {
            "status": status,
            "run_id": (ctx.meta or {}).get("run_id"),
            "inbox_id": queue_item.inbox.id,
            "queue_id": queue_item.id,
            "item": queue_item.to_dict(),
        }

    async def _handle_agent_cancel(self, params: dict) -> Any:
        """Interrupt the running agent — kill the subprocess and release locks."""
        import time as _time
        _t0 = _time.perf_counter()
        def _elapsed():
            return f"+{(_time.perf_counter() - _t0)*1000:.1f}ms"

        logger.debug("[CANCEL] %s _handle_agent_cancel ENTERED", _elapsed())
        cancelled = False
        agent = self._agent
        logger.debug("[CANCEL] %s agent=%s, _current_agent_task=%s",
                    _elapsed(), agent is not None,
                    self._current_agent_task is not None and not self._current_agent_task.done()
                    if self._current_agent_task else None)

        if agent is not None:
            # Cancel any active sub-agent branches FIRST — they hold
            # independent loops and executors that won't be reached by
            # the main loop's interrupt().
            subagent_tracker = getattr(agent, "_active_subagent_tracker", None)
            if subagent_tracker is not None:
                try:
                    subagent_tracker.cancel_all()
                    logger.debug("[CANCEL] %s subagent tracker.cancel_all() done", _elapsed())
                except Exception as e:
                    logger.warning("[CANCEL] subagent tracker.cancel_all failed: %s", e)

            # Signal the agent loop to stop at the next iteration.
            current_loop = getattr(agent, "_current_loop", None)
            logger.debug("[CANCEL] %s current_loop=%s, _interrupted_before=%s",
                        _elapsed(), current_loop is not None,
                        getattr(current_loop, "_interrupted", "N/A") if current_loop else "N/A")
            if current_loop is not None and hasattr(current_loop, "interrupt"):
                try:
                    current_loop.interrupt()
                    logger.debug("[CANCEL] %s loop.interrupt() done, _interrupted=%s",
                                _elapsed(), current_loop._interrupted)
                except Exception as e:
                    logger.warning("[CANCEL-DEBUG] %s loop.interrupt failed: %s", _elapsed(), e)

            executor = getattr(agent, "current_executor", None)
            logger.debug("[CANCEL] %s executor=%s, proc=%s",
                        _elapsed(), executor is not None,
                        getattr(getattr(executor, "_proc", None), "pid", None) if executor else None)
            if executor is not None:
                try:
                    executor.interrupt()
                    logger.debug("[CANCEL] %s executor.interrupt() done", _elapsed())
                except Exception as e:
                    logger.warning("[CANCEL-DEBUG] %s executor.interrupt failed: %s", _elapsed(), e)
                try:
                    executor.cleanup()
                    logger.debug("[CANCEL] %s executor.cleanup() done", _elapsed())
                except Exception as e:
                    logger.warning("[CANCEL-DEBUG] %s executor.cleanup failed: %s", _elapsed(), e)
                agent.current_executor = None

            current_runner = getattr(agent, "_current_runner", None)
            logger.debug("[CANCEL] %s current_runner=%s, worker_thread_id=%s",
                        _elapsed(), current_runner is not None,
                        getattr(current_runner, "_worker_thread_id", None) if current_runner else None)
            if current_runner is not None:
                try:
                    result = current_runner.interrupt_worker_thread()
                    logger.debug("[CANCEL] %s interrupt_worker_thread() returned %s", _elapsed(), result)
                except Exception as e:
                    logger.warning("[CANCEL-DEBUG] %s thread interrupt failed: %s", _elapsed(), e)

        if self._current_agent_task and not self._current_agent_task.done():
            self._current_agent_task.cancel()
            cancelled = True
            logger.debug("[CANCEL] %s asyncio task cancelled, waiting...", _elapsed())
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._current_agent_task),
                    timeout=TITLE_GEN_TIMEOUT,
                )
                logger.debug("[CANCEL] %s task finished gracefully", _elapsed())
            except (TimeoutError, asyncio.CancelledError, Exception) as e:
                logger.debug("[CANCEL] %s task wait ended with: %s(%s)", _elapsed(), type(e).__name__, e)
            self._current_agent_task = None
        else:
            logger.debug("[CANCEL] %s NO active agent task to cancel!", _elapsed())

        released = await self._release_cancelled_workspace_locks(params)
        if released:
            logger.debug("[CANCEL] %s released locks: %s", _elapsed(), released)

        logger.debug("[CANCEL] %s _handle_agent_cancel DONE, returning status=%s",
                    _elapsed(), "cancelled" if cancelled else "idle")
        return {"status": "cancelled" if cancelled else "idle"}

    async def _reserve_workspace_lock(self, lock_key: str, *, source: str) -> dict[str, Any] | None:
        """Atomically reserve a workspace run slot or return a busy response."""
        async with self._workspace_lock_guard:
            if lock_key in self._workspace_locks:
                # If the previous task is actually done (cancel propagated),
                # release the stale lock and proceed.
                if self._current_agent_task is None or self._current_agent_task.done():
                    logger.info("[%s] stale lock on %s (task done), releasing.", source, lock_key)
                    self._workspace_locks.pop(lock_key, None)
                else:
                    owner = self._workspace_locks[lock_key]
                    return {
                        "status": "busy",
                        "message": (
                            f"another agent run is active on this workspace "
                            f"(run_id={owner}); stop it first via rpc.agent.interrupt."
                        ),
                        "owner_run_id": owner,
                    }
            self._workspace_locks[lock_key] = "pending"
        return None

    async def _release_workspace_lock(self, lock_key: str) -> None:
        async with self._workspace_lock_guard:
            self._workspace_locks.pop(lock_key, None)

    async def _release_cancelled_workspace_locks(self, params: dict) -> list[str]:
        workspace_path = params.get("workspace_path") or None
        run_id = params.get("run_id") or params.get("owner_run_id")
        async with self._workspace_lock_guard:
            if workspace_path:
                lock_key = _normalise_workspace(str(workspace_path))
                if lock_key in self._workspace_locks:
                    self._workspace_locks.pop(lock_key, None)
                    return [lock_key]
                return []
            if run_id:
                released = [
                    key for key, owner in self._workspace_locks.items()
                    if owner == run_id
                ]
                for key in released:
                    self._workspace_locks.pop(key, None)
                return released
            # If the caller omitted a workspace, release only the active run
            # tracked by this handler instead of clearing every workspace lock.
            lock_key = self._current_agent_lock_key
            if lock_key and lock_key in self._workspace_locks:
                self._workspace_locks.pop(lock_key, None)
                return [lock_key]
            return []

    # ─── Debug: log level control ──────────────────────────────────────

    async def _handle_set_log_level(self, params: dict) -> Any:
        """Change the backend log level at runtime.

        Params: {"level": "DEBUG" | "INFO" | "WARNING" | "ERROR"}
        """
        import logging as _logging
        from opengis_backend.runtime.logging import set_level

        level_name = (params.get("level") or "INFO").upper()
        level = getattr(_logging, level_name, None)
        if level is None:
            return {"status": "error", "message": f"Unknown level: {level_name}"}

        set_level(level)
        return {"status": "ok", "level": level_name}

    async def _handle_get_log_level(self, params: dict) -> Any:
        """Return the current log level."""
        from opengis_backend.runtime.logging import get_level
        return {"status": "ok", "level": get_level()}

    # ─── Workspace: install built-in templates ───────────────────────

    async def _handle_install_templates(self, params: dict) -> Any:
        """Install built-in workflow templates to workspace.

        Params: {"workspace_path": str}
        Idempotent — only copies files that don't already exist.
        """
        from pathlib import Path

        workspace = params.get("workspace_path")
        if not workspace:
            return {"status": "error", "message": "Missing workspace_path"}

        try:
            from opengis_backend.workspace.manager import WorkspaceManager
            wm = WorkspaceManager()
            ws = Path(workspace)
            if not ws.is_dir():
                return {"status": "error", "message": f"Not a directory: {workspace}"}
            wm._ensure_builtin_templates(ws)
            return {"status": "ok", "workspace": workspace}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ─── A4: workspace revert ──────────────────────────────────────────

    async def _handle_workspace_revert_run(self, params: dict) -> Any:
        """Reset the workspace to a previous run's pre-run snapshot.

        Params
        ------
        workspace_path : str — required.
        run_id         : str — required.

        Returns {"status":"ok","reset_to":<sha>} or {"status":"error",...}.
        We refuse if a run is active on the workspace — the user must
        stop it first.
        """
        workspace = params.get("workspace_path")
        run_id = params.get("run_id")
        if not workspace or not run_id:
            raise ValueError("Missing required parameters: workspace_path, run_id")

        lock_key = _normalise_workspace(workspace)
        if lock_key in self._workspace_locks:
            return {
                "status": "busy",
                "message": "stop the active run first before reverting.",
            }

        ra = RunArchive.load(workspace, run_id)
        if ra is None:
            return {"status": "not_found", "message": f"no archive for run_id={run_id}"}
        pre_sha = ra.meta.get("pre_sha")
        if not pre_sha:
            return {
                "status": "no_snapshot",
                "message": (
                    "this run has no pre-snapshot (workspace was not git-tracked when it started)."
                ),
            }
        try:
            self._workspace_manager.reset_hard(workspace, pre_sha)
        except WorkspaceManagerError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "ok", "reset_to": pre_sha, "run_id": run_id}

    # ─── C3: run listing / inspection / replay ──────────────────────────────────────────

    async def _handle_runs_list(self, params: dict) -> Any:
        workspace = params.get("workspace_path")
        limit = int(params.get("limit", 50))
        runs = RunArchive.list_runs(workspace, limit=limit)
        return {
            "runs": [
                {
                    "run_id": r.run_id,
                    "status": r.status,
                    "prompt": r.prompt,
                    "created_at": r.created_at,
                    "finished_at": r.finished_at,
                    "step_count": r.step_count,
                    "pre_sha": r.pre_sha,
                    "post_sha": r.post_sha,
                }
                for r in runs
            ]
        }

    async def _handle_runs_get(self, params: dict) -> Any:
        workspace = params.get("workspace_path")
        run_id = params.get("run_id")
        if not run_id:
            raise ValueError("Missing required parameter: run_id")
        ra = RunArchive.load(workspace, run_id)
        if ra is None:
            return {"status": "not_found"}
        return {
            "status": "ok",
            "meta": ra.meta,
            "steps": ra.read_steps(),
            "tool_calls": ra.read_tool_calls(),
            "tool_call_events": ra.read_tool_call_events(),
            "artifacts": ra.read_artifacts(),
            "events": ra.read_events(),
            "message_parts": ra.read_message_parts(),
            "llm_usage": ra.read_llm_usage(),
        }

    def _operation_store(self, params: dict) -> OperationStore:
        workspace = params.get("workspace_path")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        return OperationStore.from_workspace(workspace)

    async def _handle_operations_list(self, params: dict) -> Any:
        store = self._operation_store(params)
        query = str(params.get("query") or "")
        limit = int(params.get("limit") or 50)
        return {
            "status": "ok",
            "operation_root": str(store.root),
            "operation_roots": store.roots,
            "operations": store.list(query=query, limit=limit),
        }

    async def _handle_operations_get(self, params: dict) -> Any:
        operation_id = str(params.get("operation_id") or "").strip()
        if not operation_id:
            raise ValueError("Missing required parameter: operation_id")
        store = self._operation_store(params)
        operation = store.load(
            operation_id,
            include_readme=bool(params.get("include_readme", True)),
            include_code=bool(params.get("include_code", False)),
            max_code_chars=int(params.get("max_code_chars") or 40000),
        )
        return {"status": "ok", "operation": operation}

    async def _handle_operations_run(self, params: dict) -> Any:
        operation_id = str(params.get("operation_id") or "").strip()
        if not operation_id:
            raise ValueError("Missing required parameter: operation_id")
        run_params = params.get("params") or {}
        if not isinstance(run_params, dict):
            raise ValueError("params must be a JSON object")
        timeout_seconds = int(params.get("timeout_seconds") or 600)
        record = self._operation_store(params).run(
            operation_id,
            run_params,
            timeout_seconds=timeout_seconds,
        )
        return {"status": "ok", "success": True, **record}

    async def _handle_agent_profiles_list(self, params: dict) -> Any:
        """List built-in plus workspace-defined agent profiles."""
        workspace = params.get("workspace_path")
        profiles = AgentProfileStore(workspace).load_all()
        return {
            "profiles": [
                profile.to_dict()
                for profile in sorted(profiles.values(), key=lambda p: p.name)
            ]
        }

    async def _handle_agent_profiles_install_defaults(self, params: dict) -> Any:
        """Install the default profile config into ``.opengis/agents.json``."""
        workspace = params.get("workspace_path")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        path = AgentProfileStore(workspace).install_defaults()
        if not path:
            return {"status": "error", "message": "failed to install default profiles"}
        return {"status": "ok", "path": path}

    async def _handle_agent_sessions_list(self, params: dict) -> Any:
        """List recent agent sessions for the workspace."""
        workspace = params.get("workspace_path")
        limit = int(params.get("limit", 100))
        return {"sessions": SessionStore(workspace).list_recent(limit=limit)}

    async def _handle_agent_inbox_list(self, params: dict) -> Any:
        """List durable prompt admission records for the workspace."""
        workspace = params.get("workspace_path")
        status = params.get("status")
        if status is not None:
            status = str(status)
            valid = {item.value for item in InboxStatus}
            if status not in valid:
                raise ValueError(f"status must be one of: {', '.join(sorted(valid))}")
        limit = int(params.get("limit", 100))
        return {
            "items": SessionStore(workspace).list_inbox(status=status, limit=limit)
        }

    async def _handle_agent_queue_submit(self, params: dict) -> Any:
        """Submit an agent prompt to the queue without executing it yet."""
        queue_item, _agent_profile, _session_store = self._build_queue_item_from_chat_params(params)
        return {
            "status": "queued",
            "queue_id": queue_item.id,
            "inbox_id": queue_item.inbox.id,
            "item": queue_item.to_dict(),
        }

    async def _handle_agent_queue_run(self, params: dict) -> Any:
        """Execute a queued agent item by queue_id."""
        queue_item = self._resolve_queue_item(params)
        if queue_item is None:
            return {"status": "not_found", "message": "no queue item found"}
        if queue_item.status != AgentQueueStatus.QUEUED:
            return {
                "status": "invalid_state",
                "message": f"queue item is {queue_item.status.value}, not queued",
                "item": queue_item.to_dict(),
            }
        return await self._run_queue_item_with_lock(queue_item)

    async def _run_queue_item_with_lock(self, queue_item: AgentQueueItem) -> dict[str, Any]:
        """Run one queue item under the workspace serial lock."""
        lock_key = _normalise_workspace(queue_item.workspace_path)
        busy = await self._reserve_workspace_lock(lock_key, source="queue.run")
        if busy is not None:
            return busy

        agent_profile = None
        if queue_item.profile_name:
            agent_profile = AgentProfileStore(queue_item.workspace_path).get(queue_item.profile_name)
        return await self._execute_agent_queue_item(
            queue_item,
            lock_key=lock_key,
            agent_profile=agent_profile,
            session_store=SessionStore(queue_item.workspace_path),
        )

    async def _handle_agent_queue_get(self, params: dict) -> Any:
        """Get one queue item by queue_id or inbox_id."""
        queue_item = self._resolve_queue_item(params)
        if queue_item is None:
            return {"status": "not_found"}
        return {"status": "ok", "item": queue_item.to_dict()}

    async def _handle_agent_queue_resume(self, params: dict) -> Any:
        """Restore queued/resumable inbox items from workspace storage."""
        workspace = params.get("workspace_path")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        limit = int(params.get("limit", 100))
        items = [item.to_dict() for item in self._restore_queue_from_workspace(str(workspace), limit=limit)]
        return {"status": "ok", "items": items}

    async def _handle_agent_queue_retry(self, params: dict) -> Any:
        """Reset a failed/cancelled queue item back to queued."""
        queue_item = self._resolve_queue_item(params)
        if queue_item is None:
            return {"status": "not_found"}
        if queue_item.status not in {AgentQueueStatus.ERROR, AgentQueueStatus.CANCELLED}:
            return {
                "status": "invalid_state",
                "message": f"queue item is {queue_item.status.value}, not retryable",
                "item": queue_item.to_dict(),
            }
        queue_item.reset_for_retry()
        SessionStore(queue_item.workspace_path).update_inbox(
            queue_item.inbox.id,
            status=InboxStatus.QUEUED,
            error="",
            metadata={"queue_id": queue_item.id, "retry": True},
        )
        return {"status": "queued", "item": queue_item.to_dict()}

    async def _handle_agent_queue_cancel(self, params: dict) -> Any:
        """Cancel a queued item, or delegate to active agent interrupt if running."""
        queue_item = self._resolve_queue_item(params)
        if queue_item is None:
            return {"status": "not_found"}
        if queue_item.status == AgentQueueStatus.RUNNING:
            result = await self._handle_agent_cancel(params)
            return {"status": result.get("status", "cancelled"), "item": queue_item.to_dict()}
        if queue_item.status == AgentQueueStatus.QUEUED:
            queue_item.cancel()
            SessionStore(queue_item.workspace_path).update_inbox(
                queue_item.inbox.id,
                status=InboxStatus.CANCELLED,
                error="cancelled before execution",
            )
            return {"status": "cancelled", "item": queue_item.to_dict()}
        return {
            "status": "invalid_state",
            "message": f"queue item is {queue_item.status.value}, not cancellable",
            "item": queue_item.to_dict(),
        }

    async def _handle_agent_queue_process(self, params: dict) -> Any:
        """Process queued items for one workspace using the streaming path."""
        workspace = params.get("workspace_path") or None
        lock_key = _normalise_workspace(workspace)
        existing = self._queue_processors.get(lock_key)
        if existing is not None and not existing.done():
            return {"status": "busy", "message": "queue processor is already running"}
        limit = int(params.get("limit", 1))

        async def _process() -> list[dict[str, Any]]:
            if workspace:
                self._restore_queue_from_workspace(str(workspace), limit=max(limit, 100))
            processed: list[dict[str, Any]] = []
            for _ in range(max(1, limit)):
                queue_item = self._agent_queue.next_queued(workspace_path=workspace)
                if queue_item is None:
                    break
                result = await self._run_queue_item_with_lock(queue_item)
                processed.append(result)
                if result.get("status") == "busy":
                    break
            return processed

        task = asyncio.create_task(_process())
        self._queue_processors[lock_key] = task
        try:
            processed = await task
        finally:
            if self._queue_processors.get(lock_key) is task:
                self._queue_processors.pop(lock_key, None)
        return {"status": "ok", "processed": processed}

    async def _handle_agent_queue_list(self, params: dict) -> Any:
        """List in-process agent queue items for the current backend."""
        status = params.get("status")
        if status is not None:
            status = str(status)
            valid = {item.value for item in AgentQueueStatus}
            if status not in valid:
                raise ValueError(f"status must be one of: {', '.join(sorted(valid))}")
        limit = int(params.get("limit", 100))
        return {"items": self._agent_queue.list(status=status, limit=limit)}

    async def _handle_agent_artifacts_list(self, params: dict) -> Any:
        """List recent workspace artifact references produced by agent tools."""
        workspace = params.get("workspace_path")
        limit = int(params.get("limit", 100))
        return {"artifacts": ArtifactIndex(workspace).list_recent(limit=limit)}

    async def _handle_agent_permissions_list(self, params: dict) -> Any:
        """List recent permission requests, including pending approvals."""
        status = params.get("status")
        if status is not None:
            status = str(status)
            if status not in {"pending", "resolved"}:
                raise ValueError("status must be 'pending' or 'resolved'")
        limit = int(params.get("limit", 100))
        return {
            "requests": self._permission_requests.list(status=status, limit=limit)
        }

    async def _handle_agent_permission_rules_list(self, params: dict) -> Any:
        """List persisted workspace permission rules."""
        workspace = params.get("workspace_path")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        return {"rules": PermissionRuleStore(workspace).list_rules()}

    async def _handle_agent_permission_rules_add(self, params: dict) -> Any:
        """Add a persisted workspace permission rule."""
        workspace = params.get("workspace_path")
        tool = params.get("tool") or params.get("pattern")
        action_raw = params.get("action")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        if not tool:
            raise ValueError("Missing required parameter: tool")
        try:
            action = PermissionAction(str(action_raw))
        except ValueError:
            raise ValueError("action must be one of: allow, ask, deny")
        rule = PermissionRuleStore(workspace).add_rule(
            tool=str(tool),
            action=action,
            scope=str(params.get("scope") or "workspace"),
            reason=str(params.get("reason") or ""),
            profile_name=params.get("profile_name"),
        )
        return {"status": "ok", "rule": rule}

    async def _handle_agent_permission_rules_remove(self, params: dict) -> Any:
        """Remove a persisted workspace permission rule."""
        workspace = params.get("workspace_path")
        rule_id = params.get("rule_id") or params.get("id")
        if not workspace:
            raise ValueError("Missing required parameter: workspace_path")
        if not rule_id:
            raise ValueError("Missing required parameter: rule_id")
        removed = PermissionRuleStore(workspace).remove_rule(str(rule_id))
        return {"status": "ok" if removed else "not_found", "removed": removed}

    async def _handle_runs_replay(self, params: dict) -> Any:
        """Replay a previous run's prompt on the current model.

        This produces a *new* run (new run_id, new snapshots) — the
        original archive is never mutated. The caller typically treats
        the response as if they'd just called ``chat.user_message``.
        """
        workspace = params.get("workspace_path")
        run_id = params.get("run_id")
        if not run_id:
            raise ValueError("Missing required parameter: run_id")
        ra = RunArchive.load(workspace, run_id)
        if ra is None:
            return {"status": "not_found"}
        prompt = ra.meta.get("prompt") or ""
        # Delegate to chat.user_message so all the lock / snapshot /
        # archive machinery gets exercised.
        return await self._handle_agent_chat(
            {
                "message": prompt,
                "workspace_path": workspace,
                "conversation_id": params.get("conversation_id"),
            }
        )

    # ─── Agent helpers ───
    
    def _ensure_agent(self) -> GISAgent:
        if self._agent is None:
            self._agent = GISAgent(
                tool_registry=self.tool_registry,
                protocol=settings.llm_protocol,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        return self._agent

    async def _generate_title_if_needed(
        self,
        user_message: str,
        conversation_id: str | None,
        workspace_path: str | None = None,
    ) -> None:
        """Generate a short conversation title using the LLM after the first message."""
        if not conversation_id:
            logger.debug("Title generation skipped: no conversation_id")
            return

        # Lazy-load persisted titled set on first call (survives backend restart).
        if self._titled_conversations is None:
            if workspace_path:
                self._titled_conversations = _load_titled_set(workspace_path)
                logger.debug(
                    "Loaded titled conversations from disk: %d entries",
                    len(self._titled_conversations),
                )
            else:
                self._titled_conversations = set()

        if conversation_id in self._titled_conversations:
            logger.debug("Title generation skipped: already titled for %s", conversation_id)
            return
        self._titled_conversations.add(conversation_id)
        if not settings.llm_api_key:
            logger.warning(
                "Title generation skipped: llm_api_key is empty. Model=%s, base_url=%s",
                settings.llm_model,
                settings.llm_base_url,
            )
            return
        try:
            from opengis_backend.agent.llm import LLMConfig, build_llm_caller

            config = LLMConfig(
                protocol=settings.llm_protocol,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
            logger.info(
                "Generating title for conversation %s (model=%s)",
                conversation_id,
                config.model,
            )
            caller = build_llm_caller(config)
            title_messages = [
                {
                    "role": "system",
                    "content": (
                        "Generate a very short title (2-6 words, no quotes) for a conversation "
                        "that starts with the following user message. The title should capture "
                        "the main intent. Reply with ONLY the title, nothing else. "
                        "Use the same language as the user message."
                    ),
                },
                {"role": "user", "content": user_message[:200]},
            ]
            raw_title = await asyncio.to_thread(caller, title_messages)
            title_text = raw_title.content or ""
            title = title_text.strip().strip("\"'").strip()
            logger.info("Generated title: %r (len=%d)", title, len(title))
            if title and len(title) <= 60:
                await self._send_notification(
                    "chat.title_generated",
                    {
                        "conversation_id": conversation_id,
                        "title": title,
                    },
                )
                # Persist so the title survives backend restarts.
                if workspace_path and self._titled_conversations is not None:
                    _save_titled_set(workspace_path, self._titled_conversations)
            else:
                logger.warning("Title rejected: empty or too long (%d chars)", len(title))
        except Exception as e:
            # Identify "upstream temporarily overloaded / rate-limited"
            # cases (e.g. MiniMax 529, Anthropic overloaded_error) so we
            # log them as a quiet one-liner. A full traceback for every
            # provider hiccup makes users think the backend crashed.
            msg = str(e)
            transient = (
                "overloaded_error" in msg
                or "529" in msg
                or "InternalServerError" in msg
                or "RateLimitError" in msg
            )
            if transient:
                logger.warning(
                    "Title generation skipped: upstream LLM temporarily unavailable (%s)",
                    type(e).__name__,
                )
            else:
                logger.warning("Title generation failed: %s", e, exc_info=True)

    def _inject_attachments(self, message: str, attachments: list[dict]) -> str:
        """Read attached files and inject their content into the user message.

        For each attachment, we read the file content and append it as a
        structured context block so the LLM can reference it. Large files
        are truncated to avoid blowing the context window.

        Binary files (tiff, shp, images, etc.) are NOT read — only their
        path is mentioned so the LLM can reference them in code.
        """
        max_file_chars = 8000  # Truncate individual files beyond this
        max_total_chars = 24000  # Total limit across all attachments
        parts: list[str] = [message]
        total_chars = len(message)

        for att in attachments:
            path = att.get("path", "")
            name = att.get("name", "unknown")
            att_type = att.get("type", "file")

            if not path:
                continue

            # Check if adding this attachment would exceed total limit
            # Estimate: even if we skip, the header text is ~200 chars
            if total_chars >= max_total_chars:
                parts.append(
                    f"\n\n---\n📎 **Attachment skipped: `{name}`**\n"
                    f"*(Total attachment size limit reached ({max_total_chars} chars))*"
                )
                continue

            # Check if this is a binary file that shouldn't be read as text.
            from pathlib import Path as _Path

            ext = _Path(name).suffix.lower()
            if ext in _BINARY_EXTENSIONS:
                # Binary file: only provide the path, not the content.
                import os

                try:
                    size_bytes = os.path.getsize(path)
                    size_str = (
                        f"{size_bytes / 1024 / 1024:.1f} MB"
                        if size_bytes > 1024 * 1024
                        else f"{size_bytes / 1024:.1f} KB"
                    )
                except OSError:
                    size_str = "unknown size"

                entry = (
                    f"\n\n---\n📎 **Attached binary file: `{name}`**"
                    f" ({ext} format, {size_str})\n"
                    f"Path: `{path}`\n"
                    f"*(Binary file — content not shown. "
                    f"Use this path in your code to read/process it.)*"
                )
                total_chars += len(entry)
                parts.append(entry)
                continue

            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read(max_file_chars + 100)
                truncated = len(content) > max_file_chars
                if truncated:
                    content = content[:max_file_chars] + "\n... [truncated]"

                label = "Workflow" if att_type == "workflow" else "File"
                trunc_note = f"\n*(Truncated — full file at: `{path}`)*" if truncated else ""
                entry = f"\n\n---\n📎 **Attached {label}: `{name}`**\nPath: `{path}`\n```\n{content}\n```{trunc_note}"
                # Check total limit (allow this entry even if it exceeds, but warn)
                if total_chars + len(entry) > max_total_chars:
                    # Truncate the entry to fit
                    allowed = max_total_chars - total_chars - 200  # reserve for header
                    if allowed > 0:
                        content = content[:allowed] + "\n... [total limit reached]"
                        entry = f"\n\n---\n📎 **Attached {label}: `{name}`**\n```\n{content}\n```"
                    else:
                        parts.append(
                            f"\n\n---\n📎 **Attachment skipped: `{name}`**\n"
                            f"*(Total attachment size limit reached)*"
                        )
                        continue

                total_chars += len(entry)
                parts.append(entry)
            except Exception as e:
                parts.append(f"\n\n---\n📎 **Attached: `{name}`** (failed to read: {e})")

        return "\n".join(parts) if len(parts) > 1 else message

    def _parse_workflow_attachment(self, att: dict) -> "WorkflowDocument":
        """Parse a workflow attachment into a WorkflowDocument.

        Reads the .flow.json file and returns a structured document
        that the WorkflowLoop can execute.
        """
        import json as _json

        path = att.get("path", "")
        if not path:
            raise ValueError("Workflow attachment has no path")

        with open(path, encoding="utf-8") as f:
            raw = _json.load(f)

        return WorkflowDocument.from_json(raw)

    @staticmethod
    def _humanize_error(raw_error: str) -> str:
        """Convert raw error messages into user-friendly descriptions."""
        error_lower = raw_error.lower()

        # API key / auth errors
        if "insufficient balance" in error_lower or "1008" in raw_error:
            return "⚠️ API 余额不足，请检查您的 API Key 账户余额。"
        if (
            "invalid api key" in error_lower
            or "authentication" in error_lower
            or "401" in raw_error
        ):
            return "⚠️ API Key 无效或已过期，请在设置中检查您的 API Key。"
        if "rate limit" in error_lower or "429" in raw_error:
            return "⚠️ 请求频率超限，请稍后再试。"

        # Network errors
        if "timeout" in error_lower or "timed out" in error_lower:
            return "⚠️ 请求缓慢"
        if "connection" in error_lower and ("refused" in error_lower or "error" in error_lower):
            return "⚠️ 无法连接到 AI 服务，请检查网络连接。"

        # Model errors
        if "model not found" in error_lower or "does not exist" in error_lower:
            return "⚠️ 所选模型不可用，请在设置中更换模型。"

        # Sandbox errors
        if "child" in error_lower and "died" in error_lower:
            return "⚠️ 代码执行进程异常退出，请重试。"
        if "exec timeout" in error_lower or "exectimeout" in error_lower:
            return "⚠️ 代码执行超时，脚本运行时间过长。"

        # Workspace errors
        if "another agent run is active" in error_lower:
            return "⚠️ 当前工作区已有任务在运行，请等待完成或中断后再试。"

        # Fallback: show original but with a prefix
        if len(raw_error) > 200:
            return f"⚠️ 发生错误：{raw_error[:200]}..."
        return f"⚠️ 发生错误：{raw_error}"

    async def _emit_agent_event(self, event: AgentEvent) -> None:
        """
        Emit AgentEvent as the canonical native ``chat.message_part`` wire.

        Raw ``chat.*`` step notifications are not emitted here. Agent runs are
        MessagePart-first and MessagePart-only on the live wire.
        """
        part = event_to_message_part(event)
        if part is None:
            logger.warning("AgentEvent %s has no MessagePart projection; dropping live notification.", event.type)
            return
        await self._send_notification("chat.message_part", {"part": part.to_dict()})

    async def _safe_notify(self, method: str, params: dict) -> None:
        """
        Notify callback that the ToolContext hands to tools.

        Sandbox-side tool code may run in a worker thread, so we route
        the websocket send back to the original event loop to avoid
        cross-thread `ws.send_text` issues.
        """
        try:
            if self._loop and self._loop.is_running():
                # If we're already on the loop, send directly.
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is self._loop:
                    await self._send_notification(method, params)
                    return
                # Schedule the send onto the websocket's loop.
                fut = asyncio.run_coroutine_threadsafe(
                    self._send_notification(method, params), self._loop
                )
                # Don't block the worker indefinitely.
                fut.result(timeout=CLEANUP_FUTURE_TIMEOUT)
            else:
                await self._send_notification(method, params)
        except Exception as e:
            print(f"[RpcHandler] notify failed: {e}")

    async def _safe_request(self, method: str, params: dict) -> Any:
        """Thread-safe request callback for context-aware read tools."""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("Frontend request channel is not connected.")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            return await self._request_client(method, params)
        fut = asyncio.run_coroutine_threadsafe(
            self._request_client(method, params),
            self._loop,
        )
        return fut.result(timeout=65)

    def _approval_callback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Synchronously ask the frontend whether an ASK decision is allowed."""
        if self._loop is None or not self._loop.is_running():
            return PermissionDecision(
                PermissionAction.DENY,
                "Approval UI is not connected.",
                decision.rule,
            )
        code = str(arguments.get("code") or "")
        detail = decision.reason or f"Agent requests permission for {tool_name}."
        try:
            arguments_preview = json.dumps(arguments, ensure_ascii=False, default=str)
            if len(arguments_preview) > 2000:
                arguments_preview = arguments_preview[:2000] + "...(truncated)"
        except Exception:
            arguments_preview = repr(arguments)[:2000]
        request = self._permission_requests.create(
            tool_name=tool_name,
            arguments=arguments,
            decision=decision,
        )
        params = {
            "request_id": request.id,
            "tool_name": tool_name,
            "question": f"Allow agent tool call: {tool_name}?",
            "reason": detail,
            "arguments_preview": arguments_preview,
            "rule": decision.rule,
            "danger": True,
            "timeout_seconds": 120,
        }
        if tool_name == "execute_code":
            params = {
                "request_id": request.id,
                "tool_name": tool_name,
                "run_id": str(arguments.get("run_id") or "pending"),
                "step": 0,
                "code": code,
                "risky_operations": [detail],
                "explanation": detail,
                "timeout_seconds": 120,
            }
            method = "rpc.ui.ask.approve_code"
        else:
            method = "rpc.ui.ask.confirm"

        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._request_client(method, params, timeout=120),
                self._loop,
            )
            result = fut.result(timeout=125)
            approved = bool((result or {}).get("approved"))
        except Exception as e:
            logger.warning("approval request failed for %s: %s", tool_name, e)
            approved = False

        if approved:
            self._permission_requests.resolve(
                request.id,
                action=PermissionAction.ALLOW,
                reason=f"Approved by user: {decision.reason}",
            )
            return PermissionDecision(
                PermissionAction.ALLOW,
                f"Approved by user: {decision.reason}",
                decision.rule,
            )
        self._permission_requests.resolve(
            request.id,
            action=PermissionAction.DENY,
            reason=f"Denied by user: {decision.reason}",
        )
        return PermissionDecision(
            PermissionAction.DENY,
            f"Denied by user: {decision.reason}",
            decision.rule,
        )

    # ─── User Instructions ───

    async def _handle_ui_get(self, params: dict) -> Any:
        """Return the current user instructions."""
        from opengis_backend.user_prefs.store import load
        return {"content": load()}

    async def _handle_ui_set(self, params: dict) -> Any:
        """Replace user instructions (called from Settings UI)."""
        from opengis_backend.user_prefs.store import save
        content = params.get("content", "")
        save(str(content))
        return {"status": "ok"}

    # ─── JSON-RPC Helpers ───

    async def _send_result(self, request_id: str, result: Any) -> None:
        if self._closed:
            return
        async with self._ws_write_lock:
            if self._closed:
                return
            await self._send_text_safe(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result,
                    },
                    cls=NumpyEncoder,
                )
            )

    async def _send_error(self, request_id: str | None, code: int, message: str) -> None:
        if self._closed:
            return
        async with self._ws_write_lock:
            if self._closed:
                return
            await self._send_text_safe(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": code, "message": message},
                    },
                    cls=NumpyEncoder,
                )
            )

    async def _send_notification(self, method: str, params: dict) -> None:
        if self._closed:
            return
        async with self._ws_write_lock:
            if self._closed:
                return
            await self._send_text_safe(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": method,
                        "params": params,
                    },
                    cls=NumpyEncoder,
                )
            )

    async def _send_text_safe(self, payload: str) -> bool:
        """Send one websocket payload, treating client disconnect as normal."""
        try:
            await self.ws.send_text(payload)
            return True
        except WebSocketDisconnect:
            self._closed = True
            return False
        except RuntimeError as exc:
            if _looks_like_websocket_closed(exc):
                self._closed = True
                return False
            raise

    async def _request_client(
        self,
        method: str,
        params: dict,
        *,
        timeout: float = 60.0,
    ) -> Any:
        """Send a JSON-RPC request to the frontend and await its response."""
        if self._closed:
            raise RuntimeError("Frontend request channel is closed.")
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_client_requests[request_id] = fut
        try:
            async with self._ws_write_lock:
                sent = await self._send_text_safe(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": method,
                            "params": params,
                        },
                        cls=NumpyEncoder,
                    )
                )
                if not sent:
                    raise RuntimeError("Frontend request channel is closed.")
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_client_requests.pop(request_id, None)
