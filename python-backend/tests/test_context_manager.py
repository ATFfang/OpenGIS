import unittest
import json

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.context.observation import compress_observation
from opengis_backend.agent.context.request_budget import RequestBudgetManager
from opengis_backend.agent.workflow.workflow_model import WorkflowNode
from opengis_backend.agent.workflow.workflow_outputs import summarize_step_output
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer
from opengis_backend.tools.schema import ParamType, ToolParam, ToolSchema
from opengis_backend.tools.builtin.subagent_tool import _format_results


class ContextManagerPruneTests(unittest.TestCase):
    def test_prune_tool_results_keeps_protected_tool_outputs(self):
        ctx = ContextManager(
            keep_recent=0,
            use_token_based_pruning=False,
            max_single_result_tokens=0,
        )
        protected = "skill instructions\n" + ("important\n" * 500)
        ordinary = "directory listing\n" + ("file.py\n" * 500)

        ctx.add_tool_result("call-load-skill", "load_skill", protected)
        ctx.add_tool_result("call-list-directory", "list_directory", ordinary)

        saved = ctx.prune_tool_results()

        self.assertGreater(saved, 0)
        self.assertEqual(ctx.messages[0]["content"], protected)
        self.assertIn("body removed to save tokens", ctx.messages[1]["content"])

    def test_build_messages_projects_old_large_tool_results_without_mutating_raw_history(self):
        ctx = ContextManager(
            provider_raw_recent=2,
            recent_user_turns_for_provider=0,
            max_projected_tool_result_chars=500,
            max_projected_tool_call_arg_chars=300,
            max_projected_execute_code_chars=120,
        )
        long_code = "import pandas as pd\n" + "print('heavy')\n" * 200
        long_result = json.dumps({
            "features": [
                {"properties": {"name": f"poi-{idx}", "value": idx}}
                for idx in range(500)
            ]
        }, ensure_ascii=False)
        ctx.add_user_message("绘制组合图")
        ctx.add_assistant_with_tool_calls(
            "",
            [
                {
                    "id": "call-code",
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "arguments": json.dumps({"code": long_code}, ensure_ascii=False),
                    },
                }
            ],
        )
        ctx.add_tool_result("call-code", "execute_code", long_result, meta={"script_path": "/tmp/demo.py"})
        ctx.add_user_message("现在有几个图层？")
        ctx.add_assistant_message("我会查看当前图层。")

        messages = ctx.build_messages("system")

        digest = next(message["content"] for message in messages if "Earlier Live Conversation Digest" in str(message.get("content", "")))

        self.assertIn("Earlier Live Conversation Digest", digest)
        self.assertIn("assistant tool calls: execute_code", digest)
        self.assertIn("tool execute_code", digest)
        self.assertIn("script_path=/tmp/demo.py", digest)
        self.assertLess(len(digest), len(long_result))
        self.assertFalse(any(message.get("role") == "tool" for message in messages[1:3]))

        raw_args = ctx.messages[1]["tool_calls"][0]["function"]["arguments"]
        self.assertIn("print('heavy')", raw_args)
        self.assertEqual(ctx.messages[2]["content"], long_result)

    def test_build_messages_can_project_old_messages_without_collapsing_for_debug(self):
        ctx = ContextManager(
            provider_raw_recent=1,
            recent_user_turns_for_provider=0,
            collapse_old_provider_messages=False,
            max_projected_tool_result_chars=500,
            max_projected_tool_call_arg_chars=300,
            max_projected_execute_code_chars=120,
        )
        long_code = "import pandas as pd\n" + "print('heavy')\n" * 200
        ctx.add_user_message("绘制组合图")
        ctx.add_assistant_with_tool_calls(
            "",
            [{
                "id": "call-code",
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "arguments": json.dumps({"code": long_code}, ensure_ascii=False),
                },
            }],
        )
        ctx.add_tool_result("call-code", "execute_code", "result\n" * 1000)
        ctx.add_user_message("继续")

        messages = ctx.build_messages("system")

        assistant = next(message for message in messages if isinstance(message.get("tool_calls"), list))
        projected_call = assistant["tool_calls"][0]["function"]["arguments"]
        projected_args = json.loads(projected_call)
        self.assertTrue(projected_args["_opengis_projected_arguments"])
        self.assertIn("omitted from provider context", projected_args["code"])
        self.assertLess(len(projected_call), 500)
        self.assertTrue(any("[projected_tool_result]" in str(message.get("content", "")) for message in messages))

    def test_build_messages_keeps_recent_tool_context_raw_for_immediate_repair(self):
        ctx = ContextManager(
            provider_raw_recent=4,
            recent_user_turns_for_provider=0,
            max_projected_tool_result_chars=100,
            max_projected_tool_call_arg_chars=100,
        )
        long_code = "print('debug')\n" * 100
        ctx.add_user_message("运行代码")
        ctx.add_assistant_with_tool_calls(
            "",
            [{
                "id": "call-recent",
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "arguments": json.dumps({"code": long_code}, ensure_ascii=False),
                },
            }],
        )
        ctx.add_tool_result("call-recent", "execute_code", "error\n" * 200)

        messages = ctx.build_messages("system")

        assistant = next(message for message in messages if isinstance(message.get("tool_calls"), list))
        recent_args = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(recent_args["code"], long_code)
        tool_result = next(message for message in messages if message.get("role") == "tool")
        self.assertEqual(tool_result["content"], "error\n" * 200)

    def test_build_messages_sanitizes_recent_malformed_tool_calls_for_provider(self):
        ctx = ContextManager(provider_raw_recent=6, recent_user_turns_for_provider=0)
        malformed_args = (
            '{"geojson_path": "/tmp/a.geojson", "name": "A", "point_size": 5'
            '{"geojson_path": "/tmp/b.geojson", "name": "B"}'
        )
        ctx.add_assistant_with_tool_calls(
            "<tool_call><function=add_layer><parameter=name>A</parameter></function></tool_call>",
            [{
                "id": "call-bad",
                "type": "function",
                "function": {
                    "name": "add_layer",
                    "arguments": malformed_args,
                },
            }],
        )
        ctx.add_tool_result("call-bad", "add_layer", '{"success": false, "error": "bad args"}')
        ctx.add_user_message("结论是啥")

        messages = ctx.build_messages("system")

        assistant = next(message for message in messages if message.get("role") == "assistant")
        self.assertEqual(assistant["content"], "")
        projected_args = assistant["tool_calls"][0]["function"]["arguments"]
        parsed = json.loads(projected_args)
        self.assertTrue(parsed["_opengis_invalid_arguments"])
        self.assertNotIn("<tool_call>", json.dumps(assistant, ensure_ascii=False))

    def test_build_messages_anchors_recent_user_requests_across_tool_heavy_turns(self):
        ctx = ContextManager(provider_raw_recent=4, recent_user_turns_for_provider=4)
        ctx.add_user_message("能不能价格高的在上面")
        ctx.add_assistant_message("已按价格高低调整显示。")
        ctx.add_user_message("你要修正我的operation，而不是饶过他自己写脚本")
        for idx in range(12):
            call_id = f"call-{idx}"
            ctx.add_assistant_with_tool_calls(
                "",
                [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "arguments": json.dumps({"code": f"print({idx})"}, ensure_ascii=False),
                    },
                }],
            )
            ctx.add_tool_result(call_id, "execute_code", f"output {idx}")
        ctx.add_assistant_message("已完成价格分类渲染。")
        ctx.add_user_message("你说按照我的要求，我上一步的要求是啥")

        messages = ctx.build_messages("system")

        anchor = next(message["content"] for message in messages if "Recent User Requests" in str(message.get("content", "")))
        self.assertIn("Recent User Requests", anchor)
        self.assertIn("- previous: 你要修正我的operation，而不是饶过他自己写脚本", anchor)
        self.assertIn("- current: 你说按照我的要求，我上一步的要求是啥", anchor)
        self.assertLess(anchor.find("你要修正我的operation"), anchor.find("你说按照我的要求"))
        working_state = next(message["content"] for message in messages if "Working State" in str(message.get("content", "")))
        self.assertIn("previous_user_request: 你要修正我的operation", working_state)

    def test_build_messages_adds_runtime_state_anchors_for_operation_failures(self):
        ctx = ContextManager(provider_raw_recent=4, recent_user_turns_for_provider=0)
        ctx.add_user_message("修复这个 operation")
        ctx.add_tool_result(
            "call-run",
            "run_operation",
            json.dumps(
                {
                    "success": False,
                    "operation_id": "dbscan_clustering",
                    "status": "failed",
                    "error": "KeyError: input_path",
                },
                ensure_ascii=False,
            ),
        )

        messages = ctx.build_messages("system")

        anchor = next(message["content"] for message in messages if "Runtime State Anchors" in str(message.get("content", "")))
        self.assertIn("Runtime State Anchors", anchor)
        self.assertIn("active_operation: dbscan_clustering status=failed", anchor)
        self.assertIn("run_operation: KeyError: input_path", anchor)
        working_state = next(message["content"] for message in messages if "Working State" in str(message.get("content", "")))
        self.assertIn("active_operation: dbscan_clustering status=failed", working_state)

    def test_request_budget_manager_breaks_down_projected_request(self):
        ctx = ContextManager(provider_raw_recent=2, recent_user_turns_for_provider=2)
        ctx.add_user_message("分析上海饮品店")
        ctx.add_tool_result("call", "query_features", json.dumps({"success": True, "features": [1, 2, 3]}))
        messages = ctx.build_messages("system\n## Retrieved Project Memory\n- shops.csv")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query_features",
                    "description": "Query layer features",
                    "parameters": {"type": "object", "properties": {"layer_id": {"type": "string"}}},
                },
            }
        ]

        report = RequestBudgetManager(input_token_budget=8000, output_reserve_tokens=1000).analyze(
            messages=messages,
            tools=tools,
        )

        self.assertGreater(report.total_tokens, 0)
        self.assertGreater(report.section_tokens("tool_schema"), 0)
        self.assertGreater(report.section_tokens("working_state"), 0)
        self.assertEqual(report.tool_schema_count, 1)
        self.assertIn(report.pressure, {"ok", "warm", "hot", "overflow"})

    def test_request_budget_manager_suggests_tighter_limits_under_pressure(self):
        manager = RequestBudgetManager(input_token_budget=8000, output_reserve_tokens=1000)

        ok = manager.suggest_limits(pressure="ok")
        hot = manager.suggest_limits(pressure="hot")
        overflow = manager.suggest_limits(pressure="overflow")

        self.assertGreater(ok.provider_raw_recent, hot.provider_raw_recent)
        self.assertGreater(hot.provider_raw_recent, overflow.provider_raw_recent)
        self.assertGreater(ok.max_tool_result_chars, overflow.max_tool_result_chars)

    def test_build_messages_applies_projection_budget_limits(self):
        ctx = ContextManager(
            provider_raw_recent=8,
            recent_user_turns_for_provider=4,
            max_projected_tool_result_chars=4000,
            max_projected_tool_call_arg_chars=1400,
            max_projected_execute_code_chars=900,
        )
        for idx in range(6):
            ctx.add_user_message(f"user {idx}")
            ctx.add_tool_result(f"call-{idx}", "query_features", "x" * 5000)

        limits = RequestBudgetManager(input_token_budget=8000).suggest_limits(pressure="overflow")
        messages = ctx.build_messages("system", projection_limits=limits)

        digest = next(message["content"] for message in messages if "Earlier Live Conversation Digest" in str(message.get("content", "")))
        self.assertLess(len(digest), 4000)
        self.assertLessEqual(sum(1 for message in messages if message.get("role") == "tool"), limits.provider_raw_recent)

    def test_tool_materializer_keeps_complete_profile_tool_surface(self):
        schemas = []
        for name, description in [
            ("execute_code", "Run Python"),
            ("read_file", "Read file"),
            ("list_layers", "List map layers"),
            ("set_categorized_style", "Style layer categories"),
            ("start_worker", "Start background worker with a very long description " * 80),
            ("create_workflow", "Create workflow with a very long description " * 80),
        ]:
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
                },
            })
        materialized = ToolMaterializer(schemas).materialize()

        self.assertIn("execute_code", materialized.selected_names)
        self.assertIn("read_file", materialized.selected_names)
        self.assertIn("set_categorized_style", materialized.selected_names)
        self.assertIn("create_workflow", materialized.selected_names)
        self.assertIn("start_worker", materialized.selected_names)
        self.assertEqual(materialized.reason, "profile")

    def test_tool_schema_compact_mode_slims_descriptions(self):
        long_text = "This is a very long tool description. " * 80
        schema = ToolSchema(
            name="demo_tool",
            display_name="Demo",
            description=long_text,
            category="test",
            params=[
                ToolParam(
                    name="value",
                    type=ParamType.STRING,
                    description="A very long parameter description. " * 40,
                )
            ],
            returns="ok",
        )

        compact = schema.to_openai_schema(compact=True)
        full = schema.to_openai_schema(compact=False)

        self.assertLess(
            len(compact["function"]["description"]),
            len(full["function"]["description"]),
        )
        self.assertLess(
            len(compact["function"]["parameters"]["properties"]["value"]["description"]),
            len(full["function"]["parameters"]["properties"]["value"]["description"]),
        )

    def test_workflow_step_summary_contains_structured_contract(self):
        node = WorkflowNode(
            id="analyze",
            title="Analyze Data",
            output_contract="输出统计表路径与图层 id",
        )
        summary = summarize_step_output(
            node=node,
            step_index=2,
            full_output="保存结果 /tmp/report.csv\n共 120 条记录\n" + ("log\n" * 200),
            file_path="/tmp/step2.md",
        )

        self.assertIn("workflow_step_contract", summary)
        self.assertIn("/tmp/report.csv", summary)
        self.assertIn("/tmp/step2.md", summary)

    def test_subagent_results_include_structured_contract(self):
        rendered = _format_results([
            {
                "task": "分析数据",
                "ok": True,
                "result": "完成，输出 /tmp/result.geojson",
            }
        ])

        self.assertIn("subagent_result_contract", rendered)
        self.assertIn("/tmp/result.geojson", rendered)

    def test_observation_compression_preserves_summary_and_artifact_pointer(self):
        content = json.dumps(
            {
                "success": True,
                "layer_id": "layer-1",
                "name": "上海饮品店",
                "features": [
                    {"properties": {"name": f"poi-{idx}", "price": idx}}
                    for idx in range(100)
                ],
            },
            ensure_ascii=False,
        )

        compressed = compress_observation(
            tool_name="query_features",
            content=content,
            metadata={"retained_output_path": "/tmp/full-output.txt"},
            max_chars=900,
        )
        parsed = json.loads(compressed)

        self.assertTrue(parsed["observation_compressed"])
        self.assertEqual(parsed["tool"], "query_features")
        self.assertEqual(parsed["layer_id"], "layer-1")
        self.assertEqual(parsed["artifact_pointer"]["path"], "/tmp/full-output.txt")
        self.assertIn("collections", parsed)


if __name__ == "__main__":
    unittest.main()
