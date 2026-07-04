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
from pathlib import Path
from typing import Any

from opengis_backend.agent.agent_factory import build_agent_loop
from opengis_backend.agent.agent_loop import AgentStep
from opengis_backend.agent.artifacts import ArtifactIndex, artifacts_from_tool_result
from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.context_persistence import save_context, load_context
from opengis_backend.workspace.memory import append as memory_append
from opengis_backend.agent.events import (
    AgentEvent,
    AgentEventType,
)
from opengis_backend.agent.llm import LLMConfig
from opengis_backend.agent.profile import AgentProfile
from opengis_backend.agent.runner import AgentRunner
from opengis_backend.agent.script_archive import ScriptArchive
from opengis_backend.agent.session import AgentSession, SessionKind, SessionStatus, SessionStore
from opengis_backend.agent.step_recorder import StepRecorder
from opengis_backend.agent.workflow_factory import build_workflow_loop
from opengis_backend.agent.workflow_loop import WorkflowDocument
from opengis_backend.runs import RunArchive
from opengis_backend.skills.context import (
    SkillContext,
    reset_current_context,
    run_async_from_sync,
    set_current_context,
)
from opengis_backend.skills.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.skills.registry import SkillRegistry
from opengis_backend.workspace import WorkspaceManager, WorkspaceManagerError


