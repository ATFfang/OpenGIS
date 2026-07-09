"""Small helper functions for tool runtime results and arguments."""

from __future__ import annotations

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    """Best-effort parser for function-call arguments."""
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        logger.warning("Malformed tool arguments: %.200s", raw_args)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def stringify_tool_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def merge_metadata(*items: dict[str, Any] | None) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for item in items:
        if item:
            merged.update(item)
    return merged or None


def artifact_hints(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hints: dict[str, Any] = {}
    path = data.get("path") or data.get("output_path") or data.get("save_path")
    if isinstance(path, str) and path:
        hints["artifact_path"] = path
    layer_id = data.get("layer_id")
    if isinstance(layer_id, str) and layer_id:
        hints["artifact_layer_id"] = layer_id
        hints["artifact_layer_name"] = data.get("name", layer_id)
    return hints or None


def tool_title(tool_name: str) -> str:
    if tool_name == "execute_code":
        return "Execute Python"
    return tool_name.replace("_", " ").title()


def permission_metadata(decision: Any) -> dict[str, Any] | None:
    if decision is None:
        return None
    action = getattr(decision, "action", None)
    return {
        "permission": getattr(action, "value", None),
        "permission_reason": getattr(decision, "reason", ""),
        "permission_rule": getattr(decision, "rule", ""),
    }


__all__ = [
    "artifact_hints",
    "merge_metadata",
    "parse_tool_arguments",
    "permission_metadata",
    "stringify_tool_value",
    "tool_title",
]
