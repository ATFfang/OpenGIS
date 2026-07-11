"""Tool argument contract runtime.

LLMs see JSON schemas, then produce plain dictionaries.  This module is the
thin contract layer between provider output and Python callables: normalize
common aliases, coerce simple JSON-schema types, and return structured repair
errors before Python raises low-signal ``TypeError`` exceptions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolArgumentIssue:
    code: str
    message: str
    fields: list[str] = field(default_factory=list)
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.fields:
            payload["fields"] = self.fields
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(frozen=True)
class ToolArgumentResult:
    arguments: dict[str, Any]
    issues: list[ToolArgumentIssue] = field(default_factory=list)
    normalized_from: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues


class ToolArgumentContract:
    """Normalize and validate one tool call against its provider schema."""

    def __init__(self, tool_schemas: list[dict[str, Any]] | None) -> None:
        self._schemas = {
            self._schema_name(schema): schema
            for schema in (tool_schemas or [])
            if self._schema_name(schema)
        }

    def prepare(self, tool_name: str, arguments: dict[str, Any]) -> ToolArgumentResult:
        schema = self._schemas.get(tool_name)
        if not schema:
            return ToolArgumentResult(arguments=dict(arguments or {}))

        properties = self._properties(schema)
        required = self._required(schema)
        if not properties:
            unknown = sorted((arguments or {}).keys())
            if unknown:
                return ToolArgumentResult(
                    arguments={},
                    issues=[
                        ToolArgumentIssue(
                            code="unknown_arguments",
                            message=f"{tool_name} does not accept arguments.",
                            fields=unknown,
                            hint="Call the tool with an empty argument object.",
                        )
                    ],
                )
            return ToolArgumentResult(arguments={})

        normalized, normalized_from, issues = self._normalize(tool_name, arguments or {}, properties)
        if issues:
            return ToolArgumentResult(normalized, issues, normalized_from)

        missing = [name for name in required if _is_missing(normalized.get(name))]
        if missing:
            issues.append(
                ToolArgumentIssue(
                    code="missing_required_arguments",
                    message=f"{tool_name} is missing required argument(s): {', '.join(missing)}.",
                    fields=missing,
                    hint=f"Accepted arguments: {', '.join(properties.keys())}.",
                )
            )

        coerced: dict[str, Any] = {}
        for key, value in normalized.items():
            coerced_value, issue = _coerce_value(tool_name, key, value, properties.get(key, {}))
            coerced[key] = coerced_value
            if issue:
                issues.append(issue)

        return ToolArgumentResult(coerced, issues, normalized_from)

    @staticmethod
    def error_payload(
        tool_name: str,
        result: ToolArgumentResult,
        *,
        accepted: list[str],
    ) -> dict[str, Any]:
        return {
            "success": False,
            "error": "invalid_tool_arguments",
            "tool": tool_name,
            "accepted": accepted,
            "normalized_from": result.normalized_from,
            "issues": [issue.to_dict() for issue in result.issues],
            "retry": "Retry the same tool with the accepted canonical argument names.",
        }

    def accepted_arguments(self, tool_name: str) -> list[str]:
        schema = self._schemas.get(tool_name)
        if not schema:
            return []
        return list(self._properties(schema).keys())

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        return str(fn.get("name") or "") if isinstance(fn, dict) else ""

    @staticmethod
    def _properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
        fn = schema.get("function") if isinstance(schema, dict) else None
        params = fn.get("parameters") if isinstance(fn, dict) else None
        props = params.get("properties") if isinstance(params, dict) else None
        return props if isinstance(props, dict) else {}

    @staticmethod
    def _required(schema: dict[str, Any]) -> list[str]:
        fn = schema.get("function") if isinstance(schema, dict) else None
        params = fn.get("parameters") if isinstance(fn, dict) else None
        required = params.get("required") if isinstance(params, dict) else None
        return [str(item) for item in required] if isinstance(required, list) else []

    def _normalize(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        properties: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, str], list[ToolArgumentIssue]]:
        accepted = set(properties.keys())
        normalized_lookup = {_normalize_name(name): name for name in accepted}
        out: dict[str, Any] = {}
        normalized_from: dict[str, str] = {}
        unknown: list[str] = []
        conflicts: list[str] = []

        for key, value in arguments.items():
            canonical = key if key in accepted else normalized_lookup.get(_normalize_name(key))
            if canonical is None:
                canonical = _semantic_alias(key, accepted)
            if canonical is None:
                unknown.append(key)
                continue
            if canonical in out and out[canonical] != value:
                conflicts.append(key)
                continue
            out[canonical] = value
            if canonical != key:
                normalized_from[key] = canonical

        issues: list[ToolArgumentIssue] = []
        if unknown:
            issues.append(
                ToolArgumentIssue(
                    code="unknown_arguments",
                    message=f"{tool_name} received unknown argument(s): {', '.join(sorted(unknown))}.",
                    fields=sorted(unknown),
                    hint=f"Accepted arguments: {', '.join(properties.keys())}.",
                )
            )
        if conflicts:
            issues.append(
                ToolArgumentIssue(
                    code="conflicting_arguments",
                    message=f"{tool_name} received aliases that conflict with canonical argument values.",
                    fields=sorted(conflicts),
                    hint="Keep one canonical argument name and remove the alias.",
                )
            )
        return out, normalized_from, issues


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _semantic_alias(name: str, accepted: set[str]) -> str | None:
    raw = str(name or "")
    norm = _normalize_name(raw)
    alias_groups: dict[str, set[str]] = {
        "path": {
            "filepath",
            "file",
            "rasterpath",
            "tiffpath",
            "tifpath",
            "sourcepath",
            "datapath",
            "inputpath",
        },
        "geojson_path": {
            "path",
            "filepath",
            "file",
            "vectorpath",
            "datapath",
            "inputpath",
        },
        "script_path": {"path", "filepath", "script", "scriptfile", "pythonpath"},
        "layer_id": {"layer", "layerid", "id"},
        "operation_id": {"operation", "operationid"},
    }
    for canonical, aliases in alias_groups.items():
        if canonical in accepted and norm in aliases:
            return canonical

    path_params = [item for item in accepted if item == "path" or item.endswith("_path")]
    if len(path_params) == 1 and norm in {"path", "file", "filepath"}:
        return path_params[0]
    if "path" in accepted and norm.endswith("path"):
        return "path"
    return None


def _coerce_value(
    tool_name: str,
    key: str,
    value: Any,
    schema: dict[str, Any],
) -> tuple[Any, ToolArgumentIssue | None]:
    expected = schema.get("type")
    if value is None or expected is None:
        return value, None
    try:
        if expected == "number":
            if isinstance(value, bool):
                raise ValueError("boolean is not a number")
            if isinstance(value, (int, float)):
                return value, None
            if isinstance(value, str) and value.strip():
                return float(value), None
        elif expected == "boolean":
            if isinstance(value, bool):
                return value, None
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "y", "是"}:
                    return True, None
                if lowered in {"false", "0", "no", "n", "否"}:
                    return False, None
        elif expected == "array":
            if isinstance(value, list):
                return value, None
            if isinstance(value, tuple):
                return list(value), None
            if isinstance(value, str):
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed, None
        elif expected == "object":
            if isinstance(value, dict):
                return value, None
            if isinstance(value, str):
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed, None
        elif expected == "string":
            if isinstance(value, str):
                return value, None
            if isinstance(value, (int, float, bool)):
                return str(value), None
    except Exception:
        pass

    return value, ToolArgumentIssue(
        code="invalid_argument_type",
        message=f"{tool_name}.{key} expected {expected}, got {type(value).__name__}.",
        fields=[key],
        hint=f"Pass `{key}` as {expected}.",
    )


__all__ = [
    "ToolArgumentContract",
    "ToolArgumentIssue",
    "ToolArgumentResult",
]
