"""Failure-memory extraction and projection.

Failure memory is intentionally narrower than general project memory: it only
stores lessons when a failed tool path is followed by a verified successful
settlement in the same run. This keeps transient errors from becoming durable
misguidance.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opengis_backend.runs.archive import RunArchive
from opengis_backend.workspace.memory_store import MemoryRecord, MemoryStore

_PATH_RE = re.compile(r"(/[\w\u4e00-\u9fff ._\-/]+)")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_TRACE_LINE_RE = re.compile(r'File ".*?", line \d+, in .*')

_REPAIR_TOOLS = {
    "edit_operation",
    "edit_file",
    "write_file",
    "apply_patch",
    "bash",
}


@dataclass(frozen=True)
class FailureSignature:
    tool_name: str
    error_type: str
    normalized_error: str
    args_keys: tuple[str, ...]
    target_kind: str
    target_id: str

    @property
    def fingerprint(self) -> str:
        raw = "|".join(
            [
                self.tool_name,
                self.error_type,
                self.normalized_error,
                ",".join(self.args_keys),
                self.target_kind,
                self.target_id,
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def query_text(self) -> str:
        return " ".join(
            item
            for item in [
                self.tool_name,
                self.error_type,
                self.normalized_error,
                self.target_kind,
                self.target_id,
            ]
            if item
        )


class FailureMemoryExtractor:
    """Derive durable failure lessons from a completed run archive."""

    def extract_run(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
    ) -> list[MemoryRecord]:
        calls = run_archive.read_tool_calls()
        if not calls:
            return []

        records: list[MemoryRecord] = []
        for index, call in enumerate(calls):
            if not _is_failed_call(call):
                continue
            signature = failure_signature(call)
            if signature is None:
                continue
            success_index = _find_verified_success(calls, index, signature)
            if success_index is None:
                continue
            repair_tools = _repair_tools_between(calls[index + 1 : success_index])
            if not repair_tools and signature.tool_name == "run_operation":
                continue
            records.append(
                MemoryRecord.create(
                    kind="failure_lesson",
                    scope="tool_failure",
                    title=f"{signature.tool_name}: {signature.error_type}",
                    content=_lesson_content(
                        user_message=user_message,
                        final_answer=final_answer,
                        failure=call,
                        success=calls[success_index],
                        signature=signature,
                        repair_tools=repair_tools,
                    ),
                    tags=[
                        "failure",
                        "lesson",
                        signature.tool_name,
                        signature.error_type,
                        signature.target_kind,
                    ],
                    source_run_id=run_archive.run_id,
                    confidence=0.86 if repair_tools else 0.74,
                    metadata={
                        "fingerprint": signature.fingerprint,
                        "tool_name": signature.tool_name,
                        "error_type": signature.error_type,
                        "normalized_error": signature.normalized_error,
                        "args_keys": list(signature.args_keys),
                        "target_kind": signature.target_kind,
                        "target_id": signature.target_id,
                        "repair_tools": repair_tools,
                        "verified_by_tool": str(calls[success_index].get("name") or ""),
                    },
                )
            )
        return _dedupe_by_fingerprint(records)


class FailureMemoryProjector:
    """Retrieve and format relevant failure lessons for a task or failure."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path
        self.store = MemoryStore(workspace_path)

    def project(self, query: str, *, limit: int = 4) -> str:
        if not self.workspace_path:
            return ""
        records = self.store.search(query, kinds=["failure_lesson"], limit=limit)
        if not records:
            return ""
        return self.format(records)

    @staticmethod
    def format(records: list[MemoryRecord]) -> str:
        lines = [
            "Relevant learned failure lessons. Use them only when the current task/error matches; "
            "prefer current tool state over stale lessons.",
        ]
        for record in records[:6]:
            source = f" source={record.source_run_id}" if record.source_run_id else ""
            title = f"{record.title}: " if record.title else ""
            lines.append(f"- [{record.id[:8]}]{source} {title}{record.content[:700]}")
        return "\n".join(lines).strip()


def failure_signature(call: dict[str, Any]) -> FailureSignature | None:
    tool_name = str(call.get("name") or "").strip()
    if not tool_name:
        return None
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    error = _call_error(call)
    if not error:
        return None
    target_kind, target_id = _target(args)
    normalized = normalize_error(error)
    error_type = _error_type(error)
    return FailureSignature(
        tool_name=tool_name,
        error_type=error_type,
        normalized_error=normalized,
        args_keys=tuple(sorted(str(key) for key in args.keys())),
        target_kind=target_kind,
        target_id=target_id,
    )


