import unittest
from tempfile import TemporaryDirectory

from opengis_backend.agent.context.context_projector import ContextProjector
from opengis_backend.agent.context.knowledge_extractor import KnowledgeExtractor
from opengis_backend.agent.telemetry.event_log import event_to_message_part
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType
from opengis_backend.agent.session.session_coordinator import SessionBusyError, SessionCoordinator
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

    def test_context_projector_allows_failure_lessons_for_short_map_request(self) -> None:
        with TemporaryDirectory() as tmp:
            MemoryStore(tmp).add(MemoryRecord.create(
                kind="failure_lesson",
                scope="tool_failure",
                title="set_categorized_style: color",
                content="Symptom: set_categorized_style produced white symbols. Verified fix path: use explicit hex colors.",
                tags=["failure", "set_categorized_style", "颜色"],
                source_run_id="run_color",
            ))
            MemoryStore(tmp).add(MemoryRecord.create(
                kind="recipe",
                scope="procedure",
                content="A long report workflow should not leak into simple layer styling.",
            ))

            projected = ContextProjector(tmp).project("把图层颜色分类一下")

            self.assertIn("learned failure lessons", projected)
            self.assertIn("set_categorized_style", projected)
            self.assertNotIn("report workflow", projected)

    def test_knowledge_extractor_stores_verified_failure_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_failure",
                prompt="修正 operation",
                workspace_path=tmp,
                model="test",
            )
            archive.record_tool_call(
                call_id="call_fail",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {}},
                output='{"success": false, "error": "KeyError: input_path"}',
                status="error",
            )
            archive.record_tool_call(
                call_id="call_edit",
                name="edit_operation",
                arguments={"operation_id": "dbscan_clustering", "old_string": "params['input_path']", "new_string": "params.get('input_path')"},
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true}',
                status="completed",
            )

            records = KnowledgeExtractor(tmp).extract_run(
                user_message="你要修正我的operation，而不是饶过他自己写脚本",
                final_answer="已修复并成功运行 dbscan_clustering operation。",
                run_archive=archive,
            )

            lessons = [record for record in records if record.kind == "failure_lesson"]
            self.assertEqual(len(lessons), 1)
            self.assertIn("KeyError", lessons[0].content)
            self.assertIn("edit_operation", lessons[0].content)
            self.assertEqual(lessons[0].metadata["target_id"], "dbscan_clustering")
            projected = ContextProjector(tmp).project("run_operation KeyError input_path dbscan_clustering")
            self.assertIn("dbscan_clustering", projected)

    def test_knowledge_extractor_stores_operation_and_artifact_records(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_operation_memory",
                prompt="创建并运行 operation",
                workspace_path=tmp,
                model="test",
            )
            archive.record_tool_call(
                call_id="call_create",
                name="create_operation",
                arguments={"operation_id": "gnnwr_model"},
                output='{"success": true, "operation_id": "gnnwr_model"}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_run",
                name="run_operation",
                arguments={"operation_id": "gnnwr_model", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )
            archive.record_artifact({
                "id": "artifact_1",
                "kind": "vector",
                "path": "/tmp/result.geojson",
                "title": "result.geojson",
            })

            records = KnowledgeExtractor(tmp).extract_run(
                user_message="把 GNNWR 沉淀为 operation 并运行",
                final_answer="已创建并运行 gnnwr_model，结果保存到 /tmp/result.geojson。",
                run_archive=archive,
            )

            operation_records = [r for r in records if r.scope == "operation"]
            artifact_records = [r for r in records if r.kind == "artifact"]
            self.assertEqual(len(operation_records), 1)
            self.assertIn("gnnwr_model", operation_records[0].content)
            self.assertEqual(len(artifact_records), 1)
            self.assertIn("/tmp/result.geojson", artifact_records[0].content)

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
