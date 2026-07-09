"""Python execution strategy for execute_code and script reruns."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from opengis_backend.agent.governance.permission import PermissionRuntime


logger = logging.getLogger(__name__)


class PythonExecutionRuntime:
    """Execute Python code through the persistent subprocess executor."""

    def __init__(
        self,
        *,
        executor_call: Callable[[str], Any],
        permission_runtime: PermissionRuntime | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
        execution_output_callback: Callable[[str], None] | None = None,
        python_executable: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        self.executor_call = executor_call
        self.permission_runtime = permission_runtime
        self.progress_callback = progress_callback
        self.execution_output_callback = execution_output_callback
        self.python_executable = python_executable
        self.workspace_path = workspace_path

    def execute_code(self, arguments: dict[str, Any]) -> str:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return json.dumps({"success": False, "error": "No code provided"}, ensure_ascii=False)
        resident_warning = self.resident_dynamic_script_warning(code)
        if resident_warning:
            return json.dumps(
                {
                    "success": False,
                    "error": "resident_worker_required",
                    "message": resident_warning,
                    "recommended_tool": "start_dynamic_map_worker",
                },
                ensure_ascii=False,
            )

        install_notes: list[str] = []
        installed = self.auto_install_for_code(code)
        if installed:
            install_notes.append(installed)

        result = self.executor_call(code)
        missing_import = self.missing_import_from_error(getattr(result, "error", None))
        if missing_import:
            retry_install = self.auto_install_for_code(f"import {missing_import}")
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

    def execute_code_only_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Proxy child-only helper calls, such as save_plot, into execute_code."""
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
        return self.execute_code({"code": f"save_plot({rendered_kwargs})"})

    def load_script_file(self, script_path: str) -> tuple[Path, str]:
        path = self.resolve_script_file(script_path)
        return path, path.read_text(encoding="utf-8", errors="replace")

    def resolve_script_file(self, script_path: str) -> Path:
        if not self.workspace_path:
            raise RuntimeError("workspace_path is required to run a persisted script file")
        workspace = Path(self.workspace_path).expanduser().resolve()
        raw = Path(script_path).expanduser()
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
        script_root = (workspace / "script").resolve()
        if path.suffix.lower() != ".py":
            raise ValueError(f"script_path must point to a .py file: {script_path}")
        if script_root != path and script_root not in path.parents:
            raise ValueError(f"script_path must be under workspace script/: {script_path}")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"script not found: {script_path}")
        return path

    def relative_script_path(self, path: Any) -> str:
        try:
            if self.workspace_path:
                return str(Path(path).resolve().relative_to(Path(self.workspace_path).expanduser().resolve()))
        except Exception:
            pass
        return str(path)

    @staticmethod
    def resident_dynamic_script_warning(code: str) -> str | None:
        """Block obvious live-map loops from execute_code."""
        compact = code.lower()
        emits_dynamic_map = (
            "dynamic_layer_update" in compact
            or "emit_dynamic_layer" in compact
            or "emit_dynamic_points" in compact
            or "emit_dynamic_tracks" in compact
            or "emit_moving_objects" in compact
            or "rpc.ui.map.dynamic_layer_update" in compact
        )
        has_resident_loop = (
            re.search(r"\bwhile\s+true\s*:", compact) is not None
            or re.search(r"\bwhile\s+1\s*:", compact) is not None
            or "time.sleep(" in compact
            or "asyncio.sleep(" in compact
        )
        if emits_dynamic_map and has_resident_loop:
            return (
                "This code looks like a resident dynamic-map refresh loop. "
                "Do not run it with execute_code/run_script_file: it will stop "
                "when the agent run stops. Start a resident worker instead, "
                "preferably with start_dynamic_map_worker, and put the loop in main.py."
            )
        return None

    def auto_install_for_code(self, code: str) -> str | None:
        try:
            from opengis_backend.agent.execution.auto_install import auto_install_missing

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
    def missing_import_from_error(error: Any) -> str | None:
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

__all__ = ["PythonExecutionRuntime"]
