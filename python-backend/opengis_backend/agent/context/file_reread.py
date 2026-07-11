"""Recently edited file re-read helpers for context compaction."""

from __future__ import annotations

import logging
from pathlib import Path


logger = logging.getLogger(__name__)


def track_recent_file_edit(recent_files: list[str], file_path: str, *, limit: int = 5) -> None:
    """Track an edited file path using LRU ordering."""
    abs_path = str(Path(file_path).resolve())
    if abs_path in recent_files:
        recent_files.remove(abs_path)
    recent_files.append(abs_path)
    if len(recent_files) > limit:
        del recent_files[: len(recent_files) - limit]


def read_file_for_reread(file_path: str, *, max_chars: int) -> str | None:
    """Read one edited file for re-injection after context compression."""
    try:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return f"[File no longer exists: {file_path}]"

        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return f"[File too large to re-read: {len(content)} chars, max {max_chars}]"

        return content
    except Exception as exc:
        logger.warning("Failed to re-read %s: %s", file_path, exc)
        return f"[Failed to re-read: {exc}]"


def build_reread_message(recent_files: list[str], *, max_chars: int) -> str | None:
    """Build and clear the automatic file re-read message."""
    if not recent_files:
        return None

    parts = ["[Auto-reread after compression: Recently edited files]"]
    for file_path in recent_files:
        content = read_file_for_reread(file_path, max_chars=max_chars)
        if content:
            parts.append(f"\n--- {file_path} ---\n{content}")

    recent_files.clear()
    return "\n".join(parts) if len(parts) > 1 else None


__all__ = [
    "build_reread_message",
    "read_file_for_reread",
    "track_recent_file_edit",
]