def normalize_error(error: str) -> str:
    text = " ".join(str(error or "").split())
    text = _TRACE_LINE_RE.sub("File <path>, line <n>", text)
    text = _PATH_RE.sub("<path>", text)
    text = _HEX_RE.sub("<id>", text)
    text = _NUMBER_RE.sub("<n>", text)
    return text[:360]


def _lesson_content(
    *,
    user_message: str,
    final_answer: str,
    failure: dict[str, Any],
    success: dict[str, Any],
    signature: FailureSignature,
    repair_tools: list[str],
) -> str:
    lines = [
        f"Task: {user_message.strip()[:240]}",
        f"Failure fingerprint: {signature.fingerprint}",
        f"Symptom: {signature.tool_name} failed with {signature.error_type}: {signature.normalized_error}",
    ]
    if signature.target_id:
        lines.append(f"Target: {signature.target_kind}={signature.target_id}")
    if repair_tools:
        lines.append("Verified fix path: " + " -> ".join(repair_tools + [str(success.get("name") or signature.tool_name)]))
    else:
        lines.append(f"Verified fix path: retry {success.get('name') or signature.tool_name} with corrected inputs.")
    lines.append("Avoid: retrying the same failed call unchanged or bypassing the target object before repair.")
    if final_answer:
        lines.append(f"Outcome: {final_answer.strip()[:360]}")
    raw_error = _call_error(failure)
    if raw_error and raw_error != signature.normalized_error:
        lines.append(f"Raw error preview: {' '.join(raw_error.split())[:300]}")
    return "\n".join(lines)


def _find_verified_success(calls: list[dict[str, Any]], failure_index: int, signature: FailureSignature) -> int | None:
    for index in range(failure_index + 1, len(calls)):
        call = calls[index]
        if _is_failed_call(call):
            continue
        if str(call.get("name") or "") != signature.tool_name:
            continue
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        _, target_id = _target(args)
        if target_id == signature.target_id:
            return index
        if not signature.target_id:
            return index
    return None


def _repair_tools_between(calls: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for call in calls:
        name = str(call.get("name") or "")
        if name in _REPAIR_TOOLS and name not in out:
            out.append(name)
    return out[:6]


def _is_failed_call(call: dict[str, Any]) -> bool:
    if call.get("error"):
        return True
    if str(call.get("status") or "").lower() == "error":
        return True
    try:
        content = json.loads(str(call.get("output") or ""))
    except Exception:
        content = None
    return isinstance(content, dict) and content.get("success") is False


def _call_error(call: dict[str, Any]) -> str:
    if call.get("error"):
        return str(call.get("error") or "")
    try:
        content = json.loads(str(call.get("output") or ""))
    except Exception:
        return ""
    if isinstance(content, dict):
        return str(content.get("error") or content.get("message") or "")
    return ""


def _error_type(error: str) -> str:
    text = str(error or "").strip()
    match = re.match(r"([A-Za-z_][\w.]*)(?::|\(|$)", text)
    if match:
        return match.group(1).split(".")[-1]
    if "KeyError" in text:
        return "KeyError"
    if "ModuleNotFoundError" in text:
        return "ModuleNotFoundError"
    return "ToolError"


def _target(args: dict[str, Any]) -> tuple[str, str]:
    for key in ("operation_id", "operation_name", "name"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return ("operation" if "operation" in key else "named_target", value.strip())
    for key in ("layer_id", "layer_name"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return ("layer", value.strip())
    for key in ("path", "file_path", "input_path", "script_path", "geojson_path", "csv_path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return ("file", Path(value).name)
    return "", ""


def _dedupe_by_fingerprint(records: list[MemoryRecord]) -> list[MemoryRecord]:
    seen: set[str] = set()
    out: list[MemoryRecord] = []
    for record in records:
        fingerprint = str(record.metadata.get("fingerprint") or record.content[:120])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(record)
    return out[:8]


__all__ = [
    "FailureMemoryExtractor",
    "FailureMemoryProjector",
    "FailureSignature",
    "failure_signature",
    "normalize_error",
]
