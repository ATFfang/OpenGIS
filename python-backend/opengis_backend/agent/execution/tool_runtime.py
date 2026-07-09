"""Shared function-call tool runtime for agent loops.

This module is the one place that knows how OpenGIS tool calls are exposed
to LLMs and executed at runtime. Both the free-form AgentLoop and the
WorkflowLoop use it, which keeps function-calling behavior consistent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Callable, Optional

from opengis_backend.agent.execution.code_validation import validate_execute_code_payload
from opengis_backend.agent.execution.python_execution import PythonExecutionRuntime
from opengis_backend.agent.execution.tool_helpers import (
    artifact_hints,
    merge_metadata,
    parse_tool_arguments,
    permission_metadata,
    stringify_tool_value,
    tool_title,
)
from opengis_backend.agent.execution.tool_output import ToolOutputRuntime
from opengis_backend.agent.execution.tool_result import ToolExecutionResult
from opengis_backend.agent.execution.tool_schemas import (
    CODE_ONLY_TOOLS,
    EXECUTE_CODE_SCHEMA,
    RUN_SCRIPT_FILE_SCHEMA,
    build_tool_schemas,
)
from opengis_backend.agent.governance.permission import (
    PermissionAction,
    PermissionRuntime,
)

logger = logging.getLogger(__name__)


class ToolRuntime:
    """Execute LLM tool calls against executable tools and the code sandbox."""

    def __init__(
        self,
        *,
        tool_schemas: list[dict],
        tool_callables: dict[str, Callable],
        executor_call: Callable[[str], Any],
        permission_runtime: PermissionRuntime | None = None,
        output_runtime: ToolOutputRuntime | None = None,
        progress_callback: Optional[Callable[[str, str], None]] = None,
        execution_output_callback: Optional[Callable[[str], None]] = None,
        python_executable: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        self.tool_schemas = tool_schemas
        self.tool_callables = tool_callables
        self.executor_call = executor_call
        self.permission_runtime = permission_runtime
        self.output_runtime = output_runtime
        self.progress_callback = progress_callback
        self.execution_output_callback = execution_output_callback
        self.python_executable = python_executable
        self.workspace_path = workspace_path
        self.python_runtime = PythonExecutionRuntime(
            executor_call=executor_call,
            permission_runtime=permission_runtime,
            progress_callback=progress_callback,
            execution_output_callback=execution_output_callback,
            python_executable=python_executable,
            workspace_path=workspace_path,
        )
        self._async_loop: asyncio.AbstractEventLoop | None = None

    def execute(self, tool_name: str, arguments: dict[str, Any] | None) -> ToolExecutionResult:
        """Execute a tool call and return a string payload for the LLM."""
        args = arguments or {}
        t0 = time.monotonic()
        if tool_name == "execute_code":
            validation_error = validate_execute_code_payload(str(args.get("code") or ""))
            if validation_error:
                content = json.dumps(
                    {
                        "success": False,
                        "error": "invalid_execute_code_payload",
                        "message": validation_error,
                        "retry": (
                            "Call execute_code again with code-only Python, "
                            "or call the needed non-Python tool directly."
                        ),
                    },
                    ensure_ascii=False,
                )
                return ToolExecutionResult(
                    name=tool_name,
                    arguments=args,
                    content=content,
                    error="invalid_execute_code_payload",
                    duration_ms=(time.monotonic() - t0) * 1000,
                    title="Execute Python rejected",
                    metadata={"validation": "code_only"},
                )
        decision = (
            self.permission_runtime.evaluate(tool_name, args)
            if self.permission_runtime is not None
            else None
        )
        enforce_permissions = (
            self.permission_runtime is not None
            and getattr(self.permission_runtime.policy, "enforce", False)
        )
        if decision is not None and decision.action != PermissionAction.ALLOW and enforce_permissions:
            content = json.dumps(
                {
                    "success": False,
                    "error": "permission_required" if decision.action == PermissionAction.ASK else "permission_denied",
                    "permission": {
                        "action": decision.action.value,
                        "reason": decision.reason,
                        "rule": decision.rule,
                    },
                },
                ensure_ascii=False,
            )
            return ToolExecutionResult(
                name=tool_name,
                arguments=args,
                content=content,
                error=decision.reason or decision.action.value,
                duration_ms=(time.monotonic() - t0) * 1000,
                title=f"{tool_name} blocked",
                metadata={"permission": decision.action.value, "rule": decision.rule},
            )
        try:
            if tool_name == "execute_code":
                content = self.python_runtime.execute_code(args)
            elif tool_name == "run_script_file":
                return self._execute_script_file(args, t0, decision, enforce_permissions)
            elif tool_name in CODE_ONLY_TOOLS:
                content = self.python_runtime.execute_code_only_tool(tool_name, args)
            else:
                content = self._execute_tool(tool_name, args)
            raw_artifacts = artifact_hints(content)
            bounded = self._bound_output(tool_name, content)
            duration_ms = (time.monotonic() - t0) * 1000
            logger.info("TOOL OK: %s — %.0fms", tool_name, duration_ms)
            return ToolExecutionResult(
                name=tool_name,
                arguments=args,
                content=bounded.content,
                duration_ms=duration_ms,
                title=tool_title(tool_name),
                metadata=merge_metadata(
                    permission_metadata(decision),
                    raw_artifacts,
                    bounded.metadata,
                ),
                truncated=bounded.truncated,
            )
        except Exception as e:  # noqa: BLE001 - tool errors must return to the model
            duration_ms = (time.monotonic() - t0) * 1000
            error = f"{type(e).__name__}: {e}"
            logger.error("TOOL FAIL: %s(%s) -> %s", tool_name, args, error)
            return ToolExecutionResult(
                name=tool_name,
                arguments=args,
                content=json.dumps({"success": False, "error": error}, ensure_ascii=False),
                error=error,
                duration_ms=duration_ms,
                title=f"{tool_name} failed",
            )

    def _execute_script_file(
        self,
        arguments: dict[str, Any],
        t0: float,
        decision: Any,
        enforce_permissions: bool,
    ) -> ToolExecutionResult:
        script_path = str(arguments.get("script_path") or "").strip()
        if not script_path:
            return ToolExecutionResult(
                name="run_script_file",
                arguments=arguments,
                content=json.dumps({"success": False, "error": "script_path is required"}, ensure_ascii=False),
                error="script_path is required",
                duration_ms=(time.monotonic() - t0) * 1000,
                title="Run Script File failed",
            )

        try:
            path, code = self.python_runtime.load_script_file(script_path)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return ToolExecutionResult(
                name="run_script_file",
                arguments=arguments,
                content=json.dumps({"success": False, "error": error}, ensure_ascii=False),
                error=error,
                duration_ms=(time.monotonic() - t0) * 1000,
                title="Run Script File failed",
            )

        if self.permission_runtime is not None:
            code_decision = self.permission_runtime.evaluate("execute_code", {"code": code})
            if code_decision.action != PermissionAction.ALLOW and enforce_permissions:
                content = json.dumps(
                    {
                        "success": False,
                        "error": "permission_required" if code_decision.action == PermissionAction.ASK else "permission_denied",
                        "permission": {
                            "action": code_decision.action.value,
                            "reason": code_decision.reason,
                            "rule": code_decision.rule,
                        },
                    },
                    ensure_ascii=False,
                )
                return ToolExecutionResult(
                    name="run_script_file",
                    arguments=arguments,
                    content=content,
                    error=code_decision.reason or code_decision.action.value,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    title="Run Script File blocked",
                    metadata={"permission": code_decision.action.value, "rule": code_decision.rule},
                )

        content = self.python_runtime.execute_code({"code": code})
        raw_artifacts = artifact_hints(content)
        bounded = self._bound_output("run_script_file", content)
        duration_ms = (time.monotonic() - t0) * 1000
        logger.info("TOOL OK: run_script_file — %.0fms", duration_ms)
        return ToolExecutionResult(
            name="run_script_file",
            arguments=arguments,
            content=bounded.content,
            duration_ms=duration_ms,
            title="Run Script File",
            metadata=merge_metadata(
                permission_metadata(decision),
                raw_artifacts,
                bounded.metadata,
                {
                    "script_path": self.python_runtime.relative_script_path(path),
                    "script_abs_path": str(path),
                    "rerun_script": True,
                },
            ),
            truncated=bounded.truncated,
        )

    def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        callable_fn = self.tool_callables.get(tool_name)
        if callable_fn is None:
            return json.dumps(
                {"success": False, "error": f"Tool not found: {tool_name}"},
                ensure_ascii=False,
            )

        value = callable_fn(**arguments)
        if inspect.iscoroutine(value):
            value = self._run_coroutine(value)
        return stringify_tool_value(value)

    def _run_coroutine(self, coro: Any) -> Any:
        if self._async_loop is None or self._async_loop.is_closed():
            self._async_loop = asyncio.new_event_loop()
        return self._async_loop.run_until_complete(coro)

    def _bound_output(self, tool_name: str, content: str):
        if self.output_runtime is None:
            from opengis_backend.agent.execution.tool_output import BoundedToolOutput

            return BoundedToolOutput(content=content)
        return self.output_runtime.bound(content, tool_name=tool_name)



__all__ = [
    "EXECUTE_CODE_SCHEMA",
    "RUN_SCRIPT_FILE_SCHEMA",
    "ToolExecutionResult",
    "ToolRuntime",
    "build_tool_schemas",
    "parse_tool_arguments",
]
