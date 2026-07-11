from __future__ import annotations

import unittest
from unittest.mock import patch

from opengis_backend.agent.execution.auto_install import auto_install_missing
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

    def test_execute_code_rejects_reserved_opengis_import(self) -> None:
        def executor(_code: str) -> _ExecResult:
            self.fail("executor must not run for reserved OpenGIS imports")

        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=executor,
        )

        result = runtime.execute("execute_code", {"code": "import opengis\nprint('x')"})

        self.assertTrue(result.ok)
        self.assertIn("reserved_platform_import", result.content)
        self.assertIn("registered OpenGIS function tool", result.content)

    def test_auto_install_skips_reserved_opengis_import(self) -> None:
        output: list[str] = []
        with patch(
            "opengis_backend.agent.execution.auto_install.find_missing_packages",
            return_value=["opengis"],
        ), patch("opengis_backend.agent.execution.auto_install.subprocess.Popen") as popen:
            installed = auto_install_missing(
                "import opengis",
                output_callback=output.append,
            )

        self.assertIsNone(installed)
        self.assertFalse(popen.called)
        self.assertIn("auto-install skipped", "".join(output))

    def test_tool_runtime_normalizes_alias_before_calling_tool(self) -> None:
        calls: list[dict] = []

        def add_raster(**kwargs):
            calls.append(kwargs)
            return {"success": True, "path": kwargs["path"]}

        runtime = ToolRuntime(
            tool_schemas=[_tool_schema("add_raster", {"path": "string", "opacity": "number"})],
            tool_callables={"add_raster": add_raster},
            executor_call=lambda _code: _ExecResult(output=""),
        )

        result = runtime.execute("add_raster", {"raster_path": "dem.tif", "opacity": "0.5"})

        self.assertTrue(result.ok, result.content)
        self.assertEqual(calls, [{"path": "dem.tif", "opacity": 0.5}])
        self.assertIn('"path": "dem.tif"', result.content)

    def test_tool_runtime_rejects_unknown_arguments_before_python_type_error(self) -> None:
        def add_raster(**_kwargs):
            self.fail("tool must not run with invalid arguments")

        runtime = ToolRuntime(
            tool_schemas=[_tool_schema("add_raster", {"path": "string"})],
            tool_callables={"add_raster": add_raster},
            executor_call=lambda _code: _ExecResult(output=""),
        )

        result = runtime.execute("add_raster", {"raster": "dem.tif"})
        payload = json_loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["error"], "invalid_tool_arguments")
        self.assertEqual(payload["accepted"], ["path"])
        self.assertEqual(payload["issues"][0]["code"], "unknown_arguments")

    def test_tool_runtime_reports_missing_required_arguments(self) -> None:
        runtime = ToolRuntime(
            tool_schemas=[_tool_schema("zoom_to_layer", {"layer_id": "string"}, required=["layer_id"])],
            tool_callables={"zoom_to_layer": lambda **_kwargs: {"success": True}},
            executor_call=lambda _code: _ExecResult(output=""),
        )

        result = runtime.execute("zoom_to_layer", {})
        payload = json_loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["issues"][0]["code"], "missing_required_arguments")
        self.assertEqual(payload["issues"][0]["fields"], ["layer_id"])

    def test_tool_runtime_normalizes_camel_case_and_coerces_arrays(self) -> None:
        calls: list[dict] = []

        def query_features(**kwargs):
            calls.append(kwargs)
            return {"success": True}

        runtime = ToolRuntime(
            tool_schemas=[_tool_schema("query_features", {"layer_id": "string", "bbox": "array"})],
            tool_callables={"query_features": query_features},
            executor_call=lambda _code: _ExecResult(output=""),
        )

        result = runtime.execute("query_features", {"layerId": "poi", "bbox": "[1, 2, 3, 4]"})

        self.assertTrue(result.ok, result.content)
        self.assertEqual(calls, [{"layer_id": "poi", "bbox": [1, 2, 3, 4]}])


def _tool_schema(name: str, properties: dict[str, str], required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {
                "type": "object",
                "properties": {
                    key: {"type": type_name, "description": key}
                    for key, type_name in properties.items()
                },
                "required": required if required is not None else list(properties.keys()),
            },
        },
    }


def json_loads(text: str) -> dict:
    import json

    value = json.loads(text)
    assert isinstance(value, dict)
    return value


if __name__ == "__main__":
    unittest.main()
