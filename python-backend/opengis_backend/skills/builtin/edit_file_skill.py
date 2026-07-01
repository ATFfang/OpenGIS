"""edit_file skill — precise string replacement (Claude Code / Cursor style).

Replaces old_string with new_string in a file.
Tries exact match first, then line-trimmed match (ignores trailing whitespace).
Fails clearly if no unique match — the LLM should read_file first and retry.
"""

from __future__ import annotations

import difflib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

# Per-file lock to prevent concurrent edits.
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_MAX_OUTPUT_CHARS = 2000


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _detect_line_ending(text: str) -> str:
    for line in text.split("\n"):
        if line.endswith("\r"):
            return "\r\n"
    return "\n"


def _find_all(content: str, old: str) -> list[int]:
    """Return all start indices of old in content."""
    indices = []
    idx = 0
    while True:
        idx = content.find(old, idx)
        if idx == -1:
            break
        indices.append(idx)
        idx += 1
    return indices


def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> str | None:
    """Try exact match, then line-trimmed match. Return new content or None."""
    # Strategy 1: Exact match
    indices = _find_all(content, old)
    if indices:
        if not replace_all and len(indices) > 1:
            return None  # ambiguous — caller reports error
        if replace_all:
            for idx in reversed(indices):
                content = content[:idx] + new + content[idx + len(old):]
            return content
        return content[:indices[0]] + new + content[indices[0] + len(old):]

    # Strategy 2: Line-trimmed match (ignore trailing whitespace per line)
    lines_c = [ln.rstrip() for ln in content.splitlines(True)]
    lines_o = [ln.rstrip() for ln in old.splitlines(True)]
    joined_c = "".join(lines_c)
    joined_o = "".join(lines_o)
    idx = joined_c.find(joined_o)
    if idx != -1:
        return content[:idx] + new + content[idx + len(old):]

    return None


def _edit_sync(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}",
            "diff": None,
        }
    if not path.is_file():
        return {
            "success": False,
            "error": f"Not a file: {file_path}",
            "diff": None,
        }

    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"success": False, "error": f"Cannot read file: {e}", "diff": None}

    if old_string == new_string:
        return {
            "success": False,
            "error": "old_string and new_string must be different",
            "diff": None,
        }

    # Normalize line endings for matching
    orig_norm = _normalize_line_endings(original)
    old_norm = _normalize_line_endings(old_string)
    new_norm = _normalize_line_endings(new_string)
    ending = _detect_line_ending(original)

    result = _apply_edit(orig_norm, old_norm, new_norm, replace_all)
    if result is None:
        # Provide a helpful error: tell the LLM what went wrong and
        # suggest reading the file first.
        sample = old_string[:120].replace("\n", "\\n")
        return {
            "success": False,
            "error": (
                f"old_string not found in {file_path}. "
                f"Make sure you copy the exact text from read_file output. "
                f"You may need to read_file first to see the current content. "
                f"(searched for: \"{sample}{'...' if len(old_string) > 120 else ''}\")"
            ),
            "diff": None,
        }

    # Restore original line ending style
    if ending != "\n":
        result = result.replace("\n", ending)

    try:
        path.write_text(result, encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"Failed to write file: {e}", "diff": None}

    # Generate a simple unified diff for the response
    old_lines = original.splitlines(True)
    new_lines = result.splitlines(True)
    diff = "\n".join(
        difflib.unified_diff(
            old_lines, new_lines, fromfile=file_path, tofile=file_path, lineterm=""
        )
    )
    diff_preview = diff[:_MAX_OUTPUT_CHARS] + ("..." if len(diff) > _MAX_OUTPUT_CHARS else "")

    return {
        "success": True,
        "error": None,
        "diff": diff_preview,
        "path": file_path,
    }


@skill(
    name="edit_file",
    display_name="Edit File",
    description=(
        "Edit a file by replacing old_string with new_string. "
        "old_string must be an EXACT match of the file content (copy from "
        "read_file output). Trailing whitespace differences are tolerated. "
        "If the match fails, read_file first to see the current content. "
        "Use write_file for new files; edit_file for modifying existing ones."
    ),
    category="system",
    needs_context=True,
    params=[
        {"name": "file_path", "type": "string",
         "description": "Absolute path to the file to edit."},
        {"name": "old_string", "type": "string",
         "description": "The exact text to replace (copy from read_file output)."},
        {"name": "new_string", "type": "string",
         "description": "The text to replace it with."},
        {"name": "replace_all", "type": "boolean",
         "description": "Replace all occurrences (default false)."},
    ],
    returns="dict with keys: success (bool), error (str|null), diff (str)",
    examples=[
        "edit_file('/workspace/main.py', 'def foo():', 'def foo():\\n    return 42')",
    ],
)
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Edit file with precise matching.

    NOTE: Synchronous on purpose — invoked from the agent's tool-bridge
    worker thread with no event loop.
    """
    # Per-file lock to serialize concurrent edits to the same path.
    lock_key = str(Path(file_path).resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.setdefault(lock_key, threading.Lock())
    with lock:
        return _edit_sync(file_path, old_string, new_string, replace_all)
