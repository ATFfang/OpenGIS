"""Working-state projection for provider context.

The working state is a compact, dependency-oriented summary of the current
turn/session state. It is not long-term memory; it is rebuilt from live
messages before provider calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkingState:
    current_goal: str = ""
    previous_user_request: str = ""
    active_operation: str = ""
    active_layer: str = ""
    recent_failures: list[str] = field(default_factory=list)
    last_actions: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.current_goal,
                self.previous_user_request,
                self.active_operation,
                self.active_layer,
                self.recent_failures,
                self.last_actions,
            ]
        )

    def to_prompt(self) -> str:
        if self.is_empty():
            return ""
        lines = [
            "## Working State",
            "Compact state inferred from this conversation. Prefer explicit current-state tools when precision matters.",
        ]
        if self.current_goal:
            lines.append(f"- current_goal: {self.current_goal}")
        if self.previous_user_request:
            lines.append(f"- previous_user_request: {self.previous_user_request}")
        if self.active_operation:
            lines.append(f"- active_operation: {self.active_operation}")
        if self.active_layer:
            lines.append(f"- active_layer: {self.active_layer}")
        if self.recent_failures:
            lines.append("- recent_failures:")
            lines.extend(f"  - {item}" for item in self.recent_failures[:4])
        if self.last_actions:
            lines.append("- last_actions:")
            lines.extend(f"  - {item}" for item in self.last_actions[:6])
        return "\n".join(lines)


class WorkingStateProjector:
    """Build a compact working state from live context messages."""

    def project(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_cutoff: int = 0,
        exclude_workflow_context: bool = False,
    ) -> WorkingState:
        live = messages[max(0, summary_cutoff) :]
        user_requests: list[str] = []
        active_operation = ""
        active_layer = ""
        failures: list[str] = []
        actions: list[str] = []

        for message in live:
            if exclude_workflow_context and _is_workflow_context_message(message):
                continue
            if _is_real_user_message(message):
                user_requests.append(_compact(str(message.get("content") or ""), 240))

        for message in reversed(live):
            if exclude_workflow_context and _is_workflow_context_message(message):
                continue
            meta = message.get("_meta") if isinstance(message.get("_meta"), dict) else {}
            if meta.get("kind") != "tool_result":
                continue
            tool_name = str(message.get("name") or meta.get("tool_name") or "")
            content = str(message.get("content") or "")
            data = _parse_json_object(content)
            if not active_operation and tool_name in {"get_operation", "run_operation", "edit_operation", "validate_operation"}:
                active_operation = _operation_label(tool_name, data)
            if not active_layer and tool_name in {
                "add_layer",
                "get_layer",
                "zoom_to_layer",
                "set_categorized_style",
                "set_graduated_style",
                "update_layer_style",
                "set_layer_visual_variables",
            }:
                active_layer = _layer_label(tool_name, data, meta)
            if _failed_tool_result(data, meta, content) and len(failures) < 4:
                error = str(data.get("error") or data.get("message") or meta.get("runner_guard_reason") or "unknown error")
                failures.append(f"{tool_name}: {_compact(error, 220)}")
            elif len(actions) < 6:
                actions.append(_action_label(tool_name, data, meta, content))
            if active_operation and active_layer and len(failures) >= 4 and len(actions) >= 6:
                break

        return WorkingState(
            current_goal=user_requests[-1] if user_requests else "",
            previous_user_request=user_requests[-2] if len(user_requests) >= 2 else "",
            active_operation=active_operation,
            active_layer=active_layer,
            recent_failures=list(reversed(failures)),
            last_actions=list(reversed([item for item in actions if item])),
        )


def _is_workflow_context_message(message: dict[str, Any]) -> bool:
    meta = message.get("_meta")
    if not isinstance(meta, dict):
        return False
    kind = str(meta.get("kind") or "")
    scope = str(meta.get("scope") or "")
    return kind.startswith("workflow") or scope == "workflow"


def _is_real_user_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    meta = message.get("_meta")
    if isinstance(meta, dict):
        kind = str(meta.get("kind") or "")
        if kind == "tool_result" or kind.startswith("workflow"):
            return False
    return True


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _operation_label(tool_name: str, data: dict[str, Any]) -> str:
    operation = data.get("operation")
    if isinstance(operation, dict):
        op_id = str(operation.get("id") or operation.get("operation_id") or "")
        status = str(operation.get("status") or "")
        return " ".join(item for item in [op_id, f"status={status}" if status else ""] if item)
    op_id = str(data.get("operation_id") or data.get("id") or "")
    status = str(data.get("status") or "")
    if op_id or status:
        return " ".join(item for item in [op_id, f"status={status}" if status else ""] if item)
    return tool_name


def _layer_label(tool_name: str, data: dict[str, Any], meta: dict[str, Any]) -> str:
    layer = data.get("layer")
    if isinstance(layer, dict):
        layer_id = str(layer.get("id") or "")
        name = str(layer.get("name") or "")
        count = layer.get("feature_count") or layer.get("count")
        return " ".join(item for item in [name or layer_id, f"features={count}" if count is not None else ""] if item)
    name = str(data.get("layer_name") or data.get("name") or meta.get("artifact_layer_name") or "")
    layer_id = str(data.get("layer_id") or data.get("id") or meta.get("artifact_layer_id") or "")
    return name or layer_id or tool_name


def _failed_tool_result(data: dict[str, Any], meta: dict[str, Any], content: str) -> bool:
    return data.get("success") is False or bool(meta.get("had_error")) or "runner_guard_blocked" in content[:240]


def _action_label(tool_name: str, data: dict[str, Any], meta: dict[str, Any], content: str) -> str:
    target = ""
    if tool_name in {"add_layer", "get_layer", "zoom_to_layer"}:
        target = _layer_label(tool_name, data, meta)
    elif tool_name in {"run_operation", "edit_operation", "validate_operation"}:
        target = _operation_label(tool_name, data)
    elif data.get("path"):
        target = str(data.get("path"))
    if not target:
        target = _compact(content, 120)
    return f"{tool_name}: {target}" if target else tool_name


def _compact(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    head = limit // 2
    tail = max(0, limit - head - 24)
    return compact[:head] + " ... [omitted] ... " + compact[-tail:]


__all__ = [
    "WorkingState",
    "WorkingStateProjector",
]
