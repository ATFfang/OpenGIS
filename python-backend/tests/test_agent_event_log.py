import unittest

from opengis_backend.agent.telemetry.event_log import AgentEventLog
from opengis_backend.agent.telemetry.event_log import event_to_message_part
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType


class FakeArchive:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.parts: list[dict] = []

    def record_event(self, event_type: str, data) -> None:
        self.events.append((event_type, data))

    def record_message_part(self, part: dict) -> None:
        self.parts.append(part)


class AgentEventLogTests(unittest.TestCase):
    def test_append_records_event_and_projected_message_part_with_run_id(self) -> None:
        archive = FakeArchive()
        log = AgentEventLog(archive, run_id="run_123")

        part = log.append(AgentEvent(AgentEventType.TOOL_START, {"name": "read_file", "call_id": "call_1"}))

        self.assertIsNotNone(part)
        self.assertEqual(archive.events[0][0], "tool_start")
        self.assertEqual(archive.events[0][1]["run_id"], "run_123")
        self.assertEqual(archive.parts[0]["type"], "tool")
        self.assertEqual(archive.parts[0]["tool"], "read_file")
        self.assertEqual(archive.parts[0]["call_id"], "call_1")
        self.assertEqual(archive.parts[0]["run_id"], "run_123")

    def test_projection_uses_stable_ids_for_same_tool_call(self) -> None:
        start = event_to_message_part(
            AgentEvent(AgentEventType.TOOL_START, {"run_id": "run_123", "name": "read_file", "call_id": "call_1"})
        )
        result = event_to_message_part(
            AgentEvent(AgentEventType.TOOL_RESULT, {"run_id": "run_123", "name": "read_file", "call_id": "call_1"})
        )

        self.assertIsNotNone(start)
        self.assertIsNotNone(result)
        self.assertEqual(start.id, "run_123:tool:call_1")
        self.assertEqual(result.id, "run_123:tool:call_1")

    def test_stream_delta_dict_projects_stable_text_part(self) -> None:
        event = AgentEvent(AgentEventType.STREAM_DELTA, {"content": "hello", "run_id": "run_123"})

        part = event_to_message_part(event)

        self.assertIsNotNone(part)
        self.assertEqual(part.id, "run_123:text:final")
        self.assertEqual(part.text, "hello")

    def test_progress_projects_to_renderable_message_part(self) -> None:
        event = AgentEvent(
            AgentEventType.PROGRESS,
            {"stage": "calling_llm", "message": "Thinking...", "run_id": "run_123"},
        )

        part = event_to_message_part(event)

        self.assertIsNotNone(part)
        self.assertEqual(part.id, "run_123:progress:live")
        self.assertEqual(part.type, "progress")
        self.assertEqual(part.status, "running")
        self.assertEqual(part.data["stage"], "calling_llm")
        self.assertEqual(part.data["message"], "Thinking...")

    def test_code_block_and_result_project_to_renderable_parts(self) -> None:
        code = event_to_message_part(
            AgentEvent(
                AgentEventType.CODE_BLOCK,
                {"run_id": "run_123", "step": 4, "code": "print(1)", "script_path": "script/a.py"},
            )
        )
        result = event_to_message_part(
            AgentEvent(
                AgentEventType.CODE_RESULT,
                {"run_id": "run_123", "step": 4, "output": "1", "duration_ms": 12},
            )
        )

        self.assertIsNotNone(code)
        self.assertIsNotNone(result)
        self.assertEqual(code.id, "run_123:code:4")
        self.assertEqual(code.text, "print(1)")
        self.assertEqual(code.data["scriptPath"], "script/a.py")
        self.assertEqual(result.id, "run_123:code-result:4")
        self.assertEqual(result.type, "tool_output")
        self.assertEqual(result.tool, "execute_code")
        self.assertEqual(result.text, "1")


if __name__ == "__main__":
    unittest.main()
