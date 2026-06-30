"""
GISCodeAgent — the brain of OpenGIS.

This implements the Hybrid CodeAct paradigm:
- The LLM decides at each step whether to reply with text or code.
- Code is executed in a real Python subprocess sandbox.
- Tool calls are *normal Python function calls* — no schema gymnastics.
- Plain text replies (greetings, explanations) don't require code.

v3.1 (2026-04): Replaced smolagents CodeAgent with custom AgentLoop.
No more smolagents dependency. The agent loop is fully self-contained,
using litellm directly for LLM calls.

v3.2 (2026-04): Added WorkflowLoop support. The agent now routes to
either AgentLoop (free-form) or WorkflowLoop (DAG-driven) based on
whether a workflow attachment is present.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from opengis_backend.agent.agent_factory import build_agent_loop
from opengis_backend.agent.agent_loop import AgentStep
from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.events import (
    AgentEvent,
    AgentEventType,
)
from opengis_backend.agent.llm import LLMConfig
from opengis_backend.agent.runner import AgentRunner
from opengis_backend.agent.script_archive import ScriptArchive
from opengis_backend.agent.step_recorder import StepRecorder
from opengis_backend.agent.workflow_factory import build_workflow_loop
from opengis_backend.agent.workflow_loop import WorkflowDocument
from opengis_backend.runs import RunArchive
from opengis_backend.skills.context import (
    SkillContext,
    reset_current_context,
    set_current_context,
)
from opengis_backend.skills.registry import SkillRegistry
from opengis_backend.workspace import WorkspaceManager, WorkspaceManagerError

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# CodeAgent wrapper
# ──────────────────────────────────────────────────────────────────────
class GISCodeAgent:
    """
    Orchestrator for the OpenGIS agent loop:
      1. Builds tools from our SkillRegistry
      2. Installs the per-run SkillContext (for IPC notifications)
      3. Streams events back as AgentEvent
      4. Routes to AgentLoop or WorkflowLoop based on attachments

    Maintains per-conversation context so that multiple messages within
    the same chat session share memory (the LLM sees prior turns).
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        protocol: str = "openai",
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
        max_iterations: int = 10,
    ):
        self.skills = skill_registry
        self.protocol = protocol
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_iterations = max_iterations

        # Exposed so RpcHandler._handle_agent_cancel can interrupt
        # the subprocess across the asyncio boundary.
        self.current_executor: Any = None
        self._workspace_manager = WorkspaceManager()

        # Per-conversation context managers. Key is conversation_id.
        # This allows memory to persist across multiple messages in
        # the same chat session.
        self._conversation_contexts: dict[str, ContextManager] = {}

        # Currently running loop instance — exposed so the cancel handler
        # can signal it to stop at the next safe point.
        self._current_loop: Any = None

        # Currently running AgentRunner — exposed so the cancel handler
        # can interrupt the worker thread blocked on an LLM HTTP call.
        self._current_runner: Any = None

    async def run(
        self,
        user_message: str,
        context: SkillContext | None = None,
        workflow: WorkflowDocument | None = None,
        active_skill_groups: list[str] | None = None,
        user_instructions: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Run the agent on a user message. Yields AgentEvents.

        If ``workflow`` is provided, routes to WorkflowLoop (DAG-driven).
        Otherwise, routes to AgentLoop (free-form Hybrid CodeAct).

        Event flow:
            [for each code step:]
                CODE_BLOCK { step, code, script_path, script_abs_path }
                CODE_RESULT { step, output, error? }
            STREAM_DELTA (final answer)
            STREAM_END
        """
        ctx = context or SkillContext()
        workspace = (ctx.meta or {}).get("workspace_path") if ctx else None
        conversation_id = getattr(ctx, "conversation_id", None)

        # Retrieve or create the per-conversation context manager.
        if conversation_id and conversation_id in self._conversation_contexts:
            shared_context = self._conversation_contexts[conversation_id]
            logger.info(
                "Reusing context for conversation %s (%d messages)",
                conversation_id,
                shared_context.total_messages,
            )
        else:
            shared_context = ContextManager()
            if conversation_id:
                self._conversation_contexts[conversation_id] = shared_context
                logger.info("Created new context for conversation %s", conversation_id)

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
        archive = ScriptArchive.for_run(workspace_path=workspace)
        if ctx.meta is None:
            ctx.meta = {}
        ctx.meta.setdefault("run_id", archive.run_id)
        ctx.meta.setdefault("script_dir", str(archive.script_dir))

        # Wire orchestration deps so the Agent-as-Tool sub-agent skills
        # (run_subagent / run_subagents) can spin up isolated child loops.
        # They read these back from ctx.meta and reuse build_agent_loop().
        ctx.meta.setdefault("_skill_registry", self.skills)
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

        # Build the step recorder.
        recorder = StepRecorder(
            archive=archive,
            loop=loop,
            queue=event_queue,
            user_message=user_message,
        )

        def _step_callback(step: AgentStep) -> None:
            """Step callback that records + mirrors to run archive."""
            recorder.on_step(step)
            try:
                if step.is_text_reply or not step.code:
                    return
                run_archive.record_step(
                    step=step.step_num,
                    code=step.code,
                    output=step.output,
                    error=step.error,
                    script_path=None,
                )
            except Exception:
                logger.exception("run_archive.record_step mirror failed")

        # Wire D3 risky-op telemetry.
        risky_listener = run_archive.record_risky_op

        # Progress callback: forwards thinking/executing status to the UI.
        def _progress_callback(stage: str, detail: str = "") -> None:
            recorder.on_progress(stage, detail)

        # ── Streaming callbacks ───────────────────────────────────
        # These are invoked from the LLM worker thread as tokens arrive,
        # so they hop the asyncio loop via _enqueue. They drive the
        # frontend's "code block expands while writing, collapses when
        # done" affordance.
        from opengis_backend.agent.events import _enqueue

        def _on_thought_delta(text: str) -> None:
            if not text:
                return
            # Stream the LLM's pre-code thinking into a collapsible
            # \"reasoning\" bubble. The current round id is owned by the
            # AgentLoop, but for delta events we don't strictly need it
            # \u2014 the front-end appends to whichever reasoning msg is
            # currently partial. Round-scoped events (start/end/promote)
            # carry the id explicitly.
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.REASONING_DELTA,
                    data={
                        "delta": text,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_reasoning_start(round_id: int) -> None:
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.PROGRESS,
                    data={"stage": "reasoning"},
                ),
            )
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.REASONING_DELTA,
                    data={
                        "delta": "",
                        "round": round_id,
                        "open": True,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_reasoning_end(round_id: int) -> None:
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.REASONING_END,
                    data={
                        "round": round_id,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_reasoning_promote(round_id: int) -> None:
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.REASONING_PROMOTE,
                    data={
                        "round": round_id,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_code_start(step_num: int) -> None:
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.CODE_BLOCK_START,
                    data={
                        "step": step_num,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_code_delta(step_num: int, text: str) -> None:
            if not text:
                return
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.CODE_DELTA,
                    data={
                        "step": step_num,
                        "delta": text,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_code_end(step_num: int) -> None:
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.CODE_BLOCK_END,
                    data={
                        "step": step_num,
                        "run_id": archive.run_id,
                    },
                ),
            )

        # ── Route: WorkflowLoop or AgentLoop ──────────────────────
        if workflow is not None:
            # DAG-driven workflow execution.
            agent_loop, executor = build_workflow_loop(
                skills=self.skills,
                llm_config=LLMConfig(
                    protocol=self.protocol,
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                ),
                ctx=ctx,
                workflow=workflow,
                max_retries_per_node=3,
                step_callback=_step_callback,
                progress_callback=_progress_callback,
                risky_op_listener=risky_listener,
                on_thought_delta=_on_thought_delta,
                on_code_start=_on_code_start,
                on_code_delta=_on_code_delta,
                on_code_end=_on_code_end,
            )
            logger.info(
                "Workflow mode: executing '%s' with %d nodes",
                workflow.name,
                len(workflow.nodes),
            )
        else:
            # Free-form Hybrid CodeAct.
            agent_loop, executor = build_agent_loop(
                skills=self.skills,
                llm_config=LLMConfig(
                    protocol=self.protocol,
                    model=self.model,
                    api_key=self.api_key,
                    base_url=self.base_url,
                        ),
                ctx=ctx,
                max_steps=self.max_iterations,
                step_callback=_step_callback,
                progress_callback=_progress_callback,
                risky_op_listener=risky_listener,
                context=shared_context,
                on_thought_delta=_on_thought_delta,
                on_code_start=_on_code_start,
                on_code_delta=_on_code_delta,
                on_code_end=_on_code_end,
                on_reasoning_start=_on_reasoning_start,
                on_reasoning_end=_on_reasoning_end,
                on_reasoning_promote=_on_reasoning_promote,
                skill_groups=active_skill_groups,
                user_instructions=user_instructions,
            )

        # Expose the executor and loop for cancellation.
        self.current_executor = executor
        self._current_loop = agent_loop

        # Install context for skills that use get_current_context().
        token = set_current_context(ctx)

        # Track final state for the archive.
        final_state: dict[str, Any] = {
            "status": "running",
            "final_answer": None,
            "error": None,
        }

        def _cleanup() -> None:
            reset_current_context(token)
            try:
                executor.cleanup()
            except Exception:
                logger.exception("executor cleanup failed")
            self.current_executor = None
            self._current_loop = None
            self._current_runner = None
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

        runner = AgentRunner(
            max_steps=int(self.max_iterations),
            run_id=archive.run_id,
            # AgentLoop streams its own tokens via _on_thought_delta;
            # WorkflowLoop doesn't, so it still needs the trailing emit.
            emit_final_answer=(workflow is not None),
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
                # Track terminal state for the archive.
                if event.type == AgentEventType.ERROR:
                    final_state["status"] = "error"
                    final_state["error"] = str(event.data) if event.data else None
                elif (
                    event.type == AgentEventType.STREAM_DELTA and final_state["status"] == "running"
                ):
                    if isinstance(event.data, str):
                        final_state["final_answer"] = event.data
                elif event.type == AgentEventType.STREAM_END and final_state["status"] == "running":
                    final_state["status"] = "success"
                yield event
        except asyncio.CancelledError:
            final_state["status"] = "cancelled"
            raise

    def reset(self) -> None:
        """No-op: the agent loop is rebuilt per-run."""
        pass
