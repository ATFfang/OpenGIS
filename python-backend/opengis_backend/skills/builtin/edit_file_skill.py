"""edit_file skill — robust string replacement for agent edits.

Replaces old_string with new_string in a file.
Requires read_file first for stale-write protection, then tries exact match
before progressively fuzzier unique-match strategies.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import skill
from opengis_backend.skills.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.skills.builtin._file_state import get_read_fingerprint, file_matches_fingerprint

logger = logging.getLogger(__name__)

# Per-file lock to prevent concurrent edits.
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_MAX_OUTPUT_CHARS = 8000


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


def _line_start_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    return offsets


def _replace_by_ranges(content: str, ranges: list[tuple[int, int]], new: str, replace_all: bool) -> tuple[str | None, str | None]:
    if not ranges:
        return None, "not_found"
    if not replace_all and len(ranges) > 1:
        return None, "ambiguous"
    result = content
    selected = ranges if replace_all else ranges[:1]
    for start, end in reversed(selected):
        result = result[:start] + new + result[end:]
    return result, None


def _line_window_ranges(content: str, old: str, key) -> list[tuple[int, int]]:
    content_lines = content.splitlines(True)
    old_lines = old.splitlines(True)
    if not old_lines:
        return []
    old_key = [key(line) for line in old_lines]
    offsets = _line_start_offsets(content)
    ranges: list[tuple[int, int]] = []
    width = len(old_lines)
    for idx in range(0, len(content_lines) - width + 1):
        if [key(line) for line in content_lines[idx:idx + width]] == old_key:
            start = offsets[idx]
            end = offsets[idx + width] if idx + width < len(offsets) else len(content)
            ranges.append((start, end))
    return ranges


def _indent_of_first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return re.match(r"[ \t]*", line).group(0)  # type: ignore[union-attr]
    return ""


def _reindent(new: str, old_indent: str, target_indent: str) -> str:
    if old_indent == target_indent:
        return new
    result = []
    for line in new.splitlines(True):
        if line.startswith(old_indent):
            result.append(target_indent + line[len(old_indent):])
        else:
            result.append(line)
    return "".join(result)


def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> dict[str, Any]:
    """Apply edit using exact-first, progressively fuzzier matching."""
    strategies: list[tuple[str, str, str]] = [
        ("exact", old, new),
        ("strip_outer_whitespace", old.strip(), new.strip()),
        ("tabs_as_spaces", old.replace("\t", "    "), new.replace("\t", "    ")),
    ]
    for strategy, old_value, new_value in strategies:
        if not old_value:
            continue
        indices = _find_all(content, old_value)
        result, error = _replace_by_ranges(
            content,
            [(idx, idx + len(old_value)) for idx in indices],
            new_value,
            replace_all,
        )
        if result is not None:
            return {
                "content": result,
                "strategy": strategy,
                "replacements": len(indices) if replace_all else 1,
            }
        if error == "ambiguous":
            return {"content": None, "strategy": strategy, "error": "ambiguous", "matches": len(indices)}

    line_strategies = [
        ("trim_trailing_whitespace", lambda line: line.rstrip()),
        ("normalize_whitespace", lambda line: re.sub(r"\s+", " ", line.strip())),
        ("ignore_blank_lines", lambda line: re.sub(r"\s+", " ", line.strip()) if line.strip() else ""),
    ]
    for strategy, key in line_strategies:
        ranges = _line_window_ranges(content, old, key)
        result, error = _replace_by_ranges(content, ranges, new, replace_all)
        if result is not None:
            return {
                "content": result,
                "strategy": strategy,
                "replacements": len(ranges) if replace_all else 1,
            }
        if error == "ambiguous":
            return {"content": None, "strategy": strategy, "error": "ambiguous", "matches": len(ranges)}

    old_dedented = textwrap_dedent(old)
    new_dedented = textwrap_dedent(new)
    if old_dedented != old:
        ranges = _line_window_ranges(content, old_dedented, lambda line: line.rstrip())
        if ranges:
            target_indent = _indent_of_first_line(content[ranges[0][0]:ranges[0][1]])
            adjusted = _reindent(new_dedented, _indent_of_first_line(new_dedented), target_indent)
            result, error = _replace_by_ranges(content, ranges, adjusted, replace_all)
            if result is not None:
                return {
                    "content": result,
                    "strategy": "indentation_adjusted",
                    "replacements": len(ranges) if replace_all else 1,
                }
            if error == "ambiguous":
                return {"content": None, "strategy": "indentation_adjusted", "error": "ambiguous", "matches": len(ranges)}

    # Last resort: high-similarity line block.
    content_lines = content.splitlines(True)
    old_lines = old.splitlines(True)
    width = len(old_lines)
    if width:
        offsets = _line_start_offsets(content)
        candidates: list[tuple[float, int, int]] = []
        old_joined = "".join(old_lines).strip()
        for idx in range(0, len(content_lines) - width + 1):
            block = "".join(content_lines[idx:idx + width]).strip()
            ratio = difflib.SequenceMatcher(None, old_joined, block).ratio()
            if ratio >= 0.86:
                start = offsets[idx]
                end = offsets[idx + width] if idx + width < len(offsets) else len(content)
                candidates.append((ratio, start, end))
        candidates.sort(reverse=True)
        if candidates and (replace_all or len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.04):
            ranges = [(start, end) for _, start, end in (candidates if replace_all else candidates[:1])]
            result, error = _replace_by_ranges(content, ranges, new, replace_all)
            if result is not None:
                return {
                    "content": result,
                    "strategy": "levenshtein_similarity",
                    "replacements": len(ranges),
                    "similarity": candidates[0][0],
                }
        elif len(candidates) > 1:
            return {"content": None, "strategy": "levenshtein_similarity", "error": "ambiguous", "matches": len(candidates)}

    return {"content": None, "strategy": None, "error": "not_found", "matches": 0}


def textwrap_dedent(value: str) -> str:
    lines = value.splitlines(True)
    indents = [
        len(re.match(r"[ \t]*", line).group(0))  # type: ignore[union-attr]
        for line in lines
        if line.strip()
    ]
    if not indents:
        return value
    amount = min(indents)
    if amount <= 0:
        return value
    return "".join(line[amount:] if len(line) >= amount else line for line in lines)


def _edit_sync(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    ctx: SkillContext | None = None,
) -> dict[str, Any]:
    path = Path(file_path).resolve()
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

    fingerprint = get_read_fingerprint(ctx, path)
    if fingerprint is None:
        return {
            "success": False,
            "error": (
                "Refusing to edit before read_file. Read the current file first "
                "so the edit is based on the latest content."
            ),
            "diff": None,
            "requires_read": True,
        }
    if not file_matches_fingerprint(path, fingerprint):
        return {
            "success": False,
            "error": "File changed after it was read. Read it again before editing.",
            "diff": None,
            "stale_read": True,
        }

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

    applied = _apply_edit(orig_norm, old_norm, new_norm, replace_all)
    result = applied.get("content")
    if result is None:
        # Provide a helpful error: tell the LLM what went wrong and
        # suggest reading the file first.
        sample = old_string[:120].replace("\n", "\\n")
        return {
            "success": False,
            "error": (
                f"old_string not found uniquely in {file_path}. "
                f"Matching strategy reached: {applied.get('strategy') or 'none'}; "
                f"reason: {applied.get('error') or 'not_found'}; "
                f"matches: {applied.get('matches', 0)}. "
                f"Read the file again and provide a more specific old_string. "
                f"(searched for: \"{sample}{'...' if len(old_string) > 120 else ''}\")"
            ),
            "diff": None,
            "match_strategy": applied.get("strategy"),
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
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=file_path, tofile=file_path, lineterm=""))
    diff = "\n".join(diff_lines)
    diff_preview = diff[:_MAX_OUTPUT_CHARS] + ("..." if len(diff) > _MAX_OUTPUT_CHARS else "")
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return {
        "success": True,
        "error": None,
        "diff": diff_preview,
        "path": str(path),
        "match_strategy": applied.get("strategy"),
        "replacements": applied.get("replacements", 1),
        "additions": additions,
        "deletions": deletions,
    }


@skill(
    name="edit_file",
    display_name="Edit File",
    description=(
        "Edit a file by replacing old_string with new_string. "
        "Read the file with read_file first; edits are rejected if the file "
        "changed after that read. Exact matching is preferred, but the tool "
        "can tolerate line ending, trailing whitespace, indentation, normalized "
        "whitespace, and high-similarity block differences when the match is unique. "
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
    returns="dict with keys: success (bool), error (str|null), diff (str), match_strategy (str), replacements (int), additions (int), deletions (int)",
    examples=[
        "edit_file('/workspace/main.py', 'def foo():', 'def foo():\\n    return 42')",
    ],
)
def edit_file(
    ctx: SkillContext,
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
        result = _edit_sync(file_path, old_string, new_string, replace_all, ctx=ctx)
    if result.get("success"):
        notify_asset_refresh(ctx, result.get("path") or file_path, reason="edit_file")
    return result
