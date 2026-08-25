import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from opengis_backend.agent.context.pending_intent import PendingIntentResolver
from opengis_backend.agent.execution.capability_router import CapabilityRouter
from opengis_backend.agent.loop.runtime_control import (
    LoopAnomalyDetector,
    RuntimeControl,
    TaskMode,
)
from opengis_backend.agent.loop.turn_runner import ToolSettlement
from opengis_backend.agent.loop.turn_runner import LoopTurnTelemetry
from opengis_backend.runs.archive import RunArchive
from opengis_backend.workspace.memory_store import MemoryRecord, MemoryStore


def tool_call(call_id: str, name: str, arguments: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _settlement(call_id: str, name: str, content: str) -> ToolSettlement:
    return ToolSettlement(
        call_id=call_id,
        name=name,
        arguments={},
        content=content,
        counts_as_code_step=name in {"execute_code", "run_script_file"},
    )


class RuntimeControlTests(unittest.TestCase):
    def test_infers_operation_repair_from_user_request(self) -> None:
        control = RuntimeControl.from_user_message("你要修正我的operation，而不是饶过他自己写脚本")

        self.assertEqual(control.objective.mode, TaskMode.OPERATION_REPAIR)
        self.assertIn("operation_repair", control.system_prompt())
        self.assertIn("inspect contract/code", control.system_prompt())

    def test_worker_mode_blocks_one_shot_code_before_worker_tool(self) -> None:
        control = RuntimeControl.from_user_message("用 worker 后台持续刷新动态点")

        result = control.guard_tool_calls([tool_call("call-code", "execute_code", '{"code": "while True: pass"}')])

        self.assertIn("call-code", result.blocked_call_ids)
        self.assertIn("worker lifecycle tools", result.blocked_call_ids["call-code"])

    def test_worker_mode_forces_final_after_healthy_worker_update(self) -> None:
        control = RuntimeControl.from_user_message("构建一个后台worker实时渲染车辆轨迹")

        decision = control.observe_settlements([
            ToolSettlement(
                call_id="call-worker",
                name="wait_worker_update",
                arguments={"worker_id": "worker_ok"},
                content=(
                    '{"id": "worker_ok", "status": "running", '
                    '"health": {"ok": true, "message": "dynamic layer diff seq=3"}}'
                ),
            )
        ])

        self.assertEqual(decision.force_final_reason, "worker_running_verified")
        self.assertIn("worker=healthy", decision.corrective_message)

    def test_workflow_mode_blocks_direct_map_side_effect_before_workflow_tool(self) -> None:
        control = RuntimeControl.from_user_message("创建一个 workflow 处理这个数据")

        result = control.guard_tool_calls([tool_call("call-layer", "add_layer", '{"name": "x"}')])

        self.assertIn("call-layer", result.blocked_call_ids)
        self.assertIn("workflow tools", result.blocked_call_ids["call-layer"])

    def test_loop_anomaly_detector_flags_map_churn(self) -> None:
        anomaly = LoopAnomalyDetector.detect(
            ["remove_layer", "add_layer", "set_categorized_style", "remove_layer", "add_layer", "zoom_to_layer"],
            [],
        )

        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly.kind, "map_churn")

    def test_loop_anomaly_detector_flags_repeated_failure(self) -> None:
        anomaly = LoopAnomalyDetector.detect([], ["run_operation(call-a) failed: x", "run_operation(call-b) failed: x"])

        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly.kind, "repeated_failure")

    def test_loop_anomaly_detector_ignores_changed_error_shape(self) -> None:
        anomaly = LoopAnomalyDetector.detect([], ["run_operation(call-a) failed: x", "run_operation(call-b) failed: y"])

        self.assertIsNone(anomaly)

    def test_structured_tool_failure_warns_against_same_retry(self) -> None:
        control = RuntimeControl.from_user_message("加载华东师范大学闵行校区建筑物")
        decision = control.observe_settlements([
            ToolSettlement(
                call_id="call-osm",
                name="osm_call",
                arguments={},
                content='{"success": false, "error": "osm_invalid_params", "message": "missing bbox", "do_not_retry_same_request": true}',
            )
        ])

        self.assertIn("change input materially", decision.corrective_message)
        self.assertIn("osm_invalid_params", decision.corrective_message)

    def test_blocks_shell_overpass_bypass_after_osm_failure(self) -> None:
        control = RuntimeControl.from_user_message("加载华东师范大学闵行校区建筑物")
        control.observe_settlements([
            ToolSettlement(
                call_id="call-osm",
                name="osm_call",
                arguments={},
                content='{"success": false, "error": "osm_network_error", "message": "Overpass unavailable", "do_not_retry_same_request": true}',
            )
        ])

        result = control.guard_tool_calls([
            tool_call(
                "call-bash",
                "bash",
                '{"command": "curl https://overpass.kumi.systems/api/interpreter -d data=..."}',
            )
        ])

        self.assertIn("call-bash", result.blocked_call_ids)
        self.assertIn("do not bypass", result.blocked_call_ids["call-bash"])

    def test_operation_repair_edit_resets_repeated_failure_window(self) -> None:
        control = RuntimeControl.from_user_message("修复 dbscan_clustering operation")
        control.observe_settlements([
            ToolSettlement(
                call_id="call-a",
                name="run_operation",
                arguments={},
                content='{"success": false, "error": "KeyError: input_path"}',
                error="KeyError: input_path",
            )
        ])
        control.observe_settlements([
            ToolSettlement(
                call_id="call-edit",
                name="edit_operation",
                arguments={},
                content='{"success": true}',
            )
        ])
        decision = control.observe_settlements([
            ToolSettlement(
                call_id="call-b",
                name="run_operation",
                arguments={},
                content='{"success": false, "error": "KeyError: input_path"}',
                error="KeyError: input_path",
            )
        ])

        self.assertNotIn("Loop anomaly detected", decision.corrective_message)
        self.assertEqual(len(control.recent_failures), 1)

    def test_data_analysis_blocks_unrequested_map_side_effects(self) -> None:
        control = RuntimeControl.from_user_message("你觉得这块地交通通达度如何")

        result = control.guard_tool_calls([tool_call("call-layer", "add_layer", '{"name": "分析范围"}')])

        self.assertIn("call-layer", result.blocked_call_ids)
        self.assertIn("analysis/opinion", result.blocked_call_ids["call-layer"])

    def test_data_analysis_forces_final_after_sufficient_answer_output(self) -> None:
        control = RuntimeControl.from_user_message("你觉得这块地交通通达度如何")
        output = """
        华东师范大学闵行校区交通通达度分析。
        道路网络分析：道路总数 866 条，路网密度 17.84 km/km²。
        公共交通覆盖：公交站点 189 个，500m 内公交站 8 个。
        地铁覆盖：最近地铁口距离中心 1819 m。
        综合评价：校区内部道路和公交覆盖较好。
        总分：90/100。评级：优秀。结论：整体交通通达度较好，但轨道交通距离稍远。
        """

        first = control.observe_settlements([
            _settlement("call-probe", "execute_code", "道路 count=866"),
        ])
        second = control.observe_settlements([
            _settlement("call-analysis", "execute_code", output),
        ])

        self.assertIsNone(first.force_final_reason)
        self.assertEqual(second.force_final_reason, "analysis_answer_ready")
        self.assertIn("analysis_answer_ready", second.corrective_message)

    def test_data_analysis_allows_map_side_effect_when_requested(self) -> None:
        control = RuntimeControl.from_user_message("分析交通通达度并加到地图展示")

        result = control.guard_tool_calls([tool_call("call-layer", "add_layer", '{"name": "分析范围"}')])

        self.assertNotIn("call-layer", result.blocked_call_ids)

    def test_runtime_control_projects_learned_failure_lessons_after_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            MemoryStore(tmp).add(MemoryRecord.create(
                kind="failure_lesson",
                scope="tool_failure",
                title="run_operation: KeyError",
                content=(
                    "Symptom: run_operation failed with KeyError input_path for dbscan_clustering. "
                    "Verified fix path: edit_operation -> run_operation."
                ),
                tags=["failure", "run_operation", "KeyError", "dbscan_clustering"],
                source_run_id="run_prev",
            ))
            control = RuntimeControl.from_user_message(
                "修正 dbscan_clustering operation 的 input_path 报错",
                workspace_path=tmp,
            )
            control.observe_settlements([
                ToolSettlement(
                    call_id="call_fail",
                    name="run_operation",
                    arguments={"operation_id": "dbscan_clustering"},
                    content='{"success": false, "error": "KeyError: input_path"}',
                    error="KeyError: input_path",
                )
            ])

            prompt = control.system_prompt()

            self.assertIn("Failure Lessons", prompt)
            self.assertIn("edit_operation", prompt)
            self.assertIn("run_prev", prompt)

    def test_pending_intent_resolves_ok_after_map_load_offer(self) -> None:
        with TemporaryDirectory() as tmp:
            messages = [
                {
                    "role": "tool",
                    "name": "run_operation",
                    "content": (
                        '{"success": true, "clustered_path": "测试华师大闵行_POI_clustered.geojson", '
                        '"centers_path": "测试华师大闵行_POI_centers.geojson"}'
                    ),
                    "_meta": {"kind": "tool_result", "tool_name": "run_operation"},
                },
                {
                    "role": "assistant",
                    "content": "测试完成。需要我把结果加载到地图上查看吗？",
                },
            ]

            intent = PendingIntentResolver(tmp).resolve(messages, "ok")

            self.assertIsNotNone(intent)
            assert intent is not None
            self.assertEqual(intent.kind, "confirm_map_load")
            self.assertIn("加载到地图", intent.resolved_objective)
            self.assertTrue(any(item.endswith("测试华师大闵行_POI_clustered.geojson") for item in intent.artifacts))

    def test_runtime_control_blocks_analysis_tools_for_confirmed_map_load(self) -> None:
        with TemporaryDirectory() as tmp:
            intent = PendingIntentResolver(tmp).resolve(
                [{"role": "assistant", "content": "需要我把结果加载到地图上查看吗？"}],
                "ok",
            )
            control = RuntimeControl.from_user_message(
                "ok",
                workspace_path=tmp,
                pending_intent=intent,
            )

            result = control.guard_tool_calls([
                tool_call("call-bash", "bash", '{"command": "python inspect.py"}'),
                tool_call("call-layer", "add_layer", '{"geojson_path": "result.geojson"}'),
            ])

            self.assertIn("call-bash", result.blocked_call_ids)
            self.assertNotIn("call-layer", result.blocked_call_ids)
            self.assertIn("Resolved Turn Objective", control.system_prompt())

    def test_simple_map_requests_use_narrow_base_tool_packs(self) -> None:
        control = RuntimeControl.from_user_message("把颜色分类一下")

        packs = control.tool_packs()

        self.assertIn("map", packs)
        self.assertIn("style", packs)
        self.assertIn("skill", packs)
        self.assertNotIn("file", packs)
        self.assertNotIn("code", packs)
        self.assertNotIn("operation", packs)

    def test_style_only_requests_keep_map_inspection_tools(self) -> None:
        control = RuntimeControl.from_user_message("按照价格分层设色，透明度和大小，价格高的在上面")

        packs = control.tool_packs()

        self.assertIn("style", packs)
        self.assertIn("map", packs)
        self.assertNotIn("code", packs)

    def test_capability_router_selects_relevant_workspace_assets(self) -> None:
        with TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / ".opengis" / "skills" / "poi_style"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: poi_style\ndescription: Improve POI styling and labeling.\ntags: [map, style, label]\n---\n# poi_style\n",
                encoding="utf-8",
            )

            op_dir = Path(tmp) / ".opengis" / "operations" / "cluster_points"
            op_dir.mkdir(parents=True)
            (op_dir / "main.py").write_text(
                "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--input', required=True)\nparser.add_argument('--output', required=True)\nargs = parser.parse_args()\n",
                encoding="utf-8",
            )
            (op_dir / "operation.json").write_text(
                """
{
  "schema_version": "1.0",
  "id": "cluster_points",
  "name": "Cluster Points",
  "version": "0.1.0",
  "revision": 1,
  "status": "validated",
  "description": "Cluster spatial points for map visualization.",
  "entry": "main.py",
  "runtime": {"language": "python", "python": ">=3.12", "dependencies": []},
  "input_schema": {"type": "object", "required": [], "properties": {}},
  "output_schema": {"type": "object", "properties": {}},
  "tunable_parameters": {},
  "validation": {"input_checks": [], "output_checks": []},
  "provenance": {"created_at": "2026-01-01T00:00:00"},
  "created_at": "2026-01-01T00:00:00",
  "updated_at": "2026-01-01T00:00:00"
}
""".strip(),
                encoding="utf-8",
            )

            router = CapabilityRouter(workspace_path=tmp)
            from opengis_backend.workspace.evolution_asset_registry import EvolutionAssetRecord, EvolutionAssetRegistry

            EvolutionAssetRegistry(tmp).upsert(EvolutionAssetRecord(
                id="asset-1",
                kind="operation",
                name="cluster_points",
                path=str(op_dir),
                state="enabled",
            ))

            selection = router.select(
                "请帮我用 poi_style 优化 POI 图层，并复用 cluster_points operation",
                base_tool_packs={"skill", "map"},
                allow_3d=False,
            )

            second = router.select(
                "请帮我用 poi_style 优化 POI 图层，并复用 cluster_points operation",
                base_tool_packs={"skill", "map"},
                allow_3d=False,
            )

            self.assertIn("skill", selection.tool_packs)
            self.assertIn("operation", selection.tool_packs)
            self.assertTrue(any(item.name == "poi_style" for item in selection.skills))
            self.assertTrue(any(item.name == "cluster_points" for item in selection.operations))
            self.assertFalse(any(item.name == "cluster_points" for item in selection.evolution_assets))
            self.assertEqual(selection.inventory_cache_status, "miss")
            self.assertEqual(second.inventory_cache_status, "hit")
            self.assertIn("router=inventory:", selection.to_prompt())

            control = RuntimeControl.from_user_message(
                "poi_style cluster_points",
                workspace_path=tmp,
                feature_flags={
                    "capability_router_max_skills": 0,
                    "capability_router_max_operations": 0,
                    "capability_router_max_assets": 0,
                },
            )
            limited = control.capability_selection()

            self.assertEqual(len(limited.skills), 0)
            self.assertEqual(len(limited.operations), 0)
            self.assertEqual(len(limited.evolution_assets), 0)
            self.assertNotIn("operation", limited.tool_packs)

    def test_capability_router_hides_rolled_back_operation(self) -> None:
        with TemporaryDirectory() as tmp:
            op_dir = Path(tmp) / ".opengis" / "operations" / "old_operation"
            op_dir.mkdir(parents=True)
            (op_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (op_dir / "operation.json").write_text(
                '{"id":"old_operation","name":"Old Operation","description":"Old analysis","entry":"main.py"}',
                encoding="utf-8",
            )
            from opengis_backend.workspace.evolution_asset_registry import (
                EvolutionAssetRecord,
                EvolutionAssetRegistry,
            )

            EvolutionAssetRegistry(tmp).upsert(EvolutionAssetRecord(
                id="asset-old",
                kind="operation",
                name="old_operation",
                path=str(op_dir),
                state="archived",
            ))

            selection = CapabilityRouter(workspace_path=tmp).select(
                "run old_operation",
                base_tool_packs={"operation"},
            )

            self.assertFalse(any(item.name == "old_operation" for item in selection.operations))

    def test_capability_router_injects_enabled_harness_assets_compactly(self) -> None:
        with TemporaryDirectory() as tmp:
            from opengis_backend.workspace.evolution_asset_registry import (
                EvolutionAssetRecord,
                EvolutionAssetRegistry,
            )

            patch_dir = Path(tmp) / ".opengis" / "evolution" / "assets" / "prompt_patches" / "style-color-repair"
            patch_dir.mkdir(parents=True)
            (patch_dir / "prompt_patch.json").write_text(
                """
{
  "kind": "prompt_patch",
  "id": "style-color-repair",
  "name": "Style color repair",
  "summary": "When changing categorized POI colors, require explicit hex color mappings.",
  "tags": ["style", "categorized", "颜色", "poi"],
  "injection": {"surface": "dynamic_tail", "mode": "router_selected", "max_chars": 420}
}
""".strip(),
                encoding="utf-8",
            )
            (patch_dir / "PATCH.md").write_text(
                "# Patch\n\nFor matching style turns, use explicit hex colors for each category and verify layer style state after applying categorized colors.\n",
                encoding="utf-8",
            )
            policy_dir = Path(tmp) / ".opengis" / "evolution" / "assets" / "tool_policies" / "style-tool-policy"
            policy_dir.mkdir(parents=True)
            (policy_dir / "tool_policy.json").write_text(
                """
{
  "kind": "tool_policy",
  "id": "style-tool-policy",
  "name": "Style tool policy",
  "summary": "Prefer map style tools over Python for layer styling.",
  "tags": ["style", "tool routing", "图层"],
  "routing": {"surface": "capability_router", "mode": "advisory_dynamic_tail", "max_chars": 420}
}
""".strip(),
                encoding="utf-8",
            )
            (policy_dir / "POLICY.md").write_text(
                "# Policy\n\nFor matching layer style requests, use set_categorized_style/update_layer_style before writing Python scripts.\n",
                encoding="utf-8",
            )

            registry = EvolutionAssetRegistry(tmp)
            registry.upsert(EvolutionAssetRecord(
                id="patch-asset",
                kind="prompt_patch",
                name="style-color-repair",
                path=str(patch_dir),
                state="enabled",
            ))
            registry.upsert(EvolutionAssetRecord(
                id="policy-asset",
                kind="tool_policy",
                name="style-tool-policy",
                path=str(policy_dir),
                state="enabled",
            ))

            selection = CapabilityRouter(workspace_path=tmp).select(
                "按照价格分层设色，颜色不要变白，优先用图层样式工具",
                base_tool_packs={"map", "style"},
            )

            self.assertTrue(any(item.name == "style-color-repair" for item in selection.evolution_assets))
            self.assertTrue(any(item.name == "style-tool-policy" for item in selection.evolution_assets))
            prompt = selection.to_prompt()
            self.assertIn("harness_assets:", prompt)
            self.assertIn("explicit hex colors", prompt)
            self.assertIn("set_categorized_style", prompt)
            self.assertIn("dynamic-tail guidance", prompt)

            registry.set_state("patch-asset", "archived")
            refreshed = CapabilityRouter(workspace_path=tmp, cache_ttl_seconds=0).select(
                "按照价格分层设色，颜色不要变白，优先用图层样式工具",
                base_tool_packs={"map", "style"},
            )
            self.assertFalse(any(item.name == "style-color-repair" for item in refreshed.evolution_assets))
            self.assertTrue(any(item.name == "style-tool-policy" for item in refreshed.evolution_assets))

    def test_run_archive_persists_capability_telemetry(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = RunArchive.open(run_id="run_caps", prompt="test", workspace_path=tmp, model="m")
            telemetry = LoopTurnTelemetry(
                iteration=1,
                code_steps=0,
                tool_steps=0,
                message_count=1,
                context_tokens=10,
                tool_schema_count=2,
            )
            telemetry.tool_pack_count = 3
            telemetry.selected_skill_count = 1
            telemetry.selected_operation_count = 2
            telemetry.total_skill_count = 9
            telemetry.total_operation_count = 8
            telemetry.capability_reason = "demo"
            archive.record_llm_usage(
                usage={"prompt_tokens": 10, "cached_tokens": 0},
                prompt_cache={"enabled": True},
                telemetry=telemetry.__dict__,
                request={"tool_schema_hash": "x"},
            )

            records = archive.read_llm_usage()
            self.assertEqual(records[0]["telemetry"]["tool_pack_count"], 3)
            self.assertEqual(records[0]["telemetry"]["selected_skill_count"], 1)
            self.assertEqual(records[0]["telemetry"]["capability_reason"], "demo")


if __name__ == "__main__":
    unittest.main()
