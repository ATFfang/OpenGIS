"""
Factory for a fully-wired AgentLoop + subprocess executor.

This module is the "glue layer": it pulls together every other
agent/* factory and returns the objects that :class:`GISCodeAgent`
needs to drive a run.

v3.1 (2026-04): Replaced smolagents CodeAgent with our custom AgentLoop.
No more smolagents dependency in the factory.

Public surface is one function, :func:`build_agent_loop`. Callers
pass the raw ingredients and get back ``(agent_loop, executor)``; the
executor's lifecycle (``cleanup()`` after the run) is still owned by
the caller.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from opengis_backend.agent.agent_loop import AgentLoop, AgentStep, CodeExecResult
from opengis_backend.agent.context_manager import ContextManager
from opengis_backend.agent.executor_factory import build_subprocess_executor
from opengis_backend.agent.llm import LLMConfig, build_llm_caller
from opengis_backend.agent.prompts import OPENGIS_SYSTEM_PROMPT, build_skill_signatures
from opengis_backend.agent.tools import build_tool_callables
from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def _compose_system_prompt(registered_skills, ctx: Optional[SkillContext] = None) -> str:
    """Render the OpenGIS system prompt with live skill signatures.

    When a ``ctx`` is given and has a ``workspace_path``, we append an
    additional ``## Workspace`` block so the LLM knows the absolute cwd
    of the subprocess, plus any persisted project memory.
    """
    base = OPENGIS_SYSTEM_PROMPT.format(
        skill_signatures=build_skill_signatures(registered_skills)
    )
    if ctx is None:
        return base
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if not workspace:
        return base
    suffix = (
        "\n## Workspace\n"
        f"Your current working directory is: {workspace}\n"
        "All relative paths in your code resolve against it. You may\n"
        "write to / read from any file under this directory. Writes\n"
        "are snapshotted by git so the user can revert any run.\n"
    )
    # Inject project memory (key facts from previous conversations).
    try:
        from opengis_backend.workspace.memory import load as load_memory
        memory = load_memory(workspace)
        if memory:
            suffix += f"\n## Project Memory\n{memory}\n"
    except Exception:
        pass
    return base + suffix


def build_agent_loop(
    *,
    skills: SkillRegistry,
    llm_config: LLMConfig,
    ctx: SkillContext,
    max_steps: int,
    step_callback: Optional[Callable[[AgentStep], None]] = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
    risky_op_listener: Optional[Callable[[dict], None]] = None,
    context: Optional[ContextManager] = None,
    on_thought_delta: Optional[Callable[[str], None]] = None,
    on_code_start: Optional[Callable[[int], None]] = None,
    on_code_delta: Optional[Callable[[int, str], None]] = None,
    on_code_end: Optional[Callable[[int], None]] = None,
    on_reasoning_start: Optional[Callable[[int], None]] = None,
    on_reasoning_end: Optional[Callable[[int], None]] = None,
    on_reasoning_promote: Optional[Callable[[int], None]] = None,
    skill_groups: Optional[list[str]] = None,
    user_instructions: Optional[str] = None,
) -> tuple[AgentLoop, Any]:
    """Build a fresh AgentLoop + subprocess executor.

    The agent loop is rebuilt per-run so that each tool callable's
    closure captures *this* ``ctx`` directly — no contextvar gymnastics.

    Parameters
    ----------
    skills:
        The SkillRegistry whose ``list_registered()`` result drives both
        tool construction and system-prompt rendering.
    llm_config:
        Provider-agnostic LLM config. Routing (MiniMax / OpenAI / etc.)
        is handled by :func:`opengis_backend.agent.llm.build_llm_caller`.
    ctx:
        Per-run ``SkillContext``. Used by (a) tool closures that need
        ``needs_ctx=True``, (b) the executor factory to pick the
        subprocess' working directory, and (c) the system-prompt
        composer to inject the workspace path.
    max_steps:
        Hard cap on code execution steps in the loop.
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
    registered = skills.list_registered()

    # Filter by skill groups if specified (e.g. only expose 'core' + 'qgis' skills).
    if skill_groups is not None:
        registered = [s for s in registered if s.schema.group in skill_groups]

    # Build tool callables — each needs_ctx skill receives *this* ctx.
    tool_callables = build_tool_callables(registered, ctx_provider=lambda: ctx)

    # Build the LLM caller.
    llm_call = build_llm_caller(llm_config)

    # Build a listener that forwards plot_saved events from the child
    # subprocess to the frontend via ctx.notify (rpc.ui.chat.show_image).
    def _plot_saved_listener(msg: dict) -> None:
        payload: dict = {"path": msg.get("path", "")}
        caption = msg.get("caption")
        if caption:
            payload["caption"] = caption
        try:
            from opengis_backend.skills.context import run_async_from_sync
            run_async_from_sync(ctx.notify("rpc.ui.chat.show_image", payload))
        except Exception as e:
            logger.warning("plot_saved_listener notify failed: %s", e)

    # Build the subprocess executor.
    executor = build_subprocess_executor(
        ctx,
        risky_op_listener=risky_op_listener,
        plot_saved_listener=_plot_saved_listener,
    )

    # Initialize the executor with tool names.
    executor.send_tools(tool_callables)

    # Compose the system prompt.
    system_prompt = _compose_system_prompt(registered, ctx)

    # Wrap the executor as a simple callable for the agent loop.
    def _executor_call(code: str) -> CodeExecResult:
        return executor(code)

    # Build the agent loop.
    agent_loop = AgentLoop(
        llm_call=llm_call,
        executor_call=_executor_call,
        system_prompt=system_prompt,
        max_steps=max_steps,
        step_callback=step_callback,
        progress_callback=progress_callback,
        on_thought_delta=on_thought_delta,
        on_code_start=on_code_start,
        on_code_delta=on_code_delta,
        on_code_end=on_code_end,
        on_reasoning_start=on_reasoning_start,
        on_reasoning_end=on_reasoning_end,
        on_reasoning_promote=on_reasoning_promote,
        context=context if context is not None else ContextManager(),
        user_instructions=user_instructions,
    )

    return agent_loop, executor


# Legacy alias for backward compatibility.
build_code_agent = build_agent_loop


__all__ = [
    "build_agent_loop",
    "build_code_agent",
]
