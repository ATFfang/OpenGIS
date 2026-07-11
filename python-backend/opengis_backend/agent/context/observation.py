"""Observation compression for tool results.

LLMs should not see full logs, full GeoJSON, or full tabular previews unless
the next action truly needs raw bytes. This module keeps model-facing
observations as summary + evidence + pointers while raw output remains in the
run archive/tool-output store.
"""

from __future__ import annotations

import json
from typing import Any

from opengis_backend.agent.telemetry.artifacts import artifact_pointer_from_metadata


SUMMARY_KEYS = (
    "success",
    "error",
    "message",
    "summary",
    "status",
    "path",
    "output_path",
    "save_path",
    "layer_id",
    "name",
    "operation_id",
    "worker_id",
    "count",
    "feature_count",
    "row_count",
    "total_rows",
    "fields",
    "columns",
)


def compress_observation(
    *,
    tool_name: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    max_chars: int = 1800,
) -> str:
    text = str(content or "")
    metadata = metadata or {}
    if len(text) <= max_chars:
        return text
    json_summary = _compress_json(tool_name=tool_name, text=text, metadata=metadata, max_chars=max_chars)
    if json_summary:
        return json_summary
    return _compress_text(tool_name=tool_name, text=text, metadata=metadata, max_chars=max_chars)


def _compress_json(
    *,
    tool_name: str,
    text: str,
    metadata: dict[str, Any],
    max_chars: int,
) -> str:
    try:
        data = json.loads(text)
    except Exception:
        return ""

    summary: dict[str, Any] = {
        "observation_compressed": True,
        "tool": tool_name,
        "original_chars": len(text),
    }
    pointer = _artifact_pointer(metadata)
    if pointer:
        summary["artifact_pointer"] = pointer

    if isinstance(data, dict):
        for key in SUMMARY_KEYS:
            if key in data:
                summary[key] = _small_value(data[key])
        collection_notes = _collection_notes(data)
        if collection_notes:
            summary["collections"] = collection_notes
        evidence = _evidence_from_dict(data)
        if evidence:
            summary["evidence"] = evidence
    elif isinstance(data, list):
        summary["items"] = len(data)
        summary["sample"] = [_small_value(item) for item in data[:5]]
    else:
        summary["value"] = _small_value(data)

    encoded = json.dumps(summary, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    summary["note"] = "Compressed observation exceeded budget; sample fields were reduced."
    for key in ("evidence", "collections", "sample"):
        if key in summary:
            summary[key] = _small_value(summary[key])
    encoded = json.dumps(summary, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    return encoded[:max_chars] + "\n... [compressed observation truncated] ..."


def _compress_text(
    *,
    tool_name: str,
    text: str,
    metadata: dict[str, Any],
    max_chars: int,
) -> str:
    pointer = _artifact_pointer(metadata)
    head = max(300, int(max_chars * 0.55))
    tail = max(200, max_chars - head - 260)
    payload = {
        "observation_compressed": True,
        "tool": tool_name,
        "original_chars": len(text),
        "artifact_pointer": pointer,
        "head": text[:head],
        "tail": text[-tail:] if tail > 0 else "",
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _artifact_pointer(metadata: dict[str, Any]) -> dict[str, Any]:
    return artifact_pointer_from_metadata(metadata)


def _collection_notes(data: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for key, value in data.items():
        if isinstance(value, list):
            notes.append({"key": key, "items": len(value), "sample": [_small_value(item) for item in value[:3]]})
        elif isinstance(value, dict) and len(value) > 12:
            notes.append({"key": key, "keys": len(value), "sample_keys": list(value.keys())[:12]})
    return notes[:8]


def _evidence_from_dict(data: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for key in ("features", "rows", "records", "results", "items"):
        value = data.get(key)
        if isinstance(value, list) and value:
            for item in value[:5]:
                evidence.append({"source": key, "sample": _small_value(item)})
            break
    return evidence


def _small_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 240 else value[:120] + " ... [omitted] ... " + value[-80:]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_small_value(item) for item in value[:8]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 12:
                out["_omitted_keys"] = len(value) - idx
                break
            out[str(key)] = _small_value(item)
        return out
    return str(value)[:240]


__all__ = ["compress_observation"]
