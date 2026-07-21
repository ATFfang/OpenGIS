"""Provider-facing projection for conversation context.

Raw conversation context is useful for replay, debugging, and run archives,
but it is too noisy to send back to the model on every turn. This module owns
the boundary from stored context events/messages to provider-safe messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from opengis_backend.agent.context.observation import compress_observation

ToolResultPredicate = Callable[[dict], bool]
PlaceholderBuilder = Callable[[dict], str]
WorkflowPredicate = Callable[[dict], bool]


@dataclass(frozen=True)
class ProviderProjectionConfig:
    raw_recent: int = 8
    collapse_old_messages: bool = True
    max_digest_chars: int = 6000
    max_tool_result_chars: int = 4000
    max_tool_call_arg_chars: int = 1400
    max_execute_code_chars: int = 900
    recent_user_turns: int = 4
    max_recent_user_chars: int = 800


class ProviderContextProjector:
    """Project stored conversation messages into provider messages."""

    def __init__(
        self,
        *,
        config: ProviderProjectionConfig | None = None,
        is_tool_result: ToolResultPredicate,
        make_pruned_placeholder: PlaceholderBuilder,
        is_workflow_context_message: WorkflowPredicate,
    ) -> None:
        self.config = config or ProviderProjectionConfig()
        self.is_tool_result = is_tool_result
        self.make_pruned_placeholder = make_pruned_placeholder
        self.is_workflow_context_message = is_workflow_context_message

    def project_live_messages(
        self,
        messages: list[dict],
        *,
        summary_cutoff: int,
        exclude_workflow_context: bool,
    ) -> list[dict[str, Any]]:
        raw_start = max(summary_cutoff, len(messages) - max(0, self.config.raw_recent))
        old_live_messages = [
            msg
            for index, msg in enumerate(messages)
            if summary_cutoff <= index < raw_start
            and not (exclude_workflow_context and self.is_workflow_context_message(msg))
        ]
        recent_user_anchor = self.build_recent_user_anchor(
            messages,
            summary_cutoff=summary_cutoff,
            exclude_workflow_context=exclude_workflow_context,
        )

        result: list[dict[str, Any]] = []
        if recent_user_anchor:
            result.append({"role": "system", "content": recent_user_anchor})
        if self.config.collapse_old_messages:
            digest = self.build_digest(old_live_messages)
            if digest:
                result.append({"role": "system", "content": digest})
        else:
            result.extend(self.message_for_provider(msg, project=True) for msg in old_live_messages)

        for index, msg in enumerate(messages):
            if index < raw_start:
                continue
            if exclude_workflow_context and self.is_workflow_context_message(msg):
                continue
            result.append(self.message_for_provider(msg, project=False))
        return self.ensure_tool_protocol(result)

    def build_recent_user_anchor(
        self,
        messages: list[dict],
        *,
        summary_cutoff: int,
        exclude_workflow_context: bool,
    ) -> str:
        if self.config.recent_user_turns <= 0:
            return ""
        user_messages: list[dict] = []
        for msg in messages[summary_cutoff:]:
            if exclude_workflow_context and self.is_workflow_context_message(msg):
                continue
            if not self.is_real_user_message(msg):
                continue
            user_messages.append(msg)
        selected = user_messages[-self.config.recent_user_turns :]
        if not selected:
            return ""
        lines = [
            "## Recent User Requests (verbatim)",
            "These are the latest user-authored chat requests in order. "
            "Use this list, not assistant summaries or tool logs, to resolve references such as previous request, last step, current requirement, or user's instruction.",
        ]
        last_index = len(selected) - 1
        for idx, msg in enumerate(selected):
            if idx == last_index:
                label = "current"
            elif idx == last_index - 1:
                label = "previous"
            else:
                label = f"previous-{last_index - idx}"
            content = compact_inline(
                str(msg.get("content") or ""),
                self.config.max_recent_user_chars,
            )
            lines.append(f"- {label}: {content}")
        return "\n".join(lines)

    def is_real_user_message(self, msg: dict) -> bool:
        if msg.get("role") != "user":
            return False
        meta = msg.get("_meta")
        if isinstance(meta, dict):
            kind = str(meta.get("kind") or "")
            if kind == "tool_result" or kind.startswith("workflow"):
                return False
        return True

    def message_for_provider(self, msg: dict, *, project: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            k: v for k, v in msg.items() if not k.startswith("_")
        }
        if project and self.is_tool_result(msg):
            content = str(out.get("content") or "")
            if len(content) > self.config.max_tool_result_chars:
                out["content"] = self.project_tool_result_content(msg, content)

        tool_calls = out.get("tool_calls")
        if isinstance(tool_calls, list):
            out["tool_calls"] = [
                self.project_tool_call(tool_call, project=project)
                for tool_call in tool_calls
            ]
            if out.get("role") == "assistant":
                # OpenAI-compatible providers can be strict about assistant
                # messages that mix structured tool_calls with textual
                # pseudo-tool markup. Keep the structured calls as the source
                # of truth and drop any draft XML/markdown content.
                out["content"] = ""
        return out

    def ensure_tool_protocol(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return OpenAI-compatible messages with no orphan ``role=tool`` entries.

        Projection is allowed to trim the raw conversation window. If that cut
        lands between an assistant ``tool_calls`` message and its tool results,
        the OpenAI protocol rejects the whole request. Treat incomplete
        historical tool-call transactions as ordinary system summaries instead
        of leaking half of the structured protocol.
        """
        out: list[dict[str, Any]] = []
        pending_buffer: list[dict[str, Any]] = []
        pending_ids: set[str] = set()

        def flush_pending_as_summary() -> None:
            nonlocal pending_buffer, pending_ids
            if not pending_buffer:
                return
            out.append(self.tool_transaction_summary(pending_buffer, pending_ids))
            pending_buffer = []
            pending_ids = set()

        for msg in messages:
            role = msg.get("role")
            if pending_buffer:
                if role == "tool" and str(msg.get("tool_call_id") or "") in pending_ids:
                    pending_buffer.append(msg)
                    pending_ids.discard(str(msg.get("tool_call_id") or ""))
                    if not pending_ids:
                        out.extend(pending_buffer)
                        pending_buffer = []
                    continue
                flush_pending_as_summary()

            tool_calls = msg.get("tool_calls")
            if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
                ids = {
                    str(tool_call.get("id") or "")
                    for tool_call in tool_calls
                    if isinstance(tool_call, dict) and str(tool_call.get("id") or "")
                }
                if ids:
                    pending_buffer = [msg]
                    pending_ids = ids
                else:
                    out.append(self.tool_transaction_summary([msg], set()))
                continue

            if role == "tool":
                out.append(self.orphan_tool_result_summary(msg))
                continue

            out.append(msg)

        flush_pending_as_summary()
        return out

    def tool_transaction_summary(
        self,
        messages: list[dict[str, Any]],
        missing_ids: set[str],
    ) -> dict[str, Any]:
        assistant = messages[0] if messages else {}
        names: list[str] = []
        tool_calls = assistant.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                fn = tool_call.get("function") if isinstance(tool_call, dict) else None
                if isinstance(fn, dict):
                    names.append(str(fn.get("name") or "unknown"))
        result_notes: list[str] = []
        for msg in messages[1:]:
            if msg.get("role") != "tool":
                continue
            result_notes.append(
                f"{msg.get('name') or 'tool'} id={msg.get('tool_call_id') or '?'} "
                f"chars={len(str(msg.get('content') or ''))}"
            )
        missing = f" missing_ids={','.join(sorted(missing_ids))}" if missing_ids else ""
        results = f" results={'; '.join(result_notes[:6])}" if result_notes else ""
        return {
            "role": "system",
            "content": (
                "[Historical tool-call transaction summarized because the provider "
                "projection did not contain a complete assistant/tool sequence.] "
                f"tool_calls={', '.join(names) or 'unknown'}{missing}{results}"
            ),
        }

    def orphan_tool_result_summary(self, msg: dict[str, Any]) -> dict[str, Any]:
        name = str(msg.get("name") or "tool")
        tool_call_id = str(msg.get("tool_call_id") or "")
        content = compact_inline(str(msg.get("content") or ""), 900)
        return {
            "role": "system",
            "content": (
                "[Historical orphan tool result summarized for provider protocol safety.] "
                f"tool={name} tool_call_id={tool_call_id or '?'} content={content}"
            ),
        }

    def build_digest(self, messages: list[dict]) -> str:
        if not messages:
            return ""
        lines = [
            "## Earlier Live Conversation Digest",
            "Older messages in this same conversation are summarized here. "
            "Use as background only; prefer current tool/state results over this digest.",
        ]
        for idx, msg in enumerate(messages, 1):
            role = str(msg.get("role") or "unknown")
            meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
            kind = str(meta.get("kind") or "")
            if role == "user":
                content = compact_inline(str(msg.get("content") or ""), 500)
                label = "tool-result-as-user" if kind == "tool_result" else "user"
                lines.append(f"- {idx}. {label}: {content}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    names = []
                    arg_notes = []
                    for tool_call in tool_calls[:6]:
                        fn = tool_call.get("function") if isinstance(tool_call, dict) else None
                        if not isinstance(fn, dict):
                            continue
                        name = str(fn.get("name") or "unknown")
                        names.append(name)
                        arguments = str(fn.get("arguments") or "")
                        if arguments:
                            arg_notes.append(f"{name}:args_chars={len(arguments)}")
                    suffix = f" ({'; '.join(arg_notes[:4])})" if arg_notes else ""
                    lines.append(f"- {idx}. assistant tool calls: {', '.join(names) or '(unknown)'}{suffix}")
                else:
                    content = compact_inline(str(msg.get("content") or ""), 500)
                    if content:
                        lines.append(f"- {idx}. assistant: {content}")
            elif role == "tool":
                tool_name = str(msg.get("name") or meta.get("tool_name") or "unknown")
                content = str(msg.get("content") or "")
                refs = message_refs(meta)
                status = "error" if "Error:" in content[:80] or meta.get("had_error") else "ok"
                excerpt = compact_inline(content, 450)
                ref_text = f" refs={'; '.join(refs[:4])}" if refs else ""
                lines.append(
                    f"- {idx}. tool {tool_name}: {status}, chars={len(content)}{ref_text}; excerpt={excerpt}"
                )
            else:
                content = compact_inline(str(msg.get("content") or ""), 400)
                if content:
                    lines.append(f"- {idx}. {role}: {content}")
            current = "\n".join(lines)
            if len(current) > self.config.max_digest_chars:
                return current[: self.config.max_digest_chars] + "\n... [digest truncated] ..."
        return "\n".join(lines)

    def project_tool_result_content(self, msg: dict, content: str) -> str:
        meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
        placeholder = self.make_pruned_placeholder(msg)
        tool_name = str(msg.get("name") or meta.get("tool_name") or "tool")
        excerpt = compress_observation(
            tool_name=tool_name,
            content=content,
            metadata=meta if isinstance(meta, dict) else {},
            max_chars=self.config.max_tool_result_chars,
        )
        return (
            f"{placeholder}\n"
            f"[projected_tool_result] original_chars={len(content)}"
            f" tool={meta.get('tool_name') if isinstance(meta, dict) else ''}\n"
            f"{excerpt}"
        )

    def project_tool_call(self, tool_call: Any, *, project: bool) -> Any:
        if not isinstance(tool_call, dict):
            return tool_call
        projected = dict(tool_call)
        fn = projected.get("function")
        if not isinstance(fn, dict):
            return projected
        next_fn = dict(fn)
        name = str(next_fn.get("name") or "")
        arguments = next_fn.get("arguments")
        if isinstance(arguments, str):
            next_fn["arguments"] = self.project_tool_arguments(name, arguments, project=project)
        else:
            next_fn["arguments"] = "{}"
        projected["function"] = next_fn
        return projected

    def project_tool_arguments(self, tool_name: str, arguments: str, *, project: bool) -> str:
        try:
            parsed = json.loads(arguments)
        except Exception:
            return json.dumps(
                {
                    "_opengis_invalid_arguments": True,
                    "tool": tool_name,
                    "original_chars": len(arguments),
                    "note": "Historical tool arguments were malformed and were omitted from provider context.",
                },
                ensure_ascii=False,
            )
        if not project:
            return arguments
        if len(arguments) <= self.config.max_tool_call_arg_chars:
            return arguments
        if not isinstance(parsed, dict):
            return json.dumps(
                {
                    "_opengis_projected_arguments": True,
                    "tool": tool_name,
                    "original_chars": len(arguments),
                    "note": "Historical non-object tool arguments omitted from model context.",
                },
                ensure_ascii=False,
            )

        next_args: dict[str, Any] = {}
        for key, value in parsed.items():
            if (
                tool_name == "execute_code"
                and key == "code"
                and isinstance(value, str)
                and len(value) > self.config.max_execute_code_chars
            ):
                next_args[key] = (
                    "# Historical execute_code body omitted from provider context.\n"
                    "# Use the following tool result, script_path, or run archive for the outcome."
                )
                next_args["_opengis_projected_code_chars"] = len(value)
            elif isinstance(value, str) and len(value) > self.config.max_tool_call_arg_chars:
                next_args[key] = (
                    value[:300]
                    + "\n... [projected: long argument omitted from model context] ...\n"
                    + value[-200:]
                )
                next_args[f"_opengis_projected_{key}_chars"] = len(value)
            else:
                next_args[key] = value
        next_args["_opengis_projected_arguments"] = True
        next_args["_opengis_original_argument_chars"] = len(arguments)
        return json.dumps(next_args, ensure_ascii=False)


def compact_inline(text: str, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact or "(empty)"
    head = max_chars // 2
    tail = max_chars - head - 40
    return compact[:head] + " ... [omitted] ... " + compact[-max(0, tail):]


def message_refs(meta: dict) -> list[str]:
    refs: list[str] = []
    for key in (
        "artifact_layer_id",
        "artifact_layer_name",
        "artifact_path",
        "script_path",
        "script_abs_path",
        "retained_output_path",
    ):
        value = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(value, str) and value:
            refs.append(f"{key}={value}")
    return refs


__all__ = [
    "ProviderContextProjector",
    "ProviderProjectionConfig",
    "compact_inline",
    "message_refs",
]
