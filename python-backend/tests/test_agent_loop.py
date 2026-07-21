import unittest

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.execution.tool_result import ToolExecutionResult
from opengis_backend.agent.governance.profile import AgentMode, AgentProfile, PermissionLevel
from opengis_backend.agent.llm import LLMResponse, _extract_xmlish_tool_calls
from opengis_backend.agent.loop.agent_loop import AgentLoop
from opengis_backend.agent.loop.loop_kernel import _ensure_provider_tool_protocol
from opengis_backend.agent.loop.turn_runner import tool_intent_progress


class FakeToolRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, arguments: dict) -> ToolExecutionResult:
        self.calls.append((name, arguments))
        return ToolExecutionResult(name=name, arguments=arguments, content='{"success": true}', duration_ms=0)


class OperationRepairFakeRuntime(FakeToolRuntime):
    def execute(self, name: str, arguments: dict) -> ToolExecutionResult:
        self.calls.append((name, arguments))
        if name == "run_operation":
            return ToolExecutionResult(
                name=name,
                arguments=arguments,
                content='{"success": false, "error": "KeyError: input_path"}',
                error="OperationError: KeyError: input_path",
                duration_ms=0,
            )
        return ToolExecutionResult(name=name, arguments=arguments, content='{"success": true}', duration_ms=0)


