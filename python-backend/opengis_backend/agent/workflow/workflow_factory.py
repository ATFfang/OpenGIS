"""
Factory for a fully-wired WorkflowLoop + subprocess executor.

Mirrors :func:`build_agent_loop` but returns a WorkflowLoop instead.
Shares the same executor, LLM caller, and system prompt infrastructure.

v3.2 (2026-04): Initial implementation.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from opengis_backend.agent.loop.types import AgentStep, CodeExecResult
from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.factory_common import build_loop_runtime_bundle
from opengis_backend.agent.llm import LLMConfig
from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer
from opengis_backend.agent.loop.workflow_loop import WorkflowLoop
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import ToolRegistry

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
    runtime = build_loop_runtime_bundle(
        tools=tools,
        llm_config=llm_config,
        ctx=ctx,
        profile=profile,
        progress_callback=progress_callback,
        execution_output_callback=execution_output_callback,
        risky_op_listener=risky_op_listener,
        include_workspace_write_note=False,
    )

    system_prompt = runtime.system_prompt + _WORKFLOW_SYSTEM_SUFFIX
    if profile.prompt_suffix:
        system_prompt += "\n" + profile.prompt_suffix.strip() + "\n"

    # Build the workflow loop. Reuse the shared conversation context if
    # provided so the workflow can see prior chat history.
    workflow_loop = WorkflowLoop(
        llm_call=runtime.llm_call,
        executor_call=runtime.executor_call,
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
        tool_runtime=runtime.tool_runtime,
        tool_schemas=runtime.tool_schemas,
        tool_materializer=ToolMaterializer(runtime.tool_schemas),
        workspace=str((getattr(ctx, "meta", None) or {}).get("workspace_path", "")),
    )

    return workflow_loop, runtime.executor


__all__ = ["build_workflow_loop"]
