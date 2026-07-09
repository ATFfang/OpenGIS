import json
import tempfile
import unittest
from pathlib import Path

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True

if FASTAPI_AVAILABLE:
    from opengis_backend.rpc.handler import RpcHandler
else:
    RpcHandler = None
from opengis_backend.rpc.workflow_detection import detect_pasted_workflow_message, parse_pasted_workflow_message
from opengis_backend.tools.registry import ToolRegistry


class FakeWebSocket:
    async def send_text(self, data: str) -> None:
        pass


class WorkflowDetectionHelperTests(unittest.TestCase):
    def test_parse_pasted_workflow_json(self) -> None:
        workflow = {
            "name": "Pasted Workflow",
            "nodes": [{"id": "load", "title": "Load data"}],
            "edges": [],
        }

        parsed = parse_pasted_workflow_message(json.dumps(workflow))

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name, "Pasted Workflow")
        self.assertEqual(parsed.nodes[0].id, "load")

    def test_parse_fenced_pasted_workflow_json(self) -> None:
        raw = """```json
{"name":"Fenced Workflow","nodes":[{"id":"n1","label":"Step 1"}],"edges":[]}
```"""

        parsed = parse_pasted_workflow_message(raw)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name, "Fenced Workflow")

    def test_non_workflow_json_is_ignored(self) -> None:
        self.assertIsNone(parse_pasted_workflow_message(json.dumps({"nodes": "not-a-workflow"})))

    def test_detect_workflow_inside_mixed_user_context(self) -> None:
        workflow = {
            "name": "Mixed Workflow",
            "nodes": [{"id": "n1", "title": "Use attached data"}],
            "edges": [],
        }
        message = (
            "请执行这个 workflow，数据在 /tmp/drinks.csv，最后要展示图表。\n"
            f"```json\n{json.dumps(workflow, ensure_ascii=False)}\n```\n"
            "注意只输出一个最终报告。"
        )

        detected = detect_pasted_workflow_message(message)

        self.assertIsNotNone(detected)
        self.assertEqual(detected.workflow.name, "Mixed Workflow")
        self.assertIn("/tmp/drinks.csv", detected.context)
        self.assertIn("最终报告", detected.context)

    def test_detect_inline_workflow_object_inside_mixed_user_context(self) -> None:
        workflow = {
            "name": "Inline Workflow",
            "nodes": [{"id": "n1", "title": "Use data"}],
            "edges": [],
        }
        message = f"数据：/tmp/a.csv\nworkflow如下：{json.dumps(workflow, ensure_ascii=False)}\n请运行"

        detected = detect_pasted_workflow_message(message)

        self.assertIsNotNone(detected)
        self.assertEqual(detected.workflow.name, "Inline Workflow")
        self.assertIn("/tmp/a.csv", detected.context)


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not installed in this lightweight test environment")
class RpcWorkflowDetectionTests(unittest.TestCase):
    def test_pasted_workflow_json_routes_to_workflow_queue_item(self) -> None:
        workflow = {
            "name": "Pasted Workflow",
            "description": "Smoke test workflow",
            "nodes": [
                {
                    "id": "load",
                    "title": "Load data",
                    "description": "Load the input data.",
                }
            ],
            "edges": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            handler = RpcHandler(FakeWebSocket(), ToolRegistry())  # type: ignore[arg-type]
            queue_item, _profile, _session_store = handler._build_queue_item_from_chat_params({
                "message": f"数据在 /tmp/drinks.csv\n{json.dumps(workflow)}",
                "workspace_path": str(Path(tmp)),
                "conversation_id": "conv-test",
            })

        self.assertIsNotNone(queue_item.workflow)
        self.assertEqual(queue_item.workflow.name, "Pasted Workflow")
        self.assertTrue(queue_item.metadata["has_workflow"])
        self.assertIn("Execute the pasted OpenGIS workflow", queue_item.message)
        self.assertIn("/tmp/drinks.csv", queue_item.message)

    def test_non_workflow_json_stays_normal_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handler = RpcHandler(FakeWebSocket(), ToolRegistry())  # type: ignore[arg-type]
            queue_item, _profile, _session_store = handler._build_queue_item_from_chat_params({
                "message": json.dumps({"nodes": "not-a-workflow"}),
                "workspace_path": str(Path(tmp)),
                "conversation_id": "conv-test",
            })

        self.assertIsNone(queue_item.workflow)
        self.assertFalse(queue_item.metadata["has_workflow"])


if __name__ == "__main__":
    unittest.main()
