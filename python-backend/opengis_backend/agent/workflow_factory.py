"""
Factory for a fully-wired WorkflowLoop + subprocess executor.

Mirrors :func:`build_agent_loop` but returns a WorkflowLoop instead.
Shares the same executor, LLM caller, and system prompt infrastructure.

v3.2 (2026-04): Initial implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from opengis_backend.agent.agent_loop import AgentStep, CodeExecResult
from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.context_projector import ContextProjector
from opengis_backend.agent.executor_factory import build_subprocess_executor
from opengis_backend.agent.llm import LLMConfig, build_llm_caller
from opengis_backend.agent.permission import PermissionRuntime
from opengis_backend.agent.profile import AgentProfile
from opengis_backend.agent.prompts import OPENGIS_SYSTEM_PROMPT, build_tool_signatures
from opengis_backend.agent.tool_output import ToolOutputRuntime
from opengis_backend.agent.tool_runtime import ToolRuntime, build_tool_schemas
from opengis_backend.agent.tools import build_tool_callables, filter_agent_tools
from opengis_backend.agent.workflow_loop import WorkflowDocument, WorkflowLoop
from opengis_backend.tools.context import ToolContext
from opengis_backend.skills.discovery import UserSkillDiscovery, format_available_skills
from opengis_backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


_WORKFLOW_SYSTEM_SUFFIX = """

## Workflow Execution Mode

You are currently executing a structured workflow. Follow these rules:
1. Focus on the CURRENT STEP only — do not skip ahead or combine steps.
2. Use results from previous steps when they are provided.
3. Call function tools to do the work. Use `execute_code` for Python.
4. If a step fails, analyze the tool/code error and fix the arguments or
   patch/rerun the persisted script instead of creating near-duplicates.
5. You may call MULTIPLE tools within a single step. Keep using tools until
   the step is FULLY complete.
6. When a step is done, reply with PLAIN TEXT to signal completion.
   Summarize what was accomplished in that text reply.
7. IMPORTANT: Save all output files to the workspace directory. If the
   step mentions visualization or map display, call `add_layer(...)`.
8. Do NOT stop after just loading data — complete the ENTIRE task.
9. Do NOT output Markdown code blocks as executable work. They will not be
   executed. Python execution must be a structured `execute_code` tool call.
10. `execute_code.code` must contain raw executable Python only. Do not put
    hidden reasoning, strategy narration, tool-planning prose, Markdown fences,
    `<think>` tags, or long comment monologues in code. If another tool is
    needed, call that function tool directly in the next action.
