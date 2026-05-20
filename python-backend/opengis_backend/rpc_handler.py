"""JSON-RPC 2.0 protocol handler for WebSocket communication."""

import asyncio
import json
import logging
import traceback
from typing import Any

import numpy as np
from fastapi import WebSocket

from opengis_backend.agent.engine import GISAgent
from opengis_backend.agent.events import AgentEvent, EventTranslator
from opengis_backend.agent.workflow_loop import WorkflowDocument
from opengis_backend.config import settings
from opengis_backend.constants import (  # noqa: E402 module-level import
    CLEANUP_FUTURE_TIMEOUT,
    DEFAULT_EXEC_TIMEOUT,
    MAX_EXEC_TIMEOUT,
    MIN_EXEC_TIMEOUT,
    TITLE_GEN_TIMEOUT,
)
from opengis_backend.runs import RunArchive
from opengis_backend.sandbox.script_runner import ScriptRunner
from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import SkillRegistry
from opengis_backend.workspace import WorkspaceManager, WorkspaceManagerError

logger = logging.getLogger(__name__)


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

    Renamed from ``JsonRpcHandler`` in Stage 3.1 to free up the "protocol"
    namespace for :mod:`opengis_backend.protocol_types` (Pydantic models).
    """

    def __init__(
        self, websocket: WebSocket, skill_registry: SkillRegistry
    ):
        self.ws = websocket
        self.skills = skill_registry
        # Reusable agent instance — built lazily once we know the LLM config.
        self._agent: GISAgent | None = None
        self._current_agent_task: asyncio.Task | None = None
        # D2: per-workspace serial lock. Key is the normalised workspace
        # path (or "<no-workspace>" for orphan runs). We reject rather
        # than queue a second Send on the same workspace to keep the
        # state model simple — mirrors the decision in MEMORY ADR-009.
        self._workspace_locks: dict[str, str] = {}  # workspace -> owner run_id
        self._titled_conversations: set[str] = set()  # conversation_ids that already have a title
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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._method_handlers = {
            # Canonical v3.0 three-channel method names (Stage 3.6 — sole wire).
            # rpc.* channel: synchronous command / query
            "rpc.fs.load_file": self._handle_load_file,
            "rpc.fs.get_file_info": self._handle_get_file_info,
            "rpc.skill.list": self._handle_skill_list,
            "rpc.skill.execute": self._handle_skill_execute,
            "rpc.code.run_script": self._handle_run_script,
            "rpc.code.cancel_script": self._handle_cancel_script,
            "rpc.agent.interrupt": self._handle_agent_cancel,
            "rpc.agent.set_llm_config": self._handle_agent_configure,
            "rpc.agent.test_connection": self._handle_agent_test_connection,
            # user_instructions — global personalization prompt
            "user_instructions.get": self._handle_ui_get,
            "user_instructions.set": self._handle_ui_set,
            # A4 + C3 — workspace / run inspection & control
            "rpc.workspace.revert_run": self._handle_workspace_revert_run,
            "rpc.runs.list": self._handle_runs_list,
            "rpc.runs.get": self._handle_runs_get,
            "rpc.runs.replay": self._handle_runs_replay,
            # chat.* channel: long-running conversation turn (streams via notifications)
            "chat.user_message": self._handle_agent_chat,
        }

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

    async def _handle_skill_list(self, params: dict) -> Any:
        return {"skills": [s.to_dict() for s in self.skills.list_all()]}

    async def _handle_skill_execute(self, params: dict) -> Any:
        name = params.get("name")
        args = params.get("args", {})
        if not name:
            raise ValueError("Missing required parameter: name")

        skill_timeout = 50.0
        # Skills triggered directly (not via agent) still get a SkillContext
        # so context-aware display skills work end-to-end.
        ctx = SkillContext(notify_fn=self._safe_notify)
        try:
            result = await asyncio.wait_for(
                self.skills.execute(name, args, context=ctx),
                timeout=skill_timeout,
            )
            return result
        except TimeoutError:
            return {
                "success": False,
                "error": (
                    f"Skill '{name}' timed out after {skill_timeout}s. "
                    "The operation may be too complex for the skill."
                ),
            }

    # ─── Script Runner (user-authored scripts in the agent's sandbox) ───

    def _ensure_script_runner(self) -> ScriptRunner:
        if self._script_runner is None:
            self._script_runner = ScriptRunner(
                skill_registry=self.skills,
                notify_fn=self._safe_notify,
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

        Params: { protocol, model, api_key, base_url, max_iterations? }
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
            str(params.get("max_iterations", "")),
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
        if "max_iterations" in params:
            settings.agent_max_iterations = int(params["max_iterations"])

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
            if reply:
                return {"ok": True}
            else:
                return {"ok": False, "error": "Empty response from LLM"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _handle_agent_chat(self, params: dict) -> Any:
        """Start an agent conversation. Streams via JSON-RPC notifications."""
        message = params.get("message")
        if not message:
            raise ValueError("Missing required parameter: message")

        # Optional user instructions — global personalization prompt
        user_instructions = params.get("user_instructions") or None

        # Optional workspace root — used by ScriptArchive to decide where
        # to persist step scripts. None means "no workspace open → fall
        # back to appdata/agent-runs/<run_id>/".
        workspace_path = params.get("workspace_path") or None

        # Process attachments: detect workflow vs regular files vs skills.
        attachments = params.get("attachments") or []
        workflow_doc: WorkflowDocument | None = None
        regular_attachments: list[dict] = []
        skill_groups: list[str] | None = None

        for att in attachments:
            if att.get("type") == "workflow":
                # Parse the workflow file.
                try:
                    workflow_doc = self._parse_workflow_attachment(att)
                except Exception as e:
                    logger.warning("Failed to parse workflow attachment: %s", e)
                    # Fall back to treating it as a regular file.
                    regular_attachments.append(att)
            elif att.get("type") == "skill":
                # Extract skill groups to activate.
                groups = att.get("skill_groups") or []
                if groups:
                    # Merge: keep "core" always available.
                    skill_groups = list(set(groups + ["core"]))
            else:
                regular_attachments.append(att)

        if regular_attachments:
            message = self._inject_attachments(message, regular_attachments)

        # D2: serial lock per workspace. We reject rather than queue,
        # keeping the state model obvious to the UI. Frontend should
        # disable the Send button while another run is live, but if a
        # stale tab slips a second request through we bounce it here.
        lock_key = _normalise_workspace(workspace_path)
        if lock_key in self._workspace_locks:
            # If the previous task is actually done (cancel propagated),
            # release the stale lock and proceed.
            if self._current_agent_task is None or self._current_agent_task.done():
                logger.info("[chat] stale lock on %s (task done), releasing.", lock_key)
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

        agent = self._ensure_agent()
        ctx = SkillContext(
            notify_fn=self._safe_notify,
            conversation_id=params.get("conversation_id"),
            meta={"workspace_path": workspace_path} if workspace_path else {},
        )

        # We don't know the run_id until ScriptArchive.for_run generates
        # one inside agent.run(); stash a placeholder and overwrite as
        # soon as ctx.meta['run_id'] becomes available.
        self._workspace_locks[lock_key] = "pending"

        async def _drive():
            try:
                async for event in agent.run(message, context=ctx, workflow=workflow_doc, active_skill_groups=skill_groups, user_instructions=user_instructions):
                    # Promote the lock owner now that we know the run_id.
                    if self._workspace_locks.get(lock_key) == "pending":
                        rid = (ctx.meta or {}).get("run_id")
                        if rid:
                            self._workspace_locks[lock_key] = rid
                    await self._emit_agent_event(event)
                # After successful run, generate a conversation title
                # in the background (non-blocking) to avoid delaying
                # the stream_end event.
                asyncio.create_task(
                    self._generate_title_if_needed(message, params.get("conversation_id"))
                )
            except asyncio.CancelledError:
                await self._send_notification("chat.cancelled", {})
                raise
            except Exception as e:
                traceback.print_exc()
                await self._send_notification("chat.error", {"error": self._humanize_error(str(e))})

        # Track the task so agent.cancel can interrupt.
        self._current_agent_task = asyncio.create_task(_drive())
        try:
            await self._current_agent_task
        finally:
            self._current_agent_task = None
            # D2: always release the lock, even on error/cancel.
            self._workspace_locks.pop(lock_key, None)

        return {"status": "completed", "run_id": (ctx.meta or {}).get("run_id")}

    async def _handle_agent_cancel(self, params: dict) -> Any:
        """Interrupt the running agent — kill the subprocess and release locks.

        Order matters:
          1. Ask the subprocess executor to die (``interrupt()``) so any
             ``pip install`` / training loop stops burning CPU. On
             Windows this escalates to ``taskkill /F /T /PID`` if the
             child ignores CTRL_BREAK.
          2. Cancel the asyncio task driving the event loop, which lets
             the ``finally`` in ``_drive()`` release the workspace lock.
          3. Force-release all workspace locks as a safety net — the
             ``finally`` block in ``_drive()`` should handle this, but
             if the task is stuck we need a fallback.
          4. Wait briefly for the task to actually finish so the next
             message doesn't race with the dying task.
        """
        cancelled = False
        agent = self._agent
        if agent is not None:
            # Signal the agent loop to stop at the next iteration.
            current_loop = getattr(agent, "_current_loop", None)
            if current_loop is not None and hasattr(current_loop, "interrupt"):
                try:
                    current_loop.interrupt()
                    logger.info("[agent_cancel] agent loop interrupted")
                except Exception as e:
                    logger.warning("[agent_cancel] loop.interrupt failed: %s", e)

            executor = getattr(agent, "current_executor", None)
            if executor is not None:
                try:
                    executor.interrupt()
                    logger.info("[agent_cancel] executor.interrupt() sent")
                except Exception as e:
                    logger.warning("[agent_cancel] executor.interrupt failed: %s", e)
                # Also force cleanup the executor to kill the subprocess tree.
                try:
                    executor.cleanup()
                    logger.info("[agent_cancel] executor.cleanup() done")
                except Exception as e:
                    logger.warning("[agent_cancel] executor.cleanup failed: %s", e)
                # Clear the reference so the next run creates a fresh executor.
                agent.current_executor = None

            # Interrupt the worker thread blocked on an LLM HTTP call.
            # This is the last-resort mechanism — the normal path (loop.interrupt
            # + executor.interrupt + task.cancel) handles most cases, but when
            # the thread is stuck on a long-running HTTP request we need to
            # inject an exception to unblock it.
            current_runner = getattr(agent, "_current_runner", None)
            if current_runner is not None:
                try:
                    current_runner.interrupt_worker_thread()
                except Exception as e:
                    logger.warning("[agent_cancel] thread interrupt failed: %s", e)

        if self._current_agent_task and not self._current_agent_task.done():
            self._current_agent_task.cancel()
            cancelled = True
            logger.info("[agent_cancel] asyncio task cancelled")
            # Wait briefly for the task to actually finish so the next
            # user message doesn't race with the dying task.
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._current_agent_task),
                    timeout=TITLE_GEN_TIMEOUT,
                )
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
            self._current_agent_task = None

        # Safety net: force-release all workspace locks. The _drive()
        # finally block should do this, but if the task is stuck or the
        # cancel doesn't propagate fast enough, we release here.
        if self._workspace_locks:
            released = list(self._workspace_locks.keys())
            self._workspace_locks.clear()
            logger.info("[agent_cancel] force-released locks: %s", released)

        return {"status": "cancelled" if cancelled else "idle"}

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
        return {"status": "ok", "meta": ra.meta, "steps": ra.read_steps()}

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
                skill_registry=self.skills,
                protocol=settings.llm_protocol,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                max_iterations=settings.agent_max_iterations,
            )
        return self._agent

    async def _generate_title_if_needed(
        self, user_message: str, conversation_id: str | None
    ) -> None:
        """Generate a short conversation title using the LLM after the first message."""
        if not conversation_id:
            logger.debug("Title generation skipped: no conversation_id")
            return
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
            title = await asyncio.to_thread(caller, title_messages)
            title = title.strip().strip("\"'").strip()
            logger.info("Generated title: %r (len=%d)", title, len(title))
            if title and len(title) <= 60:
                await self._send_notification(
                    "chat.title_generated",
                    {
                        "conversation_id": conversation_id,
                        "title": title,
                    },
                )
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
                entry = f"\n\n---\n📎 **Attached {label}: `{name}`**\n```\n{content}\n```"
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
        Translate AgentEvent to JSON-RPC notification under the canonical
        ``chat.*`` names (Stage 3.6 — sole wire). The mapping itself lives
        in ``opengis_backend.agent.events.EventTranslator`` and is
        contract-tested; this method is now pure glue.
        """
        method, params = EventTranslator.translate(event)
        await self._send_notification(method, params)

    async def _safe_notify(self, method: str, params: dict) -> None:
        """
        Notify callback that the SkillContext hands to skills.

        Sandbox-side skill code may run in a worker thread, so we route
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
        await self.ws.send_text(
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
        await self.ws.send_text(
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
        await self.ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                },
                cls=NumpyEncoder,
            )
        )
