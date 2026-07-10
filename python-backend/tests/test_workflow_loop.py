import unittest

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.llm import LLMResponse
from opengis_backend.agent.loop.loop_kernel import LoopKernel, LoopTurnRequest
from opengis_backend.agent.execution.tool_runtime import ToolRuntime
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer
from opengis_backend.agent.loop.turn_runner import decide_text_continuation
from opengis_backend.agent.loop.types import CodeExecResult
from opengis_backend.agent.loop.workflow_loop import WorkflowLoop
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument


class WorkflowLoopFunctionCallTests(unittest.TestCase):
    def test_loop_kernel_injects_profile_materialized_tool_prompt(self) -> None:
        schemas = [
            {"type": "function", "function": {"name": "read_file", "description": "Read file", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "execute_code", "description": "Run Python", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "start_worker", "description": "Start resident worker", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "layout_export", "description": "Export layout canvas", "parameters": {"type": "object", "properties": {}}}},
        ] + [
            {"type": "function", "function": {"name": f"misc_{i}", "description": "misc", "parameters": {"type": "object", "properties": {}}}}
            for i in range(8)
        ]
        captured: dict = {}

        def llm_call(messages, **kwargs):
            captured["messages"] = messages
            captured["tools"] = kwargs.get("tools")
            return LLMResponse(content="done")

        context = ContextManager()
        context.add_user_message("启动 worker 推送动态轨迹")
        kernel = LoopKernel(
            llm_call=llm_call,
            context=context,
            tool_runtime=None,
            tool_schemas=schemas,
            tool_materializer=ToolMaterializer(schemas, max_tools=8),
            retryable_exceptions=(ConnectionError,),
            max_retries=0,
            base_delay=0,
        )

        outcome = kernel.run_turn(
            LoopTurnRequest(
                iteration=0,
                code_steps=0,
                tool_steps=0,
                system_prompt="system",
                text_code_step=1,
                code_step_for_tool=lambda index: index + 1,
            )
        )

        self.assertEqual(outcome.response_text, "done")
        self.assertIn("Active Function Tools For This Agent Profile", captured["messages"][1]["content"])
        tool_names = [item["function"]["name"] for item in captured["tools"]]
        self.assertIn("start_worker", tool_names)
        self.assertIn("execute_code", tool_names)
        self.assertIn("layout_export", tool_names)

    def test_tool_materializer_is_profile_bounded_not_prompt_classified(self) -> None:
        schemas = [
            {"type": "function", "function": {"name": "read_file", "description": "Read file", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "execute_code", "description": "Run Python", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "start_worker", "description": "Start resident worker", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "layout_export", "description": "Export layout canvas", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "websearch", "description": "Search web", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "tool_a", "description": "misc", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "tool_b", "description": "misc", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "tool_c", "description": "misc", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "tool_d", "description": "misc", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "tool_e", "description": "misc", "parameters": {"type": "object", "properties": {}}}},
        ]

        materialized = ToolMaterializer(schemas, max_tools=4).materialize([
            {"role": "user", "content": "创建一个worker持续推送动态轨迹"}
        ])

        self.assertIn("read_file", materialized.selected_names)
        self.assertIn("execute_code", materialized.selected_names)
        self.assertIn("start_worker", materialized.selected_names)
        self.assertIn("layout_export", materialized.selected_names)
        self.assertEqual(materialized.reason, "profile_bounded")

    def test_tool_materializer_never_truncates_core_tools_at_tail(self) -> None:
        schemas = [
            {"type": "function", "function": {"name": f"layout_tool_{i}", "description": "layout canvas map export", "parameters": {"type": "object", "properties": {}}}}
            for i in range(12)
        ] + [
            {"type": "function", "function": {"name": "execute_code", "description": "Run Python", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "run_script_file", "description": "Run saved script", "parameters": {"type": "object", "properties": {}}}},
        ]

        materialized = ToolMaterializer(schemas, max_tools=8).materialize([
            {"role": "user", "content": "导出制图画布并调整图例"}
        ])

        self.assertIn("execute_code", materialized.selected_names)
        self.assertIn("run_script_file", materialized.selected_names)

    def test_tool_materializer_keeps_operation_tools_visible(self) -> None:
        schemas = [
            {"type": "function", "function": {"name": f"misc_{i}", "description": "misc", "parameters": {"type": "object", "properties": {}}}}
            for i in range(30)
        ] + [
            {"type": "function", "function": {"name": "list_operations", "description": "List operations", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_operation", "description": "Get operation", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "run_operation", "description": "Run operation", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "create_operation", "description": "Create operation", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "edit_operation", "description": "Edit operation", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "promote_script_to_operation", "description": "Promote operation", "parameters": {"type": "object", "properties": {}}}},
        ]

        materialized = ToolMaterializer(schemas, max_tools=8).materialize([
            {"role": "user", "content": "把这个脚本沉淀成可复用操作"}
        ])

        self.assertIn("list_operations", materialized.selected_names)
        self.assertIn("get_operation", materialized.selected_names)
        self.assertIn("run_operation", materialized.selected_names)
        self.assertIn("create_operation", materialized.selected_names)
        self.assertIn("edit_operation", materialized.selected_names)
        self.assertIn("promote_script_to_operation", materialized.selected_names)

    def test_shared_continuation_policy_flags_text_without_tool_work(self) -> None:
        decision = decide_text_continuation("可以，颜色分为红绿蓝三类。")

        self.assertTrue(decision.should_nudge)
        self.assertEqual(decision.reason, "no_tool_work_yet")

    def test_shared_continuation_policy_accepts_after_workflow_tool_or_nudge(self) -> None:
        after_tool = decide_text_continuation(
            "已完成：输出结果已保存。",
            tool_steps=1,
            accept_after_any_tool=True,
        )
        after_nudge = decide_text_continuation(
            "本节点不需要额外工具，已完成。",
            nudged=True,
            accept_after_any_tool=True,
        )

        self.assertTrue(after_tool.should_accept)
        self.assertTrue(after_nudge.should_accept)

    def test_shared_continuation_policy_accepts_gis_action_confirmation_after_tool(self) -> None:
        decision = decide_text_continuation(
            "已缩放到 上海饮品店 图层，该图层包含 13,847 个点数据。",
            tool_steps=1,
        )

        self.assertTrue(decision.should_accept)
        self.assertEqual(decision.reason, "action_completion_after_tool")

    def test_markdown_code_block_is_not_executed(self) -> None:
        responses = iter([
            "```python\nprint('should not run')\n```",
            "已完成：本步骤不需要执行代码。",
            "workflow summary",
        ])
        executed: list[str] = []

        def llm_call(_messages, **_kwargs):
            return LLMResponse(content=next(responses))

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

    def test_workflow_tool_calls_use_shared_settler_and_scope_results(self) -> None:
        responses = iter([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(42)"}',
                        },
                    }
                ],
            ),
            LLMResponse(content="已完成：输出 42。", tool_calls=None),
            LLMResponse(content="workflow summary"),
        ])
        executed: list[str] = []
        tool_events: list[tuple[str, str | None]] = []

        def llm_call(_messages, **_kwargs):
            return next(responses)

        def executor_call(code: str) -> CodeExecResult:
            executed.append(code)
            return CodeExecResult(output="42", logs="", error=None)

        def on_tool_result(name, _content, error, *_args, **_kwargs):
            tool_events.append((name, error))
            return {}

        workflow = WorkflowDocument.from_json({
            "name": "Tool workflow",
            "nodes": [
                {
                    "id": "n1",
                    "title": "Run code",
                    "description": "Run one code tool.",
                }
            ],
            "edges": [],
        })
        runtime = ToolRuntime(
            tool_schemas=[],
            tool_callables={},
            executor_call=executor_call,
        )
        loop = WorkflowLoop(
            llm_call=llm_call,
            executor_call=executor_call,
            system_prompt="system",
            workflow=workflow,
            tool_runtime=runtime,
            on_tool_result=on_tool_result,
        )

        result = loop.run("test")

        self.assertEqual(result, "workflow summary")
        self.assertEqual(executed, ["print(42)"])
        self.assertEqual(tool_events, [("execute_code", None)])
        tool_results = [
            message for message in loop.context.messages
            if (message.get("_meta") or {}).get("scope") == "workflow"
            and (message.get("_meta") or {}).get("tool_name") == "execute_code"
        ]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual((tool_results[0].get("_meta") or {}).get("scope"), "workflow")


if __name__ == "__main__":
    unittest.main()
