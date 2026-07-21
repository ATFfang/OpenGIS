"""Shared construction helpers for agent and workflow factories."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from opengis_backend.agent.context.context_projector import ContextProjector
from opengis_backend.agent.context.request_budget import RequestBudgetManager
from opengis_backend.agent.execution.executor_factory import build_subprocess_executor
from opengis_backend.agent.execution.tool_capabilities import format_capability_manifest
from opengis_backend.agent.execution.tool_output import ToolOutputRuntime
from opengis_backend.agent.execution.tool_runtime import ToolRuntime, build_tool_schemas
from opengis_backend.agent.governance.permission import PermissionRuntime
from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.llm import LLMConfig, build_llm_caller
from opengis_backend.agent.prompts import OPENGIS_SYSTEM_PROMPT, build_tool_catalog_summary
from opengis_backend.agent.telemetry.event_log import MessagePart
from opengis_backend.agent.tools import build_tool_callables, filter_agent_tools
from opengis_backend.skills.discovery import UserSkillDiscovery, format_available_skills
from opengis_backend.tools.context import ToolContext, run_async_from_sync
from opengis_backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class LoopRuntimeBundle:
    registered_tools: list[Any]
    tool_callables: dict[str, Callable[..., Any]]
    tool_schemas: list[dict[str, Any]]
    llm_call: Callable[..., Any]
    executor: Any
    executor_call: Callable[[str], Any]
    tool_runtime: ToolRuntime
    system_prompt: str
    # Task-relevant project memory for the CURRENT user message. Deliberately
    # kept OUT of ``system_prompt`` (the cacheable stable prefix): memory is
    # keyed on the live user message and changes every run, so the loops inject
    # it into the DYNAMIC TAIL (after conversation history) instead.
    project_memory: str = ""


def compose_system_prompt(
    registered_tools: list[Any],
    ctx: ToolContext,
    *,
    include_workspace_write_note: bool,
) -> str:
    """Build the STABLE system prefix.

    Everything appended here must be byte-stable across turns and across runs
    of the same workspace/profile so the provider can cache the prefix. Only
    static content belongs here: the base template, the domain capability
    manifest, the workspace path, and the workspace-write note. Per-run dynamic
    content (project memory) is projected separately by
    :func:`project_run_memory` and placed in the dynamic tail by the loops.
    """
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    user_skills = UserSkillDiscovery(workspace_path=workspace).list()
    prompt = OPENGIS_SYSTEM_PROMPT.format(
        tool_signatures=build_tool_catalog_summary(registered_tools),
        available_skills=format_available_skills(user_skills),
    )
    # Stable capability manifest: domain-level "what this platform can do".
    # Deterministic from the profile tool set, so it rides the cache prefix and
    # keeps the model from denying a supported capability.
    manifest = format_capability_manifest([rs.schema.name for rs in registered_tools])
    if manifest:
        prompt += "\n" + manifest + "\n"
    if not workspace:
        return prompt

    prompt += (
        "\n## Workspace\n"
        f"Your current working directory is: {workspace}\n"
        "All relative paths in your code resolve against it.\n"
    )
    if include_workspace_write_note:
        prompt += (
            "You may write to / read from any file under this directory. Writes\n"
            "are snapshotted by git so the user can revert any run.\n"
        )
    return prompt


def project_run_memory(ctx: ToolContext) -> str:
    """Retrieve task-relevant project memory for the CURRENT user message.

    Returns a ready-to-inject block (or ``""``). This is intentionally NOT part
    of the system prompt: because it is keyed on the live user message it
    changes every run and would otherwise break the cacheable stable prefix at
    the very top. The loops inject the returned text into the DYNAMIC TAIL,
    physically after the conversation history.
    """
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if not workspace:
        return ""
    try:
        current_user_message = str(((getattr(ctx, "meta", None) or {}).get("_current_user_message")) or "")
        memory_limit = RequestBudgetManager().suggest_limits().max_memory_records
        projected = ContextProjector(workspace).project(
            current_user_message,
            max_records=memory_limit,
        )
        if projected:
            return f"## Retrieved Project Memory\n{projected}"
    except Exception:
        logger.debug("project_run_memory failed; continuing without project memory", exc_info=True)
    return ""


def build_loop_runtime_bundle(
    *,
    tools: ToolRegistry,
    llm_config: LLMConfig,
    ctx: ToolContext,
    profile: AgentProfile,
    progress_callback: Callable[[str, str], None] | None,
    execution_output_callback: Callable[[str], None] | None,
    risky_op_listener: Callable[[dict], None] | None,
    tool_groups: list[str] | None = None,
    include_workspace_write_note: bool = True,
) -> LoopRuntimeBundle:
    registered = tools.list_registered()
    effective_groups = tool_groups if tool_groups is not None else profile.tool_groups
    if effective_groups is not None:
        registered = [s for s in registered if s.schema.group in effective_groups]
    registered = filter_agent_tools(registered)

    tool_callables = build_tool_callables(registered, ctx_provider=lambda: ctx)
    tool_schemas = build_tool_schemas(registered)
    llm_call = build_llm_caller(llm_config)

    def _plot_saved_listener(msg: dict) -> None:
        run_id = (getattr(ctx, "meta", None) or {}).get("run_id")
        path = str(msg.get("path") or "")
        if not path:
            return
        caption = str(msg.get("caption") or Path(path).stem)
        part = MessagePart.create(
            id=f"{run_id or 'run'}:artifact:image:{Path(path).name}",
            type="artifact",
            status="completed",
            text=caption,
            run_id=str(run_id or ""),
            data={
                "kind": "image",
                "images": [path],
                "files": [path],
                "path": path,
                "title": caption,
            },
        )
        try:
            run_async_from_sync(ctx.notify("chat.message_part", {"part": part.to_dict()}))
        except Exception as exc:
            logger.warning("plot_saved_listener notify failed: %s", exc)

    executor = build_subprocess_executor(
        ctx,
        stdout_listener=execution_output_callback,
        risky_op_listener=risky_op_listener,
        plot_saved_listener=_plot_saved_listener,
    )
    executor.send_tools(tool_callables)

    def _executor_call(code: str):
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

    return LoopRuntimeBundle(
        registered_tools=registered,
        tool_callables=tool_callables,
        tool_schemas=tool_schemas,
        llm_call=llm_call,
        executor=executor,
        executor_call=_executor_call,
        tool_runtime=tool_runtime,
        system_prompt=compose_system_prompt(
            registered,
            ctx,
            include_workspace_write_note=include_workspace_write_note,
        ),
        project_memory=project_run_memory(ctx),
    )
