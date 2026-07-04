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
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from opengis_backend.agent.permission import (
    PermissionAction,
    PermissionRuntime,
)
from opengis_backend.agent.tool_output import BoundedToolOutput, ToolOutputRuntime

logger = logging.getLogger(__name__)

CODE_ONLY_TOOLS = {"save_plot"}


EXECUTE_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Execute Python code in a sandbox. Use ONLY when no other tool matches "
            "the task. The code runs with access to numpy, pandas, geopandas, "
            "shapely, rasterio, matplotlib, seaborn, and the registered OpenGIS "
            "skills as top-level functions. Missing imported packages are "
            "auto-installed before execution when permitted; do not switch to a "
            "weaker method just because a package may be absent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute.",
                },
                "persist": {
                    "type": "boolean",
                    "description": (
                        "Normal chat only: persist this code as a reusable/auditable script. "
                        "Use false for quick one-off inspection. Workflow runs persist all code regardless."
                    ),
                },
                "script_name": {
                    "type": "string",
                    "description": "Short semantic script name used in the persisted filename when persist is true.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief purpose/metadata for this code when persisted.",
                },
            },
            "required": ["code"],
        },
    },
}


@dataclass
class ToolExecutionResult:
    """Normalized result of one function-call tool execution."""

    name: str
    arguments: dict[str, Any]
    content: str
    error: Optional[str] = None
    duration_ms: float = 0.0
    title: str = ""
    metadata: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class ToolRuntime:
    """Execute LLM tool calls against skills and the code sandbox."""

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
    ) -> None:
        self.tool_schemas = tool_schemas
        self.tool_callables = tool_callables
        self.executor_call = executor_call
        self.permission_runtime = permission_runtime
        self.output_runtime = output_runtime
        self.progress_callback = progress_callback
        self.execution_output_callback = execution_output_callback
        self.python_executable = python_executable
        self._async_loop: asyncio.AbstractEventLoop | None = None

    def execute(self, tool_name: str, arguments: dict[str, Any] | None) -> ToolExecutionResult:
        """Execute a tool call and return a string payload for the LLM."""
        args = arguments or {}
        t0 = time.monotonic()
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
                content = self._execute_code(args)
            elif tool_name in CODE_ONLY_TOOLS:
                content = self._execute_code_only_tool(tool_name, args)
            else:
                content = self._execute_skill(tool_name, args)
            raw_artifacts = self._artifact_hints(content)
            bounded = self._bound_output(tool_name, content)
            duration_ms = (time.monotonic() - t0) * 1000
            logger.info("TOOL OK: %s — %.0fms", tool_name, duration_ms)
            return ToolExecutionResult(
                name=tool_name,
                arguments=args,
                content=bounded.content,
                duration_ms=duration_ms,
                title=self._title_for(tool_name),
                metadata=self._merge_metadata(
                    self._permission_metadata(decision),
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

    def _execute_code(self, arguments: dict[str, Any]) -> str:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return json.dumps({"success": False, "error": "No code provided"}, ensure_ascii=False)

        install_notes: list[str] = []
        installed = self._auto_install_for_code(code)
        if installed:
            install_notes.append(installed)

        result = self.executor_call(code)
        missing_import = self._missing_import_from_error(getattr(result, "error", None))
        if missing_import:
            retry_install = self._auto_install_for_code(f"import {missing_import}")
            if retry_install:
                install_notes.append(f"{retry_install}; retrying original code")
                result = self.executor_call(code)

        parts: list[str] = []
        if install_notes:
            parts.extend(f"[auto-install] {note}" for note in install_notes)
        if getattr(result, "logs", ""):
            parts.append(str(result.logs))
        output = getattr(result, "output", None)
        if output is not None:
            parts.append(str(output))
        error = getattr(result, "error", None)
        if error:
            parts.append(f"Error: {error}")
        return "\n".join(parts).strip() or "(no output)"

    def _auto_install_for_code(self, code: str) -> str | None:
        """Install imports required by a code block before execution.

        Function-call execution goes through this runtime, so keeping the
        policy here makes `execute_code` match the legacy CodeAct path and
        prevents the model from burning turns on manual pip installs or
        weaker fallback algorithms.
        """
        try:
            from opengis_backend.agent.auto_install import auto_install_missing

            return auto_install_missing(
                code,
                python_executable=self.python_executable,
                progress_callback=self.progress_callback,
                output_callback=self.execution_output_callback,
                permission_runtime=self.permission_runtime,
            )
        except Exception:
            logger.warning(
                "auto_install check failed (non-fatal), code execution may fail if packages are missing",
                exc_info=True,
            )
            return None

    @staticmethod
    def _missing_import_from_error(error: Any) -> str | None:
        if not error:
            return None
        text = str(error)
        patterns = [r"No module named ['\"]([^'\"]+)['\"]"]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            name = match.group(1).split(".")[0].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                return name
        return None

    def _execute_code_only_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Run a code-only helper inside the persistent Python subprocess.

        Some providers can keep calling a previously-advertised tool for one
        turn after the schema changes. For helpers such as ``save_plot`` the
        parent process cannot do the work, because the matplotlib figure lives
        inside the child process. Proxy the call back into that child instead.
        """
        if tool_name != "save_plot":
            return json.dumps(
                {
                    "success": False,
                    "error": f"{tool_name} is only available inside execute_code.",
                },
                ensure_ascii=False,
            )

        allowed = {"caption", "filename", "dpi", "auto_close"}
        kwargs = {
            key: value
            for key, value in arguments.items()
            if key in allowed and value is not None
        }
        rendered_kwargs = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in kwargs.items()
        )
        return self._execute_code({"code": f"save_plot({rendered_kwargs})"})

    def _execute_skill(self, tool_name: str, arguments: dict[str, Any]) -> str:
        callable_fn = self.tool_callables.get(tool_name)
        if callable_fn is None:
            return json.dumps(
                {"success": False, "error": f"Tool not found: {tool_name}"},
                ensure_ascii=False,
            )

        value = callable_fn(**arguments)
        if inspect.iscoroutine(value):
            value = self._run_coroutine(value)
        return self._stringify(value)

    def _run_coroutine(self, coro: Any) -> Any:
        if self._async_loop is None or self._async_loop.is_closed():
            self._async_loop = asyncio.new_event_loop()
        return self._async_loop.run_until_complete(coro)

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(value)

    def _bound_output(self, tool_name: str, content: str) -> BoundedToolOutput:
        if self.output_runtime is None:
            return BoundedToolOutput(content=content)
        return self.output_runtime.bound(content, tool_name=tool_name)

    @staticmethod
    def _merge_metadata(*items: dict[str, Any] | None) -> dict[str, Any] | None:
        merged: dict[str, Any] = {}
        for item in items:
            if item:
                merged.update(item)
        return merged or None

    @staticmethod
    def _artifact_hints(content: str) -> dict[str, Any] | None:
        try:
            data = json.loads(content)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        hints: dict[str, Any] = {}
        path = data.get("path") or data.get("output_path") or data.get("save_path")
        if isinstance(path, str) and path:
            hints["artifact_path"] = path
        layer_id = data.get("layer_id")
        if isinstance(layer_id, str) and layer_id:
            hints["artifact_layer_id"] = layer_id
            hints["artifact_layer_name"] = data.get("name", layer_id)
        return hints or None

    @staticmethod
    def _title_for(tool_name: str) -> str:
        if tool_name == "execute_code":
            return "Execute Python"
        return tool_name.replace("_", " ").title()

    @staticmethod
    def _permission_metadata(decision: Any) -> dict[str, Any] | None:
        if decision is None:
            return None
        action = getattr(decision, "action", None)
        return {
            "permission": getattr(action, "value", None),
            "permission_reason": getattr(decision, "reason", ""),
            "permission_rule": getattr(decision, "rule", ""),
        }


def build_tool_schemas(registered: list[Any]) -> list[dict]:
    """Build OpenAI-compatible schemas for all skills plus execute_code."""
    schemas = [
        rs.schema.to_openai_schema()
        for rs in registered
        if rs.schema.name not in CODE_ONLY_TOOLS
    ]
    schemas.append(EXECUTE_CODE_SCHEMA)
    return schemas


def parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    """Best-effort parser for function-call arguments."""
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        logger.warning("Malformed tool arguments: %.200s", raw_args)
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "EXECUTE_CODE_SCHEMA",
    "ToolExecutionResult",
    "ToolRuntime",
    "build_tool_schemas",
    "parse_tool_arguments",
]