class AgentLoopFunctionCallTests(unittest.TestCase):
    def test_provider_tool_protocol_sanitizer_removes_orphan_tool_result(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "tool", "tool_call_id": "call-missing", "name": "execute_code", "content": "ok"},
            {"role": "user", "content": "continue"},
        ]

        sanitized = _ensure_provider_tool_protocol(messages)

        self.assertFalse(any(message.get("role") == "tool" for message in sanitized))
        self.assertIn("orphan tool result", sanitized[1]["content"])

    def test_tool_intent_progress_is_semantic(self) -> None:
        stage, detail = tool_intent_progress(
            "zoom_to_layer",
            {"layer_id": "上海饮品店"},
        )

        self.assertEqual(stage, "tool_intent")
        self.assertEqual(detail, "缩放到目标图层 · 上海饮品店")

    def test_xmlish_tool_call_content_is_normalized(self) -> None:
        content = """
我将运行一段代码。
<tool_call>
<function=execute_code>
<parameter=code>
import pandas as pd
print("上海饮品店")
</parameter>
</function>
</tool_call>
"""

        cleaned, tool_calls = _extract_xmlish_tool_calls(content)

        self.assertEqual(cleaned, "我将运行一段代码。")
        self.assertIsNotNone(tool_calls)
        self.assertEqual(tool_calls[0]["function"]["name"], "execute_code")  # type: ignore[index]
        self.assertNotIn("<tool_call>", cleaned or "")
        self.assertIn("上海饮品店", tool_calls[0]["function"]["arguments"])  # type: ignore[index]

    def test_tool_call_turn_text_is_not_streamed_as_visible_answer(self) -> None:
        responses = iter([
            LLMResponse(
                content="请告诉我您想缩放到哪个图层。",
                tool_calls=[
                    {
                        "id": "call_zoom",
                        "type": "function",
                        "function": {
                            "name": "zoom_to_layer",
                            "arguments": '{"layer_id": "roads"}',
                        },
                    }
                ],
            ),
            LLMResponse(content='已缩放到"道路"图层。', tool_calls=None),
        ])
        visible_text: list[str] = []
        progress_events: list[tuple[str, str]] = []
        runtime = FakeToolRuntime()

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: next(responses),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=runtime,  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "zoom_to_layer",
                        "description": "Zoom to layer",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            on_thought_delta=visible_text.append,
            progress_callback=lambda stage, detail: progress_events.append((stage, detail)),
        )

        result = loop.run("缩放到路网")

        self.assertEqual(result, '已缩放到"道路"图层。')
        self.assertEqual(runtime.calls, [("zoom_to_layer", {"layer_id": "roads"})])
        self.assertEqual(visible_text, ['已缩放到"道路"图层。'])
        self.assertIn(("tool_intent", "缩放到目标图层 · roads"), progress_events)

    def test_plain_text_reply_finishes_without_system_nudge(self) -> None:
        visible_text: list[str] = []

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: LLMResponse(content="红色、绿色、蓝色是三类颜色。"),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=FakeToolRuntime(),  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "description": "Run Python",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            on_thought_delta=visible_text.append,
        )

        result = loop.run("把红绿蓝分类一下")

        self.assertEqual(result, "红色、绿色、蓝色是三类颜色。")
        self.assertEqual(visible_text, ["红色、绿色、蓝色是三类颜色。"])
        system_nudges = [
            message
            for message in loop.context.messages
            if message.get("role") == "user" and "[System] You are mid-task" in str(message.get("content", ""))
        ]
        self.assertEqual(system_nudges, [])

    def test_successful_probe_code_does_not_force_final_before_delivery(self) -> None:
        calls: list[list[str]] = []
        responses = iter([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code_1",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(1)"}',
                        },
                    }
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code_2",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(\\"plot saved\\")"}',
                        },
                    }
                ],
            ),
            LLMResponse(content="已完成绘图。", tool_calls=None),
        ])

        def llm_call(_messages, **kwargs):
            calls.append([item["function"]["name"] for item in kwargs.get("tools") or []])
            return next(responses)

        loop = AgentLoop(
            llm_call=llm_call,
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=FakeToolRuntime(),  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "description": "Run Python",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "websearch",
                        "description": "Search web",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            agent_profile=AgentProfile(
                name="short-build",
                mode=AgentMode.BUILD,
                description="Short profile",
                permission_level=PermissionLevel.SAFE_WRITE,
                max_steps=4,
            ),
        )

        result = loop.run("对上海饮品店数据绘制一个组合图")

        self.assertEqual(result, "已完成绘图。")
        self.assertIn("execute_code", calls[0])
        self.assertIn("execute_code", calls[1])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(loop.tool_runtime.calls), 2)  # type: ignore[union-attr]

    def test_provider_budget_final_turn_discards_unexpected_tool_calls(self) -> None:
        responses = iter([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code_1",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(1)"}',
                        },
                    }
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code_2",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(2)"}',
                        },
                    }
                ],
            ),
        ])
        runtime = FakeToolRuntime()

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: next(responses),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=runtime,  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "description": "Run Python",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            agent_profile=AgentProfile(
                name="short-build",
                mode=AgentMode.BUILD,
                description="Short profile",
                permission_level=PermissionLevel.SAFE_WRITE,
                max_steps=4,
                metadata={
                    "max_provider_turns": 1,
                },
            ),
        )

        result = loop.run("绘制上海饮品店组合图")

        self.assertIn("达到预算上限", result)
        self.assertEqual(len(runtime.calls), 1)

    def test_default_build_profile_has_no_fixed_preflight_turn_cap(self) -> None:
        responses = []
        for idx in range(15):
            responses.append(
                LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": f"call_lookup_{idx}",
                            "type": "function",
                            "function": {
                                "name": "list_directory",
                                "arguments": '{"path": "/workspace"}',
                            },
                        }
                    ],
                )
            )
        responses.extend([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(\\"plot saved\\")"}',
                        },
                    }
                ],
            ),
            LLMResponse(content="已完成组合图绘制。", tool_calls=None),
        ])
        response_iter = iter(responses)
        runtime = FakeToolRuntime()

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: next(response_iter),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=runtime,  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List files",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "description": "Run Python",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            agent_profile=AgentProfile.gis_build(),
        )

        result = loop.run("对上海饮品店数据绘制一个组合图")

        self.assertEqual(result, "已完成组合图绘制。")
        self.assertEqual(len(runtime.calls), 16)
        self.assertEqual([name for name, _args in runtime.calls[:-1]], ["list_directory"] * 15)
        self.assertEqual(runtime.calls[-1][0], "execute_code")

    def test_loop_does_not_classify_prompt_text_for_tool_visibility(self) -> None:
        calls: list[list[str]] = []

        def llm_call(_messages, **kwargs):
            calls.append([item["function"]["name"] for item in kwargs.get("tools") or []])
            return LLMResponse(content="红色、绿色、蓝色可以按颜色名称分类。")

        loop = AgentLoop(
            llm_call=llm_call,
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=FakeToolRuntime(),  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "description": "Run Python",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "create_workflow",
                        "description": "Create workflow dag",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "start_subagent",
                        "description": "Launch subagent",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
        )

        result = loop.run("把红绿蓝分类一下")

        self.assertIn("颜色", result)
        self.assertIn("execute_code", calls[0])
        self.assertIn("create_workflow", calls[0])
        self.assertIn("start_subagent", calls[0])

    def test_operation_repair_blocks_bypass_after_failed_run_operation(self) -> None:
        responses = iter([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_run",
                        "type": "function",
                        "function": {
                            "name": "run_operation",
                            "arguments": '{"operation_id": "dbscan_clustering", "params": {}}',
                        },
                    }
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_code",
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "print(\\"bypass\\")"}',
                        },
                    },
                    {
                        "id": "call_layer",
                        "type": "function",
                        "function": {
                            "name": "add_layer",
                            "arguments": '{"geojson_path": "result.geojson", "name": "Bypass"}',
                        },
                    },
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_get",
                        "type": "function",
                        "function": {
                            "name": "get_operation",
                            "arguments": '{"operation_id": "dbscan_clustering", "include_code": true}',
                        },
                    },
                    {
                        "id": "call_edit",
                        "type": "function",
                        "function": {
                            "name": "edit_operation",
                            "arguments": '{"operation_id": "dbscan_clustering", "description": "fixed input contract"}',
                        },
                    },
                ],
            ),
            LLMResponse(content="已回到 operation 修复路径。", tool_calls=None),
        ])
        runtime = OperationRepairFakeRuntime()

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: next(responses),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=ContextManager(),
            tool_runtime=runtime,  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ["run_operation", "execute_code", "add_layer", "get_operation", "edit_operation"]
            ],
        )

        result = loop.run("你要修正我的operation，而不是饶过他自己写脚本")

        self.assertEqual(result, "已回到 operation 修复路径。")
        self.assertEqual(
            [name for name, _args in runtime.calls],
            ["run_operation", "get_operation", "edit_operation"],
        )
        blocked = [
            message for message in loop.context.messages
            if message.get("_meta", {}).get("runner_guard_blocked")
        ]
        self.assertEqual([item.get("name") for item in blocked], ["execute_code", "add_layer"])
        corrections = [
            message for message in loop.context.messages
            if message.get("_meta", {}).get("kind") == "runner_control"
        ]
        self.assertTrue(corrections)

    def test_short_ok_resolves_previous_map_load_offer_and_blocks_rerun(self) -> None:
        responses = iter([
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_bash",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "python3 inspect_results.py"}',
                        },
                    },
                    {
                        "id": "call_scan",
                        "type": "function",
                        "function": {
                            "name": "list_directory",
                            "arguments": '{"path": "/workspace"}',
                        },
                    },
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_layer",
                        "type": "function",
                        "function": {
                            "name": "add_layer",
                            "arguments": '{"geojson_path": "/workspace/测试华师大闵行_POI_clustered.geojson", "name": "聚类结果"}',
                        },
                    },
                    {
                        "id": "call_zoom",
                        "type": "function",
                        "function": {
                            "name": "zoom_to_layer",
                            "arguments": '{"layer_id": "clustered"}',
                        },
                    },
                ],
            ),
            LLMResponse(content="已把上一轮聚类结果加载到地图。", tool_calls=None),
        ])
        context = ContextManager()
        context.add_tool_result(
            "call-op",
            "run_operation",
            (
                '{"success": true, "clustered_path": "/workspace/测试华师大闵行_POI_clustered.geojson", '
                '"centers_path": "/workspace/测试华师大闵行_POI_centers.geojson"}'
            ),
        )
        context.add_assistant_message("测试完成。需要我把结果加载到地图上查看吗？")
        runtime = FakeToolRuntime()
        runtime.workspace_path = "/workspace"

        loop = AgentLoop(
            llm_call=lambda _messages, **_kwargs: next(responses),
            executor_call=lambda _code: None,
            system_prompt="system",
            context=context,
            tool_runtime=runtime,  # type: ignore[arg-type]
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ["bash", "list_directory", "add_layer", "zoom_to_layer"]
            ],
        )

        result = loop.run("ok")

        self.assertEqual(result, "已把上一轮聚类结果加载到地图。")
        self.assertEqual([name for name, _args in runtime.calls], ["add_layer", "zoom_to_layer"])
        blocked = [
            message.get("name")
            for message in loop.context.messages
            if message.get("_meta", {}).get("runner_guard_blocked")
        ]
        self.assertEqual(blocked, ["bash", "list_directory"])
        resolved = [
            message for message in loop.context.messages
            if message.get("_meta", {}).get("pending_intent_kind") == "confirm_map_load"
        ]
        self.assertTrue(resolved)


if __name__ == "__main__":
    unittest.main()
