import unittest
from tempfile import TemporaryDirectory

from opengis_backend.agent.context_projector import ContextProjector
from opengis_backend.agent.event_log import event_to_message_part
from opengis_backend.agent.events import AgentEvent, AgentEventType
from opengis_backend.agent.session_coordinator import SessionBusyError, SessionCoordinator
from opengis_backend.runs.archive import RunArchive
from opengis_backend.workspace.memory_store import MemoryRecord, MemoryStore


class MemoryHarnessTests(unittest.TestCase):
    def test_memory_store_search_and_context_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.add(MemoryRecord.create(
                kind="dataset",
                scope="dataset",
                title="shops.csv",
                content="Dataset path: /workspace/shops.csv\nFields: name, price, district",
                tags=["csv", "poi"],
                source_run_id="run_a",
            ))

            hits = store.search("饮品店 shops price", limit=3)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].title, "shops.csv")

            projected = ContextProjector(tmp).project("继续分析 shops.csv 的 price 字段")
            self.assertIn("shops.csv", projected)
            self.assertIn("source=run_a", projected)

    def test_context_projector_hides_memory_for_short_map_request(self) -> None:
        with TemporaryDirectory() as tmp:
            MemoryStore(tmp).add(MemoryRecord.create(
                kind="recipe",
                scope="procedure",
                content="A long report workflow should not leak into simple layer styling.",
            ))

            projected = ContextProjector(tmp).project("把颜色分类一下")

            self.assertIn("intentionally hidden", projected)
            self.assertNotIn("report workflow", projected)

    def test_event_projection_and_run_archive_records_parts(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_test",
                prompt="hello",
                workspace_path=tmp,
                model="test",
            )
            event = AgentEvent(
                type=AgentEventType.TOOL_RESULT,
                data={
                    "name": "read_file",
                    "call_id": "call_1",
                    "run_id": "run_test",
                    "output": "ok",
                },
            )
            part = event_to_message_part(event)
            self.assertIsNotNone(part)
            archive.record_event(event.type.value, event.data)
            archive.record_message_part(part.to_dict())

            self.assertEqual(archive.read_events()[0]["type"], "tool_result")
            parts = archive.read_message_parts()
            self.assertEqual(parts[0]["type"], "tool")
            self.assertEqual(parts[0]["tool"], "read_file")

    def test_session_coordinator_rejects_concurrent_owner(self) -> None:
        key = "conversation-test"
        SessionCoordinator.release(key, "run_a")
        SessionCoordinator.release(key, "run_b")
        SessionCoordinator.acquire(key, "run_a")
        try:
            with self.assertRaises(SessionBusyError):
                SessionCoordinator.acquire(key, "run_b")
        finally:
            SessionCoordinator.release(key, "run_a")


if __name__ == "__main__":
    unittest.main()
