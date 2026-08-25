import unittest
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path

from opengis_backend.agent.context.context_projector import ContextProjector
from opengis_backend.agent.context.context_store import AgentContextStore
from opengis_backend.agent.context.knowledge_extractor import KnowledgeExtractor
from opengis_backend.agent.telemetry.event_log import event_to_message_part
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType
from opengis_backend.agent.session.session_coordinator import SessionBusyError, SessionCoordinator
from opengis_backend.operations import OperationStore
from opengis_backend.runs.archive import RunArchive
from opengis_backend.skills.discovery import UserSkillDiscovery
from opengis_backend.tools.builtin.operation_tools import list_operations as list_operations_tool
from opengis_backend.tools.context import ToolContext
from opengis_backend.workspace.evolution_eval import PromotionGate, ReplayEvaluator
from opengis_backend.workspace.evolution_extractor import EvolutionExtractor
from opengis_backend.workspace.evolution_holdout import HoldoutSplitter
from opengis_backend.workspace.evolution_materializer import EvolutionMaterializer
from opengis_backend.workspace.evolution_materializer import EvolutionMaterialization
from opengis_backend.workspace.evolution_asset_registry import EvolutionAssetRegistry
from opengis_backend.workspace.evolution_mining import WeaknessMiner
from opengis_backend.workspace.evolution_pipeline import EvolutionPipeline
from opengis_backend.workspace.evolution_replay import ReplaySuite
from opengis_backend.workspace.evolution_replay import ReplayCase
from opengis_backend.workspace.evolution_report import EvolutionReporter
from opengis_backend.workspace.evolution_store import EvolutionCandidate, EvolutionStore
from opengis_backend.workspace.evolution_trace import EvolutionRunTrace, EvolutionTraceStore
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

            self.assertEqual(projected, "")

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

            projected = ContextProjector(tmp).project("把图层颜色分类一下，之前那个报错怎么修")

            self.assertIn("Use these retrieved project memories", projected)
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

    def test_evolution_extractor_stores_failure_and_recipe_candidates(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_evolution",
                prompt="修正 operation 并形成复用经验",
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
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            candidates = EvolutionExtractor(tmp).extract_run(
                user_message="你要修正我的operation，而不是绕过去自己写脚本",
                final_answer="已修复并成功运行 dbscan_clustering operation。",
                run_archive=archive,
            )

            kinds = {candidate.kind for candidate in candidates}
            self.assertIn("failure_repair", kinds)
            self.assertIn("recipe", kinds)
            self.assertIn("operation_candidate", kinds)

            stored = EvolutionStore(tmp).list(limit=10)
            self.assertGreaterEqual(len(stored), 3)
            self.assertTrue(any(candidate.kind == "failure_repair" for candidate in stored))
            self.assertTrue(any("dbscan_clustering" in candidate.summary or "dbscan_clustering" in candidate.title for candidate in stored))

    def test_evolution_extractor_does_not_promote_plain_long_tool_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_plain_long_chain",
                prompt="把地图缩放到目标图层",
                workspace_path=tmp,
                model="test",
            )
            for index, name in enumerate([
                "list_layers",
                "get_layer",
                "query_features",
                "list_layers",
                "get_map_state",
                "zoom_to_layer",
            ]):
                archive.record_tool_call(
                    call_id=f"call_{index}",
                    name=name,
                    arguments={},
                    output='{"success": true}',
                    status="completed",
                )

            candidates = EvolutionExtractor(tmp).extract_run(
                user_message="把地图缩放到目标图层",
                final_answer="已缩放到目标图层。",
                run_archive=archive,
            )

            self.assertFalse(any(candidate.kind == "recipe" for candidate in candidates))

    def test_platform_tool_repair_materializes_as_skill(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_platform_repair",
                prompt="修复 edit_file",
                workspace_path=tmp,
                model="test",
            )
            archive.record_step(
                step=1,
                code="print('temporary repair')",
                script_path="script/temporary_repair.py",
            )
            candidate = EvolutionCandidate.create(
                kind="failure_repair",
                scope="tool_failure",
                title="edit_file repair candidate",
                summary="Repair the edit_file retry contract.",
                proposed_change="Read the file again before applying the edit.",
                target_component="edit_file",
                evidence=["edit_file failed because the file changed after read."],
                tags=["failure", "edit_file"],
                source_run_id=archive.run_id,
                confidence=0.9,
                status="promoted",
            )

            materialization = EvolutionMaterializer(tmp).materialize(
                candidate,
                run_archive=archive,
                force=True,
            )

            self.assertIsNotNone(materialization)
            self.assertEqual(materialization.target_kind, "skill")
            self.assertFalse((Path(tmp) / ".opengis" / "operations" / "edit_file").exists())

    def test_invalid_operation_materialization_is_not_publishable(self) -> None:
        invalid = EvolutionMaterialization(
            id="invalid-materialization",
            candidate_id="candidate",
            candidate_kind="failure_repair",
            target_kind="operation",
            materialization_mode="promote_script",
            asset_id="broken-operation",
            asset_name="Broken operation",
            asset_path="/tmp/broken-operation",
            status="failed",
            source_run_id="run",
            validation={"ok": False, "errors": [{"code": "missing_standard_cli_flag"}]},
        )

        self.assertFalse(EvolutionMaterializer.is_publishable(invalid))

    def test_replay_evaluator_scores_and_gate_promotes_failure_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            prior = RunArchive.open(
                run_id="run_evolution_eval_prior",
                prompt="修正 operation",
                workspace_path=tmp,
                model="test",
            )
            prior.record_tool_call(
                call_id="prior_fail",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {}},
                output='{"success": false, "error": "KeyError: input_path"}',
                status="error",
            )
            prior.record_tool_call(
                call_id="prior_edit",
                name="edit_operation",
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            prior.record_tool_call(
                call_id="prior_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )
            archive = RunArchive.open(
                run_id="run_evolution_eval",
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
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            candidates = EvolutionExtractor(tmp).extract_run(
                user_message="修正 operation",
                final_answer="已修复 dbscan_clustering。",
                run_archive=archive,
            )
            candidate = next(item for item in candidates if item.kind == "failure_repair")
            evaluation = ReplayEvaluator(tmp).evaluate(candidate)

            self.assertEqual(evaluation.decision, "promote")
            self.assertGreaterEqual(evaluation.score, 0.75)
            self.assertTrue(PromotionGate().should_promote(evaluation))
            self.assertGreaterEqual(evaluation.metrics.get("replay_case_count", 0), 1)
            self.assertGreaterEqual(evaluation.metrics.get("held_out_count", 0), 1)

    def test_replay_suite_materializes_cases_from_historical_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_replay_suite",
                prompt="修正 operation 并沉淀经验",
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
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            candidate = EvolutionExtractor(tmp).extract_run(
                user_message="修正 operation 并沉淀经验",
                final_answer="已修复 dbscan_clustering。",
                run_archive=archive,
            )[0]
            cases = ReplaySuite(tmp).build(candidate)

            self.assertGreaterEqual(len(cases), 1)
            self.assertTrue(all(case.candidate_id == candidate.id for case in cases))
            self.assertTrue(any(case.source_run_id == "run_replay_suite" for case in cases))

    def test_post_run_pipeline_updates_candidate_status(self) -> None:
        with TemporaryDirectory() as tmp:
            prior = RunArchive.open(
                run_id="run_post_run_pipeline_prior",
                prompt="修正 operation 并沉淀经验",
                workspace_path=tmp,
                model="test",
            )
            prior.record_tool_call(
                call_id="prior_fail",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {}},
                output='{"success": false, "error": "KeyError: input_path"}',
                status="error",
            )
            prior.record_tool_call(
                call_id="prior_edit",
                name="edit_operation",
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            prior.record_tool_call(
                call_id="prior_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )
            archive = RunArchive.open(
                run_id="run_post_run_pipeline",
                prompt="修正 operation 并沉淀经验",
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
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            AgentContextStore().extract_knowledge_after_run(
                workspace=tmp,
                user_message="修正 operation 并沉淀经验",
                final_answer="已修复 dbscan_clustering。",
                run_archive=archive,
                workflow=None,
            )

            candidates = EvolutionStore(tmp).list(limit=10)
            self.assertTrue(any(candidate.status in {"promoted", "published"} for candidate in candidates))
            self.assertTrue(any(candidate.metadata.get("last_evaluation_id") for candidate in candidates))
            self.assertTrue(any(candidate.metadata.get("materialized_id") for candidate in candidates))

    def test_generic_recipe_without_reuse_signal_is_not_promoted(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_generic_recipe_gate",
                prompt="生成一个空间统计结果",
                workspace_path=tmp,
                model="test",
            )
            for index, name in enumerate(["read_file", "execute_code", "write_file"], start=1):
                archive.record_tool_call(
                    call_id=f"call_{index}",
                    name=name,
                    arguments={},
                    output='{"success": true}',
                    status="completed",
                )
            archive.record_artifact({
                "id": "artifact_generic",
                "kind": "json",
                "path": "/tmp/result.json",
                "title": "result.json",
            })
            candidate = next(
                item for item in EvolutionExtractor(tmp).extract_run(
                    user_message="生成一个空间统计结果",
                    final_answer="已生成 result.json。",
                    run_archive=archive,
                )
                if item.kind == "recipe"
            )

            evaluation = ReplayEvaluator(tmp).evaluate(candidate)

            self.assertEqual(evaluation.decision, "review")
            self.assertIn("generic recipe", " ".join(evaluation.risks))
            self.assertFalse(PromotionGate().should_promote(evaluation))

    def test_evolution_report_summarizes_current_state(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_evolution_report",
                prompt="修正 operation 并沉淀经验",
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
                arguments={
                    "operation_id": "dbscan_clustering",
                    "old_string": "params['input_path']",
                    "new_string": "params.get('input_path')",
                },
                output='{"success": true}',
                status="completed",
            )
            archive.record_tool_call(
                call_id="call_ok",
                name="run_operation",
                arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            AgentContextStore().extract_knowledge_after_run(
                workspace=tmp,
                user_message="修正 operation 并沉淀经验",
                final_answer="已修复 dbscan_clustering。",
                run_archive=archive,
                workflow=None,
            )

            report = EvolutionReporter(tmp).build(limit=5)
            payload = report.to_dict()
            self.assertGreaterEqual(payload["candidate_count"], 1)
            self.assertGreaterEqual(payload["evaluation_count"], 1)
            self.assertGreaterEqual(payload["replay_case_count"], 1)
            self.assertIn("failure_repair", payload["candidate_kind_counts"])
            self.assertTrue(payload["recent_evaluations"])

    def test_evolution_report_includes_trace_trend_and_health_state(self) -> None:
        with TemporaryDirectory() as tmp:
            trace_store = EvolutionTraceStore(tmp)
            trace_store.record(EvolutionRunTrace(
                run_id="run_trace_closed",
                workspace_path=tmp,
                candidate_count=2,
                weakness_cluster_count=1,
                weakness_candidate_count=1,
                evaluation_attempt_count=2,
                evaluation_error_count=0,
                promoted_count=1,
                rejected_count=0,
                materialized_count=1,
                materialization_error_count=0,
                stage_statuses={"extract": "ok", "evaluate": "ok", "materialize": "ok"},
                stage_durations_ms={"extract_ms": 12.0, "evaluate_ms": 18.0, "materialize_ms": 4.0},
                candidate_ids=["c1", "c2"],
                issues=[],
                metadata={"closed_loop": True},
            ))
            trace_store.record(EvolutionRunTrace(
                run_id="run_trace_failed",
                workspace_path=tmp,
                candidate_count=1,
                weakness_cluster_count=1,
                weakness_candidate_count=0,
                evaluation_attempt_count=1,
                evaluation_error_count=1,
                promoted_count=0,
                rejected_count=0,
                materialized_count=0,
                materialization_error_count=0,
                stage_statuses={"extract": "ok", "evaluate": "failed", "materialize": "skipped"},
                stage_durations_ms={"extract_ms": 8.0, "evaluate_ms": 20.0, "materialize_ms": 0.0},
                candidate_ids=["c3"],
                issues=["evaluation failed"],
                metadata={"closed_loop": False},
            ))

            report = EvolutionReporter(tmp).build(limit=5)
            payload = report.to_dict()

            self.assertEqual(payload["trace_count"], 2)
            self.assertIn("series", payload["trace_trend"])
            self.assertEqual(len(payload["trace_trend"]["series"]), 2)
            self.assertIn("recent_trace_status_counts", payload["chain_health"])
            self.assertGreaterEqual(payload["chain_health"]["recent_trace_issue_count"], 1)
            self.assertIn("efficiency_metrics", payload)
            self.assertAlmostEqual(payload["efficiency_metrics"]["closed_loop_rate"], 0.5, places=2)
            self.assertIn("promotion_rate", payload["efficiency_metrics"])
            self.assertIn("avg_time_to_promotion_ms", payload["efficiency_metrics"])
            self.assertIn(payload["health_state"], {"healthy", "degraded", "critical", "idle"})
            self.assertTrue(payload["health_summary"])

    def test_evolution_materializer_writes_operation_and_skill_assets(self) -> None:
        from pathlib import Path
        from opengis_backend.workspace.evolution_store import EvolutionCandidate

        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(
                run_id="run_materialize_operation",
                prompt="运行 reusable operation 并沉淀经验",
                workspace_path=tmp,
                model="test",
            )
            archive.record_tool_call(
                call_id="call_run",
                name="run_operation",
                arguments={"operation_id": "kernel_density", "params": {"input_path": "shops.csv"}},
                output='{"success": true, "output_path": "/tmp/result.geojson"}',
                status="completed",
            )

            candidate = next(
                item for item in EvolutionExtractor(tmp).extract_run(
                    user_message="运行 kernel_density 并沉淀经验",
                    final_answer="已成功运行 kernel_density。",
                    run_archive=archive,
                )
                if item.kind == "operation_candidate"
            )
            materialization = EvolutionMaterializer(tmp).materialize(candidate, run_archive=archive, force=True)

            self.assertIsNotNone(materialization)
            self.assertEqual(materialization.target_kind, "operation")
            self.assertTrue(materialization.asset_path)
            self.assertTrue(Path(materialization.asset_path).exists())

            report = EvolutionReporter(tmp).build(limit=5)
            self.assertGreaterEqual(report.materialization_count, 1)
            self.assertTrue(any(item["target_kind"] == "operation" for item in report.recent_materializations))

        with TemporaryDirectory() as tmp2:
            promoted = EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title="Curated analysis recipe",
                summary="A reusable analysis instruction bundle.",
                proposed_change="Use this workflow when a recipe should be shared as guidance.",
                target_component="analysis",
                evidence=["Tool sequence: list_layers -> query_features"],
                tags=["recipe", "analysis"],
                source_run_id="run_recipe",
                confidence=0.9,
                status="promoted",
            )
            EvolutionStore(tmp2).add(promoted)
            materialization = EvolutionMaterializer(tmp2).materialize(promoted, force=True)

            self.assertIsNotNone(materialization)
            self.assertEqual(materialization.target_kind, "skill")
            self.assertTrue(Path(materialization.asset_path).joinpath("SKILL.md").exists())

    def test_materializer_reuses_existing_asset_record(self) -> None:
        with TemporaryDirectory() as tmp:
            first = EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title="Shared recipe asset",
                summary="A reusable instruction bundle.",
                proposed_change="Use this workflow when repeated.",
                target_component="analysis",
                evidence=["Tool sequence: read_file -> execute_code"],
                tags=["recipe"],
                source_run_id="run_a",
                confidence=0.9,
                status="promoted",
            )
            second = EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title="Shared recipe asset",
                summary="A reusable instruction bundle from another run.",
                proposed_change="Use this workflow when repeated.",
                target_component="analysis",
                evidence=["Tool sequence: read_file -> execute_code"],
                tags=["recipe"],
                source_run_id="run_b",
                confidence=0.9,
                status="promoted",
            )

            first_materialization = EvolutionMaterializer(tmp).materialize(first, force=True)
            second_materialization = EvolutionMaterializer(tmp).materialize(second, force=True)

            self.assertIsNotNone(first_materialization)
            self.assertIsNotNone(second_materialization)
            assert first_materialization is not None
            assert second_materialization is not None
            self.assertEqual(first_materialization.id, second_materialization.id)
            self.assertEqual(len(EvolutionMaterializer(tmp).list(limit=10)), 1)

    def test_harness_candidates_materialize_as_gated_prompt_and_tool_policy_assets(self) -> None:
        with TemporaryDirectory() as tmp:
            prompt_candidate = EvolutionCandidate.create(
                kind="weakness_cluster",
                scope="harness",
                title="Style color drift repair",
                summary="Repeated user corrections show categorized styling may produce white symbols.",
                proposed_change="For matching style turns, require explicit hex category colors and verify style state.",
                target_component="capability_router",
                evidence=["User corrected: agent said purple but layer rendered white."],
                tags=["style", "颜色", "categorized"],
                source_run_id="run_prompt_patch",
                confidence=0.92,
                status="promoted",
                metadata={"weakness_support_count": 3},
            )
            policy_candidate = EvolutionCandidate.create(
                kind="tool_policy",
                scope="harness",
                title="Prefer style tools over Python",
                summary="Layer styling should route to style tools instead of ad hoc Python scripts.",
                proposed_change="For layer style changes, select map/style packs and prefer set_categorized_style/update_layer_style.",
                target_component="tool routing",
                evidence=["Agent repeatedly used Python to change map style."],
                tags=["tool_policy", "style", "tool routing"],
                source_run_id="run_tool_policy",
                confidence=0.9,
                status="promoted",
                metadata={"weakness_support_count": 2},
            )

            prompt_materialization = EvolutionMaterializer(tmp).materialize(prompt_candidate, force=True)
            policy_materialization = EvolutionMaterializer(tmp).materialize(policy_candidate, force=True)

            self.assertIsNotNone(prompt_materialization)
            self.assertIsNotNone(policy_materialization)
            assert prompt_materialization is not None
            assert policy_materialization is not None
            self.assertEqual(prompt_materialization.target_kind, "prompt_patch")
            self.assertEqual(policy_materialization.target_kind, "tool_policy")
            self.assertTrue(Path(prompt_materialization.asset_path).joinpath("PATCH.md").exists())
            self.assertTrue(Path(policy_materialization.asset_path).joinpath("POLICY.md").exists())

            registry = EvolutionAssetRegistry(tmp)
            prompt_asset = registry.find("prompt_patch", prompt_materialization.asset_id)
            policy_asset = registry.find("tool_policy", policy_materialization.asset_id)
            self.assertIsNotNone(prompt_asset)
            self.assertIsNotNone(policy_asset)
            assert prompt_asset is not None
            assert policy_asset is not None
            self.assertEqual(prompt_asset.state, "draft")
            self.assertEqual(policy_asset.state, "draft")
            self.assertTrue(registry.inspect(prompt_asset)["loadable"])
            self.assertTrue(registry.inspect(policy_asset)["loadable"])

    def test_evolution_asset_registry_controls_visibility(self) -> None:
        with TemporaryDirectory() as tmp:
            skill_candidate = EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title="Visible only after enable",
                summary="A reusable skill promoted from a successful run.",
                proposed_change="Use when this procedure should become a reusable skill.",
                target_component="analysis",
                evidence=["Tool sequence: list_layers -> query_features"],
                tags=["recipe", "analysis"],
                source_run_id="run_skill_asset",
                confidence=0.9,
                status="promoted",
            )
            EvolutionStore(tmp).add(skill_candidate)
            skill_materialization = EvolutionMaterializer(tmp).materialize(skill_candidate, force=True)
            self.assertIsNotNone(skill_materialization)

            discovery = UserSkillDiscovery(workspace_path=tmp)
            self.assertFalse(any(info.name == skill_materialization.asset_id for info in discovery.list()))

            skill_asset = EvolutionAssetRegistry(tmp).find("skill", skill_materialization.asset_id)
            self.assertIsNotNone(skill_asset)
            if skill_asset is not None:
                EvolutionAssetRegistry(tmp).set_state(skill_asset.id, "enabled")
            self.assertTrue(any(info.name == skill_materialization.asset_id for info in UserSkillDiscovery(workspace_path=tmp).list()))
            if skill_asset is not None:
                registry = EvolutionAssetRegistry(tmp)
                history = registry.history(skill_asset.id)
                self.assertGreaterEqual(len(history), 2)
                rolled_back = registry.rollback(skill_asset.id)
                self.assertEqual(rolled_back.state, "draft")
                self.assertGreaterEqual(rolled_back.revision, 3)
                self.assertFalse(any(info.name == skill_materialization.asset_id for info in UserSkillDiscovery(workspace_path=tmp).list()))

            store = EvolutionStore(tmp)
            store.add(EvolutionCandidate.create(
                kind="failure_repair",
                scope="project",
                title="operation visibility",
                summary="A reusable operation promoted from a repeated repair pattern.",
                proposed_change="Use when this operation should be published.",
                target_component="map",
                evidence=["Repeated run_operation repair"],
                tags=["operation", "visibility"],
                source_run_id="run_operation_asset",
                confidence=0.95,
                status="promoted",
                metadata={"operation_ids": ["visible_operation_asset"]},
            ))
            op_store = OperationStore.from_workspace(tmp)
            op_store.create(
                operation_id="visible_operation_asset",
                name="Visible Operation Asset",
                description="A test operation asset.",
                code=(
                    "from pathlib import Path\n"
                    "import json\n"
                    "import sys\n"
                    "\n"
                    "def main():\n"
                    "    input_path = None\n"
                    "    output_path = None\n"
                    "    args = sys.argv[1:]\n"
                    "    for i, arg in enumerate(args):\n"
                    "        if arg == '--input' and i + 1 < len(args):\n"
                    "            input_path = args[i + 1]\n"
                    "        if arg == '--output' and i + 1 < len(args):\n"
                    "            output_path = args[i + 1]\n"
                    "    Path(output_path).write_text(json.dumps({'success': True, 'input_path': input_path}), encoding='utf-8')\n"
                    "\n"
                    "if __name__ == '__main__':\n"
                    "    main()\n"
                ),
                overwrite=True,
            )
            op_asset = EvolutionAssetRegistry(tmp).upsert_from_materialization(
                type("M", (), {
                    "target_kind": "operation",
                    "asset_id": "visible_operation_asset",
                    "asset_path": str(op_store.root / "visible_operation_asset"),
                    "candidate_id": "candidate-op",
                    "id": "materialization-op",
                    "source_run_id": "run_operation_asset",
                    "asset_name": "Visible Operation Asset",
                    "materialization_mode": "copy_operation",
                    "status": "draft",
                })(),
                default_state="draft",
            )
            self.assertIsNotNone(op_asset)
            ctx = ToolContext(meta={"workspace_path": tmp})
            listed = list_operations_tool.__wrapped__(ctx, query="visible", limit=20)
            self.assertFalse(any(item["id"] == "visible_operation_asset" for item in listed["operations"]))
            if op_asset is not None:
                EvolutionAssetRegistry(tmp).set_state(op_asset.id, "enabled")
            listed = list_operations_tool.__wrapped__(ctx, query="visible", limit=20)
            self.assertTrue(any(item["id"] == "visible_operation_asset" for item in listed["operations"]))
            if op_asset is not None:
                shutil.rmtree(op_store.root / "visible_operation_asset")
                listed = list_operations_tool.__wrapped__(ctx, query="visible", limit=20)
                self.assertFalse(any(item["id"] == "visible_operation_asset" for item in listed["operations"]))
                missing_asset = EvolutionAssetRegistry(tmp).upsert_from_materialization(
                    type("M", (), {
                        "target_kind": "operation",
                        "asset_id": "missing_operation_asset",
                        "asset_path": f"{tmp}/.opengis/operations/missing_operation_asset",
                        "candidate_id": "candidate-missing-op",
                        "id": "materialization-missing-op",
                        "source_run_id": "run_missing_operation_asset",
                        "asset_name": "Missing Operation Asset",
                        "materialization_mode": "copy_operation",
                        "status": "published",
                    })(),
                    default_state="enabled",
                )
                self.assertIsNotNone(missing_asset)
                listed = list_operations_tool.__wrapped__(ctx, query="missing", limit=20)
                self.assertFalse(any(item["id"] == "missing_operation_asset" for item in listed["operations"]))

    def test_evolution_registry_archives_unloadable_assets(self) -> None:
        with TemporaryDirectory() as tmp:
            candidate = EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title="Broken skill asset",
                summary="An asset that should be archived when unloadable.",
                proposed_change="Use when testing archive flow.",
                target_component="analysis",
                evidence=["Archive test"],
                tags=["archive"],
                source_run_id="run_archive_asset",
                confidence=0.8,
                status="promoted",
            )
            EvolutionStore(tmp).add(candidate)
            materialization = EvolutionMaterializer(tmp).materialize(candidate, force=True)
            self.assertIsNotNone(materialization)
            skill_path = Path(materialization.asset_path)
            self.assertTrue(skill_path.exists())
            shutil.rmtree(skill_path)

            registry = EvolutionAssetRegistry(tmp)
            asset = registry.find("skill", materialization.asset_id)
            self.assertIsNotNone(asset)
            archived = registry.archive_unloadable(reason="test archive unloadable")
            self.assertTrue(any(item.id == asset.id for item in archived))
            refreshed = registry.find("skill", materialization.asset_id)
            self.assertIsNotNone(refreshed)
            if refreshed is not None:
                self.assertEqual(refreshed.state, "archived")

    def test_weakness_miner_clusters_repeated_corrections(self) -> None:
        with TemporaryDirectory() as tmp:
            first = RunArchive.open(
                run_id="run_weakness_a",
                prompt="修正 operation",
                workspace_path=tmp,
                model="test",
            )
            second = RunArchive.open(
                run_id="run_weakness_b",
                prompt="再次修正 operation",
                workspace_path=tmp,
                model="test",
            )
            for archive in (first, second):
                archive.record_tool_call(
                    call_id=f"{archive.run_id}_fail",
                    name="run_operation",
                    arguments={"operation_id": "dbscan_clustering", "params": {}},
                    output='{"success": false, "error": "KeyError: input_path"}',
                    status="error",
                )
                archive.record_tool_call(
                    call_id=f"{archive.run_id}_edit",
                    name="edit_operation",
                    arguments={
                        "operation_id": "dbscan_clustering",
                        "old_string": "params['input_path']",
                        "new_string": "params.get('input_path')",
                    },
                    output='{"success": true}',
                    status="completed",
                )
                archive.record_tool_call(
                    call_id=f"{archive.run_id}_ok",
                    name="run_operation",
                    arguments={"operation_id": "dbscan_clustering", "params": {"input_path": "shops.csv"}},
                    output='{"success": true}',
                    status="completed",
                )

            clusters = WeaknessMiner(tmp).mine_for_run(second)

            self.assertTrue(any(cluster.support_count >= 2 for cluster in clusters))
            self.assertTrue(any("run_operation" in cluster.key or "edit_operation" in cluster.key for cluster in clusters))

            result = EvolutionPipeline(tmp).process_run(
                user_message="再次修正 operation",
                final_answer="已修复 dbscan_clustering。",
                run_archive=second,
            )
            stored = EvolutionStore(tmp).list(limit=20)

            self.assertGreaterEqual(result.candidate_count, 1)
            self.assertTrue(any(candidate.kind == "weakness_cluster" for candidate in stored))
            self.assertTrue(any(candidate.metadata.get("weakness_support_count", 0) >= 2 for candidate in stored))

    def test_holdout_splitter_detects_regression_risk(self) -> None:
        cases = [
            ReplayCase.create(
                candidate_id="c",
                candidate_kind="weakness_cluster",
                source_run_id="run_a",
                prompt="a",
                expected_signal="ok",
                score=0.9,
                status="materialized",
            ),
            ReplayCase.create(
                candidate_id="c",
                candidate_kind="weakness_cluster",
                source_run_id="run_b",
                prompt="b",
                expected_signal="ok",
                score=0.88,
                status="materialized",
            ),
            ReplayCase.create(
                candidate_id="c",
                candidate_kind="weakness_cluster",
                source_run_id="run_c",
                prompt="c",
                expected_signal="ok",
                score=0.42,
                status="materialized",
            ),
        ]

        summary = HoldoutSplitter().summarize(cases)

        self.assertGreaterEqual(summary.held_in_count, 1)
        self.assertGreaterEqual(summary.held_out_count, 1)
        self.assertTrue(summary.regression_risk)

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
