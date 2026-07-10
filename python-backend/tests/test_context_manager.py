import unittest
import json

from opengis_backend.agent.context.context_manager import ContextManager


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

        digest = messages[1]["content"]

        self.assertEqual(messages[1]["role"], "system")
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

        projected_call = messages[2]["tool_calls"][0]["function"]["arguments"]
        projected_args = json.loads(projected_call)
        self.assertTrue(projected_args["_opengis_projected_arguments"])
        self.assertIn("omitted from provider context", projected_args["code"])
        self.assertLess(len(projected_call), 500)
        self.assertIn("[projected_tool_result]", messages[3]["content"])

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

        recent_args = json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(recent_args["code"], long_code)
        self.assertEqual(messages[3]["content"], "error\n" * 200)

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

        anchor = messages[1]["content"]
        self.assertIn("Recent User Requests", anchor)
        self.assertIn("- previous: 你要修正我的operation，而不是饶过他自己写脚本", anchor)
        self.assertIn("- current: 你说按照我的要求，我上一步的要求是啥", anchor)
        self.assertLess(anchor.find("你要修正我的operation"), anchor.find("你说按照我的要求"))


if __name__ == "__main__":
    unittest.main()
