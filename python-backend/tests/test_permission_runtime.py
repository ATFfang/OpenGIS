import unittest
from unittest.mock import patch

from opengis_backend.agent.auto_install import auto_install_missing
from opengis_backend.agent.permission import (
    PermissionAction,
    PermissionDecision,
    PermissionRuntime,
)
from opengis_backend.agent.profile import AgentProfile
from opengis_backend.agent.tool_runtime import ToolRuntime


class PermissionRuntimeTests(unittest.TestCase):
    def test_delete_file_requires_approval_by_default(self) -> None:
        calls: list[tuple[str, dict, PermissionDecision]] = []

        def approval(tool_name: str, args: dict, decision: PermissionDecision) -> PermissionDecision:
            calls.append((tool_name, args, decision))
            return PermissionDecision(
                PermissionAction.DENY,
                "Denied in test.",
                decision.rule,
            )

        runtime = PermissionRuntime.from_profile(AgentProfile.gis_build())
        runtime.approval_callback = approval
        tool_called = False

        def delete_file(**_kwargs):
            nonlocal tool_called
            tool_called = True
            return {"success": True}

        tool_runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={"delete_file": delete_file},
            executor_call=lambda _code: None,
            permission_runtime=runtime,
        )

        result = tool_runtime.execute("delete_file", {"path": "article.pdf"})

        self.assertFalse(result.ok)
        self.assertFalse(tool_called)
        self.assertEqual(calls[0][0], "delete_file")
        self.assertEqual(calls[0][2].action, PermissionAction.ASK)

    def test_auto_install_requires_execute_code_approval(self) -> None:
        calls: list[tuple[str, dict, PermissionDecision]] = []

        def approval(tool_name: str, args: dict, decision: PermissionDecision) -> PermissionDecision:
            calls.append((tool_name, args, decision))
            return PermissionDecision(
                PermissionAction.DENY,
                "Denied package install.",
                decision.rule,
            )

        runtime = PermissionRuntime.from_profile(AgentProfile.gis_build())
        runtime.approval_callback = approval

        with patch(
            "opengis_backend.agent.auto_install.find_missing_packages",
            return_value=["humanize"],
        ), patch("opengis_backend.agent.auto_install.subprocess.run") as run:
            installed = auto_install_missing(
                "import humanize",
                permission_runtime=runtime,
            )

        self.assertIsNone(installed)
        self.assertFalse(run.called)
        self.assertEqual(calls[0][0], "execute_code")
        self.assertIn("pip install", calls[0][1]["code"])

    def test_execute_code_delete_patterns_require_approval(self) -> None:
        calls: list[tuple[str, dict, PermissionDecision]] = []

        def approval(tool_name: str, args: dict, decision: PermissionDecision) -> PermissionDecision:
            calls.append((tool_name, args, decision))
            return PermissionDecision(
                PermissionAction.DENY,
                "Denied destructive code.",
                decision.rule,
            )

        runtime = PermissionRuntime.from_profile(AgentProfile.gis_build())
        runtime.approval_callback = approval
        tool_runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=lambda _code: self.fail("executor must not run"),
            permission_runtime=runtime,
        )

        result = tool_runtime.execute("execute_code", {"code": "delete_file('article.pdf')"})

        self.assertFalse(result.ok)
        self.assertEqual(calls[0][0], "execute_code")
        self.assertEqual(calls[0][2].rule, "risk:destructive_python")


if __name__ == "__main__":
    unittest.main()
