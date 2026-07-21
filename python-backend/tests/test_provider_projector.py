import json
import unittest

from opengis_backend.agent.context.provider_projector import (
    ProviderContextProjector,
    ProviderProjectionConfig,
)


def is_tool_result(msg: dict) -> bool:
    meta = msg.get("_meta")
    return isinstance(meta, dict) and meta.get("kind") == "tool_result"


def placeholder(msg: dict) -> str:
    meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
    refs = []
    if meta.get("script_path"):
        refs.append(f"script_path={meta['script_path']}")
    suffix = " — refs: " + "; ".join(refs) if refs else ""
    return f"[Tool result pruned]{suffix} — body removed to save tokens"


class ProviderContextProjectorTests(unittest.TestCase):
    def test_collapses_old_protocol_messages_into_digest(self) -> None:
        projector = ProviderContextProjector(
            config=ProviderProjectionConfig(
                raw_recent=1,
                collapse_old_messages=True,
                recent_user_turns=0,
            ),
            is_tool_result=is_tool_result,
            make_pruned_placeholder=placeholder,
            is_workflow_context_message=lambda msg: False,
        )
        messages = [
            {"role": "user", "content": "绘制组合图"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-code",
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "arguments": json.dumps({"code": "print(1)" * 500}, ensure_ascii=False),
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-code",
                "name": "execute_code",
                "content": "output\n" * 1000,
                "_meta": {"kind": "tool_result", "tool_name": "execute_code", "script_path": "/tmp/a.py"},
            },
            {"role": "user", "content": "继续"},
        ]

        projected = projector.project_live_messages(
            messages,
            summary_cutoff=0,
            exclude_workflow_context=False,
        )

        self.assertEqual([msg["role"] for msg in projected], ["system", "user"])
        self.assertIn("Earlier Live Conversation Digest", projected[0]["content"])
        self.assertIn("assistant tool calls: execute_code", projected[0]["content"])
        self.assertIn("script_path=/tmp/a.py", projected[0]["content"])

    def test_can_keep_old_protocol_shape_with_projected_payloads(self) -> None:
        projector = ProviderContextProjector(
            config=ProviderProjectionConfig(
                raw_recent=1,
                collapse_old_messages=False,
                max_tool_result_chars=300,
                max_tool_call_arg_chars=200,
                max_execute_code_chars=80,
                recent_user_turns=0,
            ),
            is_tool_result=is_tool_result,
            make_pruned_placeholder=placeholder,
            is_workflow_context_message=lambda msg: False,
        )
        long_code = "print('x')\n" * 200
        messages = [
            {"role": "user", "content": "绘制组合图"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-code",
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "arguments": json.dumps({"code": long_code}, ensure_ascii=False),
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-code",
                "name": "execute_code",
                "content": "result\n" * 500,
                "_meta": {"kind": "tool_result", "tool_name": "execute_code"},
            },
            {"role": "user", "content": "继续"},
        ]

        projected = projector.project_live_messages(
            messages,
            summary_cutoff=0,
            exclude_workflow_context=False,
        )

        args = json.loads(projected[1]["tool_calls"][0]["function"]["arguments"])
        self.assertTrue(args["_opengis_projected_arguments"])
        self.assertIn("omitted from provider context", args["code"])
        self.assertIn("[projected_tool_result]", projected[2]["content"])
        self.assertEqual(projected[-1]["content"], "继续")

    def test_raw_recent_orphan_tool_result_is_summarized_as_system(self) -> None:
        projector = ProviderContextProjector(
            config=ProviderProjectionConfig(
                raw_recent=1,
                collapse_old_messages=True,
                recent_user_turns=0,
            ),
            is_tool_result=is_tool_result,
            make_pruned_placeholder=placeholder,
            is_workflow_context_message=lambda msg: False,
        )
        messages = [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-code",
                    "type": "function",
                    "function": {"name": "execute_code", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-code",
                "name": "execute_code",
                "content": "ok",
                "_meta": {"kind": "tool_result", "tool_name": "execute_code"},
            },
        ]

        projected = projector.project_live_messages(
            messages,
            summary_cutoff=0,
            exclude_workflow_context=False,
        )

        self.assertFalse(any(message.get("role") == "tool" for message in projected))
        self.assertTrue(any("orphan tool result" in str(message.get("content", "")) for message in projected))

    def test_complete_recent_tool_transaction_is_preserved(self) -> None:
        projector = ProviderContextProjector(
            config=ProviderProjectionConfig(
                raw_recent=2,
                collapse_old_messages=True,
                recent_user_turns=0,
            ),
            is_tool_result=is_tool_result,
            make_pruned_placeholder=placeholder,
            is_workflow_context_message=lambda msg: False,
        )
        messages = [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-code",
                    "type": "function",
                    "function": {"name": "execute_code", "arguments": "{}"},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call-code",
                "name": "execute_code",
                "content": "ok",
                "_meta": {"kind": "tool_result", "tool_name": "execute_code"},
            },
        ]

        projected = projector.project_live_messages(
            messages,
            summary_cutoff=0,
            exclude_workflow_context=False,
        )

        self.assertTrue(any(isinstance(message.get("tool_calls"), list) for message in projected))
        self.assertTrue(any(message.get("role") == "tool" for message in projected))


if __name__ == "__main__":
    unittest.main()