"""


def build_workflow_loop(
    *,
    tools: ToolRegistry,
    llm_config: LLMConfig,
    ctx: ToolContext,
    workflow: WorkflowDocument,
    max_retries_per_node: int = 3,
    step_callback: Optional[Callable[[AgentStep], None]] = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
    execution_output_callback: Optional[Callable[[str], None]] = None,
    risky_op_listener: Optional[Callable[[dict], None]] = None,
    on_thought_delta: Optional[Callable[[str], None]] = None,
    on_code_start: Optional[Callable[[int], None]] = None,
    on_code_delta: Optional[Callable[[int, str], None]] = None,
    on_code_end: Optional[Callable[[int], None]] = None,
    on_tool_start: Optional[Callable[[str, dict, str], None]] = None,
    on_tool_result: Optional[Callable[..., None]] = None,
    context: Optional[ContextManager] = None,
    plan_callback: Optional[Callable[[dict], None]] = None,
    agent_profile: Optional[AgentProfile] = None,
) -> tuple["WorkflowLoop", Any]:
    """Build a fresh WorkflowLoop + subprocess executor.

    Parameters
    ----------
    tools:
        The ToolRegistry for tool construction and system-prompt rendering.
    llm_config:
        Provider-agnostic LLM config.
    ctx:
        Per-run ToolContext.
    workflow:
        The parsed WorkflowDocument to execute.
    max_retries_per_node:
        Default max retries per node.
    step_callback:
        Optional callback invoked after each step.
    risky_op_listener:
        Optional callback for D3 telemetry.

    Returns
    -------
    (workflow_loop, executor)
        The executor MUST be cleaned up by the caller after the run.
    """
    profile = agent_profile or AgentProfile.workflow_runner()
    registered = tools.list_registered()
    if profile.tool_groups is not None:
        registered = [s for s in registered if s.schema.group in profile.tool_groups]
    registered = filter_agent_tools(registered)

    # Build tool callables.
    tool_callables = build_tool_callables(registered, ctx_provider=lambda: ctx)
    tool_schemas = build_tool_schemas(registered)

    # Build the LLM caller.
    llm_call = build_llm_caller(llm_config)

    # Build a listener that forwards plot_saved events from the child
    # subprocess to the frontend via ctx.notify (rpc.ui.chat.show_image).
    def _plot_saved_listener(msg: dict) -> None:
        payload: dict = {"path": msg.get("path", "")}
        caption = msg.get("caption")
        if caption:
            payload["caption"] = caption
        run_id = (getattr(ctx, "meta", None) or {}).get("run_id")
        if run_id:
            payload["run_id"] = run_id
        try:
            from opengis_backend.tools.context import run_async_from_sync
            run_async_from_sync(ctx.notify("rpc.ui.chat.show_image", payload))
        except Exception as e:
            logger.warning("plot_saved_listener notify failed: %s", e)

    # Build the subprocess executor.
    executor = build_subprocess_executor(
        ctx,
        stdout_listener=execution_output_callback,
        risky_op_listener=risky_op_listener,
        plot_saved_listener=_plot_saved_listener,
    )

    # Initialize the executor with tool names.
    executor.send_tools(tool_callables)

    # Compose the system prompt with workflow suffix.
    # Add workspace info if available.
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    user_skills = UserSkillDiscovery(workspace_path=workspace).list()
    base_prompt = OPENGIS_SYSTEM_PROMPT.format(
        tool_signatures=build_tool_signatures(registered),
        available_skills=format_available_skills(user_skills),
    )

    # Add workspace info if available.
    if workspace:
        base_prompt += (
            f"\n## Workspace\n"
            f"Your current working directory is: {workspace}\n"
            "All relative paths in your code resolve against it.\n"
        )
        try:
            current_user_message = str(((getattr(ctx, "meta", None) or {}).get("_current_user_message")) or "")
            projected = ContextProjector(workspace).project(current_user_message)
            if projected:
                base_prompt += f"\n## Retrieved Project Memory\n{projected}\n"
        except Exception:
            logger.debug("ContextProjector failed for workflow; continuing without project memory", exc_info=True)

    system_prompt = base_prompt + _WORKFLOW_SYSTEM_SUFFIX
    if profile.prompt_suffix:
        system_prompt += "\n" + profile.prompt_suffix.strip() + "\n"

    # Wrap the executor as a simple callable.
    def _executor_call(code: str) -> CodeExecResult:
        return executor(code)

    approval_callback = (getattr(ctx, "meta", None) or {}).get("_approval_callback")
    permission_runtime = PermissionRuntime.from_profile(
        profile,
        workspace_path=(getattr(ctx, "meta", None) or {}).get("workspace_path"),
    )
    if callable(approval_callback):
        permission_runtime.approval_callback = approval_callback

    tool_runtime = ToolRuntime(
        tool_schemas=tool_schemas,
        tool_callables=tool_callables,
        executor_call=_executor_call,
        permission_runtime=permission_runtime,
        output_runtime=ToolOutputRuntime(
            workspace_path=(getattr(ctx, "meta", None) or {}).get("workspace_path"),
        ),
        progress_callback=progress_callback,
        execution_output_callback=execution_output_callback,
        python_executable=executor.config.python_executable,
        workspace_path=(getattr(ctx, "meta", None) or {}).get("workspace_path"),
    )

    # Build the workflow loop. Reuse the shared conversation context if
    # provided so the workflow can see prior chat history.
    workflow_loop = WorkflowLoop(
        llm_call=llm_call,
        executor_call=_executor_call,
        system_prompt=system_prompt,
        workflow=workflow,
        max_retries_per_node=max_retries_per_node,
        step_callback=step_callback,
        progress_callback=progress_callback,
        on_thought_delta=on_thought_delta,
        on_code_start=on_code_start,
        on_code_delta=on_code_delta,
        on_code_end=on_code_end,
        on_tool_start=on_tool_start,
        on_tool_result=on_tool_result,
        plan_callback=plan_callback,
        context=context if context is not None else ContextManager(),
        tool_runtime=tool_runtime,
        tool_schemas=tool_schemas,
        workspace=str((getattr(ctx, "meta", None) or {}).get("workspace_path", "")),
    )

    return workflow_loop, executor


__all__ = ["build_workflow_loop"]
