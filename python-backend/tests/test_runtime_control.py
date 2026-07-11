import unittest
from tempfile import TemporaryDirectory

from opengis_backend.agent.context.pending_intent import PendingIntentResolver
from opengis_backend.agent.loop.runtime_control import (
    LoopAnomalyDetector,
    RuntimeControl,
    TaskMode,
)
from opengis_backend.agent.loop.turn_runner import ToolSettlement
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
        self.assertIn("Repair the existing Operation", control.system_prompt())

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
        self.assertIn("resident worker is running", decision.corrective_message)

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

        self.assertIn("Do not retry the exact same request", decision.corrective_message)
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
        self.assertIn("already contains enough evidence", second.corrective_message)

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

            self.assertIn("Learned Failure Lessons", prompt)
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


if __name__ == "__main__":
    unittest.main()
