import unittest

from opengis_backend.agent.agent_loop import CodeExecResult
from opengis_backend.agent.workflow_loop import (
    WorkflowDocument,
    WorkflowLoop,
)


class WorkflowLoopFunctionCallTests(unittest.TestCase):
    def test_markdown_code_block_is_not_executed(self) -> None:
        responses = iter([
            "```python\nprint('should not run')\n```",
            "已完成：本步骤不需要执行代码。",
            "workflow summary",
        ])
        executed: list[str] = []

        def llm_call(_messages, **_kwargs):
            return next(responses)

        def executor_call(code: str) -> CodeExecResult:
            executed.append(code)
            return CodeExecResult(output="unexpected")

        workflow = WorkflowDocument.from_json({
            "name": "No CodeAct",
            "nodes": [
                {
                    "id": "n1",
                    "title": "Check",
                    "description": "Return a summary.",
                }
            ],
            "edges": [],
        })
        loop = WorkflowLoop(
            llm_call=llm_call,
            executor_call=executor_call,
            system_prompt="system",
            workflow=workflow,
        )

        result = loop.run("test")

        self.assertEqual(executed, [])
        self.assertEqual(result, "workflow summary")
        messages = loop.context.messages
        nudges = [
            message
            for message in messages
            if (message.get("_meta") or {}).get("kind") == "workflow_nudge"
        ]
        self.assertEqual(len(nudges), 1)
        self.assertIn("execute_code", nudges[0]["content"])


if __name__ == "__main__":
    unittest.main()
