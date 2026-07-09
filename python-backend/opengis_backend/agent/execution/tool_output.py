"""Tool output bounding and retention.

OpenGIS tool calls can produce very large stdout, JSON payloads, or tabular
previews. The LLM should see a bounded preview, while the application keeps
the full output for inspection, replay, and artifact indexing.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opengis_backend.agent.telemetry.script_archive import _app_data_base

logger = logging.getLogger(__name__)


DEFAULT_MAX_OUTPUT_LINES = 2000
DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024


@dataclass(frozen=True)
class BoundedToolOutput:
    content: str
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolOutputRuntime:
    """Keep LLM-visible tool output small and retain full output on disk."""

    def __init__(
        self,
        *,
        workspace_path: str | None = None,
        max_lines: int = DEFAULT_MAX_OUTPUT_LINES,
        max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self.workspace_path = workspace_path
        self.max_lines = max(1, int(max_lines))
        self.max_bytes = max(1024, int(max_bytes))

    @property
    def storage_dir(self) -> Path:
        if self.workspace_path:
            return (
                Path(self.workspace_path).expanduser().resolve()
                / ".opengis"
                / "tool-output"
            )
        return _app_data_base() / "agent-tool-output"

    def bound(self, content: str, *, tool_name: str = "tool") -> BoundedToolOutput:
        text = str(content or "")
        byte_count = len(text.encode("utf-8"))
        line_count = self._line_count(text)
        base_metadata = {
            "output_bytes": byte_count,
            "output_lines": line_count,
            "output_max_bytes": self.max_bytes,
            "output_max_lines": self.max_lines,
        }

        if byte_count <= self.max_bytes and line_count <= self.max_lines:
            return BoundedToolOutput(content=text, metadata=base_metadata)

        retained_path = self._write_full_output(tool_name, text)
        preview = self._preview(text)
        preview_bytes = len(preview.encode("utf-8"))
        preview_lines = self._line_count(preview)
        retained_hint = str(retained_path) if retained_path is not None else "unavailable"
        notice = (
            "\n\n[OpenGIS: tool output truncated. "
            f"Original: {byte_count} bytes, {line_count} lines. "
            f"Preview: {preview_bytes} bytes, {preview_lines} lines. "
            f"Full output: {retained_hint}]"
        )
        metadata = {
            **base_metadata,
            "truncated": True,
            "preview_bytes": preview_bytes,
            "preview_lines": preview_lines,
        }
        if retained_path is not None:
            metadata["retained_output_path"] = str(retained_path)
        return BoundedToolOutput(
            content=preview.rstrip() + notice,
            truncated=True,
            metadata=metadata,
        )

    def _write_full_output(self, tool_name: str, content: str) -> Path | None:
        safe_tool = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name).strip("_") or "tool"
        path = self.storage_dir / f"{safe_tool}-{uuid.uuid4().hex}.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception:
            logger.warning("failed to retain full tool output at %s", path, exc_info=True)
            return None
        return path

    def _preview(self, content: str) -> str:
        by_lines = "".join(content.splitlines(keepends=True)[: self.max_lines])
        return self._truncate_utf8(by_lines, self.max_bytes)

    @staticmethod
    def _line_count(content: str) -> int:
        if not content:
            return 0
        return len(content.splitlines()) or 1

    @staticmethod
    def _truncate_utf8(content: str, max_bytes: int) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) <= max_bytes:
            return content
        return encoded[:max_bytes].decode("utf-8", errors="ignore")


__all__ = [
    "BoundedToolOutput",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MAX_OUTPUT_LINES",
    "ToolOutputRuntime",
]
