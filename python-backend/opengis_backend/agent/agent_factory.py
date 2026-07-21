"""
Factory for a fully-wired AgentLoop + subprocess executor.

This module is the "glue layer": it pulls together every other
agent/* factory and returns the objects needed to drive a run.

Public surface is one function, :func:`build_agent_loop`. Callers
pass the raw ingredients and get back ``(agent_loop, executor)``; the
executor's lifecycle (``cleanup()`` after the run) is still owned by
the caller.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from opengis_backend.agent.loop.agent_loop import AgentLoop
from opengis_backend.agent.loop.types import AgentStep, CodeExecResult
from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.factory_common import build_loop_runtime_bundle
from opengis_backend.agent.llm import LLMConfig
from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import ToolRegistry


def build_agent_loop(
    *,
    tools: ToolRegistry,
    llm_config: LLMConfig,
    ctx: ToolContext,
    step_callback: Optional[Callable[[AgentStep], None]] = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
    execution_output_callback: Optional[Callable[[str], None]] = None,
    risky_op_listener: Optional[Callable[[dict], None]] = None,
    context: Optional[ContextManager] = None,
    on_thought_delta: Optional[Callable[[str], None]] = None,
    on_code_start: Optional[Callable[[int], None]] = None,
    on_code_delta: Optional[Callable[[int, str], None]] = None,
    on_code_end: Optional[Callable[[int], None]] = None,
    on_tool_start: Optional[Callable[[str, dict, str], None]] = None,
    on_tool_result: Optional[Callable[..., None]] = None,
    on_provider_result: Optional[Callable[..., None]] = None,
    tool_groups: Optional[list[str]] = None,
    user_instructions: Optional[str] = None,
    agent_profile: Optional[AgentProfile] = None,
) -> tuple[AgentLoop, Any]:
    """Build a fresh AgentLoop + subprocess executor.

    The agent loop is rebuilt per-run so that each tool callable's
    closure captures *this* ``ctx`` directly — no contextvar gymnastics.

    Parameters
    ----------
    tools:
        The ToolRegistry whose ``list_registered()`` result drives both
        tool construction and system-prompt rendering.
    llm_config:
        Provider-agnostic LLM config. Routing (MiniMax / OpenAI / etc.)
        is handled by :func:`opengis_backend.agent.llm.build_llm_caller`.
    ctx:
        Per-run ``ToolContext``. Used by (a) tool closures that need
        ``needs_ctx=True``, (b) the executor factory to pick the
        subprocess' working directory, and (c) the system-prompt
        composer to inject the workspace path.
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    risky_op_listener:
        Optional callback for D3 telemetry (write-effect observations
        from the subprocess).
    context:
        Optional pre-existing ContextManager to reuse (for per-conversation
        memory). If None, a fresh ContextManager is created.

    Returns
    -------
    (agent_loop, executor)
        The ``executor`` MUST be cleaned up by the caller (via
        ``executor.cleanup()``) after the run finishes, win or lose —
        it owns a subprocess handle.
    """
    profile = agent_profile or AgentProfile.gis_build()
    runtime = build_loop_runtime_bundle(
        tools=tools,
        llm_config=llm_config,
        ctx=ctx,
        profile=profile,
        progress_callback=progress_callback,
        execution_output_callback=execution_output_callback,
        risky_op_listener=risky_op_listener,
        tool_groups=tool_groups,
        include_workspace_write_note=True,
    )
    system_prompt = runtime.system_prompt
    if profile.prompt_suffix:
        system_prompt += "\n" + profile.prompt_suffix.strip() + "\n"

    # Build the agent loop.
    agent_loop = AgentLoop(
        llm_call=runtime.llm_call,
        executor_call=runtime.executor_call,
        system_prompt=system_prompt,
        step_callback=step_callback,
        progress_callback=progress_callback,
        on_thought_delta=on_thought_delta,
        on_code_start=on_code_start,
        on_code_delta=on_code_delta,
        on_code_end=on_code_end,
        on_tool_start=on_tool_start,
        on_tool_result=on_tool_result,
        on_provider_result=on_provider_result,
        context=context if context is not None else ContextManager(),
        user_instructions=user_instructions,
        agent_profile=profile,
        project_memory=runtime.project_memory,
        tool_runtime=runtime.tool_runtime,
        tool_schemas=runtime.tool_schemas,
        tool_materializer=ToolMaterializer(runtime.tool_schemas),
    )

    return agent_loop, runtime.executor


__all__ = [
    "build_agent_loop",
]