def _extract_memory(workspace: str, user_message: str, final_answer: str) -> None:
    """Extract key facts from a completed run and save to project memory.

    Heuristic extraction — no extra LLM call. Extracts:
    - Task summary (what was asked)
    - File paths mentioned in the answer
    - Key numbers/statistics
    - Brief result summary
    """
    import re

    task_summary = (user_message or "").strip()[:200]
    answer = (final_answer or "").strip()

    if not task_summary:
        return

    # Extract file paths from the answer
    path_pattern = re.compile(
        r"(/[\w./\-]+\.(?:geojson|shp|gpkg|csv|json|tif|tiff|png|jpg|pdf|md))"
    )
    paths = list(set(path_pattern.findall(answer)))[:5]

    # Extract key numbers (feature counts, statistics, percentages)
    number_pattern = re.compile(r"(\d[\d,]+(?:\.\d+)?)\s*(?:条|个|家|features?|rows?|%)")
    numbers = number_pattern.findall(answer)[:3]

    visual_style_pattern = re.compile(
        r"(颜色|色彩|分类设色|按类别|类别分|分一下颜色|样式|渲染|符号|图层|color|colour|style|symbol|renderer)",
        re.IGNORECASE,
    )
    durable_task_pattern = re.compile(
        r"(报告|学术|论文|分析|统计|workflow|工作流|数据|文件|csv|geojson|shp|gpkg|report|analysis)",
        re.IGNORECASE,
    )
    if (
        len(task_summary) <= 80
        and visual_style_pattern.search(task_summary)
        and not durable_task_pattern.search(task_summary)
        and not paths
        and not numbers
    ):
        logger.debug("Skipping project memory for transient visual styling request")
        return

    # Build structured entry
    entry = f"任务: {task_summary}"
    if paths:
        entry += f"\n  产出: {', '.join(paths)}"
    if numbers:
        entry += f"\n  数据: {', '.join(numbers)}"
    # Brief result (first meaningful sentence)
    for sentence in answer.split("。"):
        s = sentence.strip()
        if len(s) > 15 and not s.startswith("#"):
            entry += f"\n  摘要: {s[:150]}"
            break

    memory_append(workspace, "Run History", entry)

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
            # Try to restore context from disk (survives app restart).
            restored = False
            logger.info(
                "Context lookup: conversation_id=%s, workspace=%s",
                conversation_id, workspace,
            )
            if conversation_id and workspace:
                shared_context = load_context(workspace, conversation_id)
                if shared_context is not None:
                    restored = True
                    logger.info(
                        "Restored context from disk for conversation %s (%d messages)",
                        conversation_id, shared_context.total_messages,
                    )
            if not restored:
                shared_context = ContextManager()
                logger.info("Created new context for conversation %s", conversation_id)
            if conversation_id:
                self._conversation_contexts[conversation_id] = shared_context

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
                AgentProfile.workflow_runner(max_steps=self.max_iterations)
                if workflow is not None
                else AgentProfile.gis_build(max_steps=self.max_iterations)
            )
        effective_max_steps = int(agent_profile.max_steps or self.max_iterations)
        ctx.meta.setdefault("run_id", archive.run_id)
        ctx.meta.setdefault("script_dir", str(archive.script_dir))
        session = AgentSession.create(
            kind=SessionKind.WORKFLOW if workflow is not None else SessionKind.CHAT,
            profile_name=agent_profile.name,
            run_id=archive.run_id,
            title=user_message[:80],
            metadata={
                "conversation_id": conversation_id,
                "workspace_path": workspace,
                "inbox_id": (ctx.meta or {}).get("_inbox_id"),
            },
        )
        ctx.meta.setdefault("_agent_session", session)
        if workspace:
            try:
                SessionStore(workspace).upsert(session)
            except Exception:
                logger.debug("initial session persistence failed (non-fatal)", exc_info=True)

        # Wire orchestration deps so the Agent-as-Tool sub-agent skills
        # (run_subagent / run_subagents) can spin up isolated child loops.
        # They read these back from ctx.meta and reuse build_agent_loop().
        ctx.meta.setdefault("_agent_ref", self)  # for subagent cancel propagation
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
        pending_asset_refresh_paths: set[str] = set()

        def _flush_asset_refreshes(reason: str) -> None:
            if not pending_asset_refresh_paths:
                return
            paths = list(pending_asset_refresh_paths)
            pending_asset_refresh_paths.clear()
            notify_asset_refresh(ctx, paths[0], reason=reason)

        def _step_callback(step: AgentStep) -> None:
            """Step callback that records + mirrors to run archive."""
            try:
                recorder.on_step(step)
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
            finally:
                if not step.is_text_reply and step.code:
                    _flush_asset_refreshes("execute_code")

        # Wire D3 risky-op telemetry. The subprocess reports writes from
        # open(..., "w"), Path.write_text, os.unlink, shutil.rmtree, etc.
        # Record them for audit and refresh the frontend asset tree.
        def risky_listener(entry: dict) -> None:
            run_archive.record_risky_op(entry)
            path_raw = entry.get("path")
            if not path_raw:
                return
            try:
                changed = Path(str(path_raw)).expanduser()
                if not changed.is_absolute() and workspace:
                    changed = Path(workspace).expanduser().resolve() / changed
                pending_asset_refresh_paths.add(str(changed))
            except Exception:
                logger.debug("execute_code asset refresh failed", exc_info=True)

        # Progress callback: forwards thinking/executing status to the UI.
        def _progress_callback(stage: str, detail: str = "") -> None:
            recorder.on_progress(stage, detail)

        current_output_tool: dict[str, str | None] = {
            "call_id": None,
            "name": None,
        }

        def _execution_output_callback(text: str) -> None:
            if not text:
                return
            call_id = current_output_tool.get("call_id")
            name = current_output_tool.get("name")
            if not call_id or name != "execute_code":
                return
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.TOOL_OUTPUT_DELTA,
                    data={
                        "call_id": call_id,
                        "name": name,
                        "delta": text,
                        "run_id": archive.run_id,
                    },
                ),
            )

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

        def _on_tool_start(name: str, args: dict, call_id: str) -> None:
            if name == "execute_code":
                current_output_tool["call_id"] = call_id
                current_output_tool["name"] = name
            if ctx.meta is not None:
                pending = ctx.meta.setdefault("_tool_call_args", {})
                pending[call_id] = args
            try:
                run_archive.record_tool_call(
                    call_id=call_id,
                    name=name,
                    arguments=args,
                    status="running",
                    metadata={"session_id": session.id},
                )
            except Exception:
                logger.debug("tool call start archive failed (non-fatal)", exc_info=True)
            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.TOOL_START,
                    data={
                        "name": name,
                        "args": args,
                        "call_id": call_id,
                        "run_id": archive.run_id,
                    },
                ),
            )

        def _on_tool_result(
            name: str,
            content: str,
            error: str | None,
            duration_ms: float,
            call_id: str,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            args = {}
            if ctx.meta is not None:
                pending = ctx.meta.get("_tool_call_args")
                if isinstance(pending, dict):
                    args = pending.pop(call_id, {}) or {}

            result_metadata = dict(metadata or {})
            if current_output_tool.get("call_id") == call_id:
                current_output_tool["call_id"] = None
                current_output_tool["name"] = None
            if name == "execute_code":
                code = args.get("code")
                if isinstance(code, str) and code.strip():
                    persist_requested = bool(args.get("persist"))
                    should_persist = workflow is not None or persist_requested
                    try:
                        step_no = int(result_metadata.get("code_step") or 0)
                    except Exception:
                        step_no = 0
                    if step_no <= 0:
                        step_no = archive.next_step_num()
                    result_metadata["code_step"] = step_no
                    result_metadata["persisted"] = should_persist
                    if should_persist:
                        semantic_name = str(args.get("script_name") or "")
                        description = str(args.get("description") or "")
                        try:
                            abs_path = archive.write_step(
                                step=step_no,
                                code=code,
                                user_message=user_message,
                                observations=content,
                                error=error,
                                semantic_name=semantic_name,
                                metadata={
                                    "description": description,
                                    "persist_requested": persist_requested,
                                    "workflow": workflow.to_dict() if workflow is not None else None,
                                    "loop_kind": "workflow" if workflow is not None else "chat",
                                    "tool_call_id": call_id,
                                },
                            )
                            rel_path = archive.to_relative(abs_path)
                            result_metadata.update({
                                "script_path": rel_path,
                                "script_abs_path": str(abs_path),
                            })
                            notify_asset_refresh(ctx, abs_path, reason="persist_script")
                            run_archive.record_step(
                                step=step_no,
                                code=code,
                                output=content,
                                error=error,
                                script_path=rel_path,
                            )
                        except Exception:
                            logger.debug("execute_code script persistence failed (non-fatal)", exc_info=True)

            _enqueue(
                loop,
                event_queue,
                AgentEvent(
                    type=AgentEventType.TOOL_RESULT,
                    data={
                        "name": name,
                        "output": content,
                        "error": error,
                        "duration_ms": duration_ms,
                        "call_id": call_id,
                        "run_id": archive.run_id,
                        "metadata": result_metadata,
                    },
                ),
            )
            try:
                permission_meta = {}
                # ToolRuntime permission metadata is encoded in future result
                # payloads; keep this record schema stable for the inspector.
                archive_metadata = {
                    "session_id": session.id,
                    **result_metadata,
                    **permission_meta,
                }
                run_archive.record_tool_call(
                    call_id=call_id,
                    name=name,
                    arguments=args,
                    output=content,
                    error=error,
                    duration_ms=duration_ms,
                    metadata=archive_metadata,
                    status="error" if error else "completed",
                )
                artifact_meta = {
                    "run_id": archive.run_id,
                    "session_id": session.id,
                    "tool_name": name,
                    "call_id": call_id,
                    **(metadata or {}),
                    **result_metadata,
                }
                artifacts = artifacts_from_tool_result(name, content, artifact_meta)
                if artifacts:
                    index = ArtifactIndex(workspace)
                    for artifact in artifacts:
                        payload = artifact.to_dict()
                        run_archive.record_artifact(payload)
                        index.append(artifact)
            except Exception:
                logger.debug("tool call archive recording failed (non-fatal)", exc_info=True)

        # ── Route: WorkflowLoop or AgentLoop ──────────────────────
        if workflow is not None:
            # DAG-driven workflow execution.
            # Plan callback: emits plan_update events for compact workflow UI.
            # The `workflow: true` flag tells the frontend to suppress
            # detailed events (code blocks, tool calls) and show only the plan.
            def _plan_callback(plan_data: dict) -> None:
                plan_data["workflow"] = True
                plan_data.setdefault("run_id", archive.run_id)
                try:
                    run_async_from_sync(ctx.notify("rpc.ui.chat.plan_update", plan_data))
                except Exception:
                    logger.debug("plan_callback notify failed", exc_info=True)

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
                execution_output_callback=_execution_output_callback,
                risky_op_listener=risky_listener,
                on_thought_delta=_on_thought_delta,
                on_code_start=_on_code_start,
                on_code_delta=_on_code_delta,
                on_code_end=_on_code_end,
                on_tool_start=_on_tool_start,
                on_tool_result=_on_tool_result,
                plan_callback=_plan_callback,
                context=shared_context,
                agent_profile=agent_profile,
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
                max_steps=effective_max_steps,
                step_callback=_step_callback,
                progress_callback=_progress_callback,
                execution_output_callback=_execution_output_callback,
                risky_op_listener=risky_listener,
                context=shared_context,
                on_thought_delta=_on_thought_delta,
                on_code_start=_on_code_start,
                on_code_delta=_on_code_delta,
                on_code_end=_on_code_end,
                on_reasoning_start=_on_reasoning_start,
                on_reasoning_end=_on_reasoning_end,
                on_reasoning_promote=_on_reasoning_promote,
                on_tool_start=_on_tool_start,
                on_tool_result=_on_tool_result,
                skill_groups=active_skill_groups,
                user_instructions=user_instructions,
                agent_profile=agent_profile,
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
            try:
                reset_current_context(token)
            except ValueError:
                # Cancellation / websocket disconnect can close the async
                # generator from a different context than the one that
                # installed the token. Cleanup must still clear live runner
                # refs so later runs do not inherit a stale plan/loop.
                logger.debug("skill context reset skipped: token belongs to another context")
            except Exception:
                logger.debug("skill context reset failed", exc_info=True)
            finally:
                try:
                    executor.cleanup()
                except Exception:
                    logger.exception("executor cleanup failed")
                self.current_executor = None
                self._current_loop = None
                self._current_runner = None
            # Persist conversation context to disk so it survives restarts.
            if conversation_id and workspace:
                try:
                    save_context(workspace, conversation_id, shared_context)
                except Exception:
                    logger.exception("context persistence failed")
            # Auto-extract key facts into project memory for normal chat only.
            # Workflow runs are already preserved in the run archive, session
            # index, and workflow script folder. Writing their summaries into
            # generic Project Memory makes later short chat turns behave as if
            # the user wanted to continue the workflow.
            if workspace and workflow is None and final_state.get("final_answer"):
                try:
                    _extract_memory(workspace, user_message, final_state["final_answer"])
                except Exception:
                    logger.debug("memory extraction failed (non-fatal)", exc_info=True)
            try:
                status_map = {
                    "success": SessionStatus.SUCCESS,
                    "error": SessionStatus.ERROR,
                    "cancelled": SessionStatus.CANCELLED,
                }
                session.finish(
                    status=status_map.get(final_state["status"], SessionStatus.ERROR),
                    summary=final_state.get("final_answer") or final_state.get("error") or "",
                )
                if ctx.meta is not None:
                    ctx.meta["_agent_session"] = session
                run_archive.record_session(session.to_dict())
                if workspace:
                    SessionStore(workspace).upsert(session)
            except Exception:
                logger.debug("session finalization failed (non-fatal)", exc_info=True)
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
            max_steps=effective_max_steps,
            run_id=archive.run_id,
            # AgentLoop streams its own tokens via _on_thought_delta;
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
