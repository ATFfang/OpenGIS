import unittest

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.execution.tool_result import ToolExecutionResult
from opengis_backend.agent.governance.profile import AgentMode, AgentProfile, PermissionLevel
from opengis_backend.agent.llm import LLMResponse, _extract_xmlish_tool_calls
from opengis_backend.agent.loop.agent_loop import AgentLoop


class FakeToolRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, arguments: dict) -> ToolExecutionResult:
        self.calls.append((name, arguments))
        return ToolExecutionResult(name=name, arguments=arguments, content='{"success": true}', duration_ms=0)


class AgentLoopFunctionCallTests(unittest.TestCase):
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
        )

        result = loop.run("缩放到路网")

        self.assertEqual(result, '已缩放到"道路"图层。')
        self.assertEqual(runtime.calls, [("zoom_to_layer", {"layer_id": "roads"})])
        self.assertEqual(visible_text, ['已缩放到"道路"图层。'])

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
                metadata={
                    "tool_schema_budget": 8,
                },
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
                    "tool_schema_budget": 8,
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


if __name__ == "__main__":
    unittest.main()
