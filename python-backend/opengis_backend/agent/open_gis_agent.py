"""
OpenGISAgent — the function-call agent for OpenGIS.

This implements the OpenGIS function-call agent:
- The LLM calls structured tools for actions.
- Python is executed only through the execute_code tool in a subprocess.
- Plain text replies (greetings, explanations, summaries) are never executed.

The agent routes to either AgentLoop (free-form) or WorkflowLoop
(DAG-driven) based on whether a workflow attachment is present.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from opengis_backend.agent.agent_factory import build_agent_loop
from opengis_backend.agent.context.context_store import AgentContextStore
from opengis_backend.agent.telemetry.event_log import AgentEventLog
from opengis_backend.agent.telemetry.events import (
    AgentEvent,
    AgentEventType,
)
from opengis_backend.agent.llm import LLMConfig
from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.loop.runtime_control import TaskMode, infer_task_mode
from opengis_backend.agent.telemetry.run_callbacks import AgentRunCallbacks
from opengis_backend.agent.telemetry.runner import AgentRunner
from opengis_backend.agent.telemetry.script_archive import ScriptArchive
from opengis_backend.agent.session.run_session import create_run_session, finish_run_session
from opengis_backend.agent.session.session_coordinator import SessionBusyError, SessionCoordinator
from opengis_backend.agent.workflow.workflow_factory import build_workflow_loop
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.runs import RunArchive
from opengis_backend.tools.context import (
    ToolContext,
    reset_current_context,
    set_current_context,
)
from opengis_backend.tools.registry import ToolRegistry
from opengis_backend.workspace import WorkspaceManager, WorkspaceManagerError


logger = logging.getLogger(__name__)


def _ensure_required_tool_groups(user_message: str, groups: list[str] | None) -> list[str] | None:
    """Keep task-mode guardrails aligned with the provider-visible tools."""
    required: set[str] = set()
    if infer_task_mode(user_message) is TaskMode.WORKER:
        required.update({"core", "worker"})
    if not required:
        return groups
    if groups is None:
        return None
    merged = list(dict.fromkeys([*groups, *sorted(required)]))
    return merged


# ──────────────────────────────────────────────────────────────────────
# Agent runtime facade
# ──────────────────────────────────────────────────────────────────────
class OpenGISAgent:
    """
    Orchestrator for the OpenGIS agent loop:
      1. Builds tools from our ToolRegistry
      2. Installs the per-run ToolContext (for IPC notifications)
      3. Streams events back as AgentEvent
      4. Routes to AgentLoop or WorkflowLoop based on attachments

    Maintains per-conversation context so that multiple messages within
    the same chat session share memory (the LLM sees prior turns).
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        protocol: str = "openai",
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
    ):
        self.tool_registry = tool_registry
        self.protocol = protocol
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

        # Exposed so RpcHandler._handle_agent_cancel can interrupt
        # the subprocess across the asyncio boundary.
        self.current_executor: Any = None
        self._workspace_manager = WorkspaceManager()

        self._context_store = AgentContextStore()

        # Currently running loop instance — exposed so the cancel handler
        # can signal it to stop at the next safe point.
        self._current_loop: Any = None

        # Currently running AgentRunner — exposed so the cancel handler
        # can interrupt the worker thread blocked on an LLM HTTP call.
        self._current_runner: Any = None

    async def run(
        self,
        user_message: str,
        context: ToolContext | None = None,
        workflow: WorkflowDocument | None = None,
        active_tool_groups: list[str] | None = None,
        user_instructions: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Run the agent on a user message. Yields AgentEvents.

        If ``workflow`` is provided, routes to WorkflowLoop (DAG-driven).
        Otherwise, routes to AgentLoop (free-form function-call chat).

        Event flow:
            [for each execute_code step:]
                CODE_BLOCK { step, code, script_path, script_abs_path }
                CODE_RESULT { step, output, error? }
            STREAM_DELTA (final answer)
            STREAM_END
        """
        ctx = context or ToolContext()
        workspace = (ctx.meta or {}).get("workspace_path") if ctx else None
        conversation_id = getattr(ctx, "conversation_id", None)

        shared_context = self._context_store.get(conversation_id, workspace)

        # Ensure the workspace is a git repo with our .gitignore.
        ws_ready = False
        if workspace:
            try:
                self._workspace_manager.ensure_initialized(workspace)
                ws_ready = True
            except WorkspaceManagerError as e:
                logger.warning(
                    "workspace init failed, snapshots disabled for this run: %s",
                    e,
                )

        # Persist scripts + per-run logs.
        archive = ScriptArchive.for_run(
            workspace_path=workspace,
            workflow_name=workflow.name if workflow is not None else None,
        )
        if ctx.meta is None:
            ctx.meta = {}
        ctx.meta["_current_user_message"] = user_message
        agent_profile = ctx.meta.get("agent_profile")
        if not isinstance(agent_profile, AgentProfile):
            agent_profile = (
                AgentProfile.workflow_runner()
                if workflow is not None
                else AgentProfile.gis_build()
            )
        effective_tool_groups = _ensure_required_tool_groups(
            user_message,
            active_tool_groups if active_tool_groups is not None else agent_profile.tool_groups,
        )
        ctx.meta.setdefault("run_id", archive.run_id)
        ctx.meta.setdefault("script_dir", str(archive.script_dir))
        session = create_run_session(
            ctx=ctx,
            workspace=workspace,
            conversation_id=conversation_id,
            workflow=workflow,
            agent_profile=agent_profile,
            run_id=archive.run_id,
            title=user_message,
        )

        # Wire orchestration deps so the Agent-as-Tool sub-agent tools
        # (run_subagent / run_subagents) can spin up isolated child loops.
        # They read these back from ctx.meta and reuse build_agent_loop().
        ctx.meta.setdefault("_agent_ref", self)  # for subagent cancel propagation
        ctx.meta.setdefault("_tool_registry", self.tool_registry)
        ctx.meta.setdefault(
            "_llm_config",
            LLMConfig(
                protocol=self.protocol,
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
            ),
        )

        # Open the run archive.
        run_archive = RunArchive.open(
            run_id=archive.run_id,
            prompt=user_message,
            workspace_path=workspace,
            model=self.model,
            scripts_dir=archive.script_dir,
        )
        event_log = AgentEventLog(run_archive, run_id=archive.run_id)
        ctx.meta.setdefault("_event_log", event_log)
        lease_key = conversation_id or session.id

        def _record_event(event: AgentEvent) -> None:
            try:
                event_log.append(event)
            except Exception:
                logger.debug("event log recording failed (non-fatal)", exc_info=True)

        # Pre-run snapshot.
        if ws_ready:
            try:
                pre_sha = self._workspace_manager.snapshot(
                    workspace, run_id=archive.run_id, label="pre"
                )
                run_archive.set_pre_sha(pre_sha)
                ctx.meta["pre_sha"] = pre_sha
            except WorkspaceManagerError as e:
                logger.warning("pre-run snapshot failed: %s", e)

        loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        callbacks = AgentRunCallbacks(
            ctx=ctx,
            archive=archive,
            run_archive=run_archive,
            loop=loop,
            event_queue=event_queue,
            user_message=user_message,
            session_id=session.id,
            workspace=workspace,
            workflow=workflow,
        )

        # ── Route: WorkflowLoop or AgentLoop ──────────────────────
        if workflow is not None:
            # DAG-driven workflow execution.
            agent_loop, executor = build_workflow_loop(
                tools=self.tool_registry,
                llm_config=LLMConfig(
                    protocol=self.protocol,
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                ),
                ctx=ctx,
                workflow=workflow,
                max_retries_per_node=3,
                step_callback=callbacks.step_callback,
                progress_callback=callbacks.progress_callback,
                execution_output_callback=callbacks.execution_output_callback,
                risky_op_listener=callbacks.risky_listener,
                on_thought_delta=callbacks.on_thought_delta,
                on_code_start=callbacks.on_code_start,
                on_code_delta=callbacks.on_code_delta,
                on_code_end=callbacks.on_code_end,
                on_tool_start=callbacks.on_tool_start,
                on_tool_result=callbacks.on_tool_result,
                on_provider_result=callbacks.on_provider_result,
                plan_callback=callbacks.plan_callback,
                context=shared_context,
                agent_profile=agent_profile,
            )
            logger.info(
                "Workflow mode: executing '%s' with %d nodes",
                workflow.name,
                len(workflow.nodes),
            )
        else:
            # Free-form function-call chat.
            agent_loop, executor = build_agent_loop(
                tools=self.tool_registry,
                llm_config=LLMConfig(
                    protocol=self.protocol,
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                        ),
                ctx=ctx,
                step_callback=callbacks.step_callback,
                progress_callback=callbacks.progress_callback,
                execution_output_callback=callbacks.execution_output_callback,
                risky_op_listener=callbacks.risky_listener,
                context=shared_context,
                on_thought_delta=callbacks.on_thought_delta,
                on_code_start=callbacks.on_code_start,
                on_code_delta=callbacks.on_code_delta,
                on_code_end=callbacks.on_code_end,
                on_tool_start=callbacks.on_tool_start,
                on_tool_result=callbacks.on_tool_result,
                on_provider_result=callbacks.on_provider_result,
                tool_groups=effective_tool_groups,
                user_instructions=user_instructions,
                agent_profile=agent_profile,
            )

        # Expose the executor and loop for cancellation.
        self.current_executor = executor
        self._current_loop = agent_loop

        try:
            SessionCoordinator.acquire(lease_key, archive.run_id)
        except SessionBusyError as e:
            err = str(e)
            run_archive.record_event(AgentEventType.ERROR.value, {"error": err, "run_id": archive.run_id})
            run_archive.close(status="error", error=err)
            try:
                executor.cleanup()
            except Exception:
                logger.debug("executor cleanup after session busy failed", exc_info=True)
            self.current_executor = None
            self._current_loop = None
            yield AgentEvent(type=AgentEventType.ERROR, data=err)
            yield AgentEvent(type=AgentEventType.STREAM_END)
            return

        # Install context for tools that use get_current_context().
        token = set_current_context(ctx)

        # Track final state for the archive.
        final_state: dict[str, Any] = {
            "status": "running",
            "final_answer": None,
            "error": None,
        }

        def _cleanup() -> None:
            try:
                reset_current_context(token)
            except ValueError:
                # Cancellation / websocket disconnect can close the async
                # generator from a different context than the one that
                # installed the token. Cleanup must still clear live runner
                # refs so later runs do not inherit a stale plan/loop.
                logger.debug("tool context reset skipped: token belongs to another context")
            except Exception:
                logger.debug("tool context reset failed", exc_info=True)
            finally:
                try:
                    executor.cleanup()
                except Exception:
                    logger.exception("executor cleanup failed")
                self.current_executor = None
                self._current_loop = None
                self._current_runner = None
            self._context_store.persist(workspace, conversation_id, shared_context)
            self._context_store.extract_knowledge_after_run(
                workspace=workspace,
                user_message=user_message,
                final_answer=final_state.get("final_answer"),
                run_archive=run_archive,
                workflow=workflow,
            )
            finish_run_session(
                ctx=ctx,
                workspace=workspace,
                session=session,
                run_archive=run_archive,
                status=final_state["status"],
                final_answer=final_state.get("final_answer"),
                error=final_state.get("error"),
            )
            # Post-run snapshot.
            if ws_ready:
                try:
                    post_sha = self._workspace_manager.snapshot(
                        workspace, run_id=archive.run_id, label="post"
                    )
                    run_archive.set_post_sha(post_sha)
                except WorkspaceManagerError as e:
                    logger.warning("post-run snapshot failed: %s", e)
            # Close the archive.
            try:
                run_archive.close(
                    status=final_state["status"],
                    final_answer=final_state["final_answer"],
                    error=final_state["error"],
                )
            except Exception:
                logger.exception("run_archive.close failed")
            try:
                SessionCoordinator.release(lease_key, archive.run_id)
            except Exception:
                logger.debug("session lease release failed", exc_info=True)

        runner = AgentRunner(
            run_id=archive.run_id,
            # AgentLoop streams its own tokens via callbacks.on_thought_delta;
            # WorkflowLoop doesn't, so it still needs the trailing emit.
            emit_final_answer=(workflow is not None),
            on_final_answer=lambda answer: final_state.update({"final_answer": answer}),
        )
        self._current_runner = runner

        # Drive the run.
        try:
            async for event in runner.drive(
                agent_loop,
                user_message,
                queue=event_queue,
                on_cleanup=_cleanup,
            ):
                _record_event(event)
                # Track terminal state for the archive.
                if event.type == AgentEventType.ERROR:
                    final_state["status"] = "error"
                    final_state["error"] = str(event.data) if event.data else None
                elif (
                    event.type == AgentEventType.STREAM_DELTA and final_state["status"] == "running"
                ):
                    if isinstance(event.data, str):
                        final_state["final_answer"] = event.data
                    elif isinstance(event.data, dict) and isinstance(event.data.get("content"), str):
                        final_state["final_answer"] = event.data.get("content")
                elif event.type == AgentEventType.STREAM_END and final_state["status"] == "running":
                    final_state["status"] = "success"
                yield event
        except asyncio.CancelledError:
            final_state["status"] = "cancelled"
            raise

    def reset(self) -> None:
        """No-op: the agent loop is rebuilt per-run."""
        pass
