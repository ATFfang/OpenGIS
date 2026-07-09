from __future__ import annotations

import unittest
from unittest.mock import patch

from opengis_backend.agent.execution.tool_runtime import ToolRuntime


class _ExecResult:
    def __init__(self, *, logs: str = "", output: str | None = None, error: str | None = None) -> None:
        self.logs = logs
        self.output = output
        self.error = error


class ToolRuntimeAutoInstallTests(unittest.TestCase):
    def test_execute_code_auto_installs_before_running(self) -> None:
        calls: list[str] = []

        def executor(code: str) -> _ExecResult:
            calls.append(code)
            return _ExecResult(output="ok")

        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=executor,
        )

        with patch(
            "opengis_backend.agent.execution.auto_install.auto_install_missing",
            return_value="Auto-installed: humanize",
        ) as auto_install:
            result = runtime.execute("execute_code", {"code": "import humanize\nprint('ok')"})

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["import humanize\nprint('ok')"])
        self.assertEqual(auto_install.call_count, 1)
        self.assertIn("[auto-install] Auto-installed: humanize", result.content)
        self.assertIn("ok", result.content)

    def test_execute_code_retries_original_code_after_runtime_missing_import(self) -> None:
        calls: list[str] = []

        def executor(code: str) -> _ExecResult:
            calls.append(code)
            if len(calls) == 1:
                return _ExecResult(error="ModuleNotFoundError: No module named 'humanize'")
            return _ExecResult(output="fixed")

        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=executor,
        )

        with patch(
            "opengis_backend.agent.execution.auto_install.auto_install_missing",
            side_effect=[None, "Auto-installed: humanize"],
        ) as auto_install:
            result = runtime.execute("execute_code", {"code": "plugin_loader()"})

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["plugin_loader()", "plugin_loader()"])
        self.assertEqual(auto_install.call_count, 2)
        self.assertEqual(auto_install.call_args_list[1].args[0], "import humanize")
        self.assertIn("retrying original code", result.content)
        self.assertIn("fixed", result.content)


if __name__ == "__main__":
    unittest.main()
