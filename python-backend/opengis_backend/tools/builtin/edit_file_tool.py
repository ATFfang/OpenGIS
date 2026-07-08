"""edit_file tool — robust string replacement for agent edits.

Replaces one or more old_string/new_string blocks in a file.
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

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool
from opengis_backend.tools.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.tools.builtin._file_state import get_read_fingerprint, file_matches_fingerprint

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


def _coerce_edits(
    old_string: str | None,
    new_string: str | None,
    replace_all: bool,
    edits: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if edits is not None:
        if not isinstance(edits, list) or not edits:
            return None, "edits must be a non-empty array when provided"
        result: list[dict[str, Any]] = []
        for idx, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                return None, f"edits[{idx}] must be an object"
            old = edit.get("old_string", edit.get("old"))
            new = edit.get("new_string", edit.get("new"))
            if not isinstance(old, str) or not isinstance(new, str):
                return None, f"edits[{idx}] must include string old_string and new_string"
            item_replace_all = edit.get("replace_all", replace_all)
            result.append({
                "old_string": old,
                "new_string": new,
                "replace_all": bool(item_replace_all),
            })
        return result, None

    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return None, "Provide old_string/new_string, or provide edits=[{old_string, new_string, replace_all?}, ...]"
    return [{
        "old_string": old_string,
        "new_string": new_string,
        "replace_all": bool(replace_all),
    }], None


def _edit_sync(
    file_path: str,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    edits: list[dict[str, Any]] | None = None,
    ctx: ToolContext | None = None,
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

    edit_items, edit_error = _coerce_edits(old_string, new_string, replace_all, edits)
    if edit_error or edit_items is None:
        return {
            "success": False,
            "error": edit_error,
            "diff": None,
        }

    for idx, edit in enumerate(edit_items, start=1):
        if edit["old_string"] == edit["new_string"]:
            return {
                "success": False,
                "error": f"edits[{idx}] old_string and new_string must be different",
                "diff": None,
                "edit_index": idx,
            }

    if len(edit_items) > 20:
        return {
            "success": False,
            "error": "Too many edits for one edit_file call; limit is 20 edits.",
            "diff": None,
        }

    # Normalize line endings for matching
    orig_norm = _normalize_line_endings(original)
    ending = _detect_line_ending(original)

    result = orig_norm
    applied_edits: list[dict[str, Any]] = []
    total_replacements = 0
    for idx, edit in enumerate(edit_items, start=1):
        old_value = edit["old_string"]
        new_value = edit["new_string"]
        old_norm = _normalize_line_endings(old_value)
        new_norm = _normalize_line_endings(new_value)
        applied = _apply_edit(result, old_norm, new_norm, bool(edit["replace_all"]))
        next_result = applied.get("content")
        if next_result is None:
            # Provide a helpful error without writing a partially-edited file.
            sample = old_value[:120].replace("\n", "\\n")
            return {
                "success": False,
                "error": (
                    f"edits[{idx}] old_string not found uniquely in {file_path}. "
                    f"Matching strategy reached: {applied.get('strategy') or 'none'}; "
                    f"reason: {applied.get('error') or 'not_found'}; "
                    f"matches: {applied.get('matches', 0)}. "
                    f"No changes were written. Read the file again and provide a more specific old_string. "
                    f"(searched for: \"{sample}{'...' if len(old_value) > 120 else ''}\")"
                ),
                "diff": None,
                "match_strategy": applied.get("strategy"),
                "edit_index": idx,
            }
        result = next_result
        replacements = int(applied.get("replacements") or 0)
        total_replacements += replacements
        applied_edits.append({
            "index": idx,
            "match_strategy": applied.get("strategy"),
            "replacements": replacements,
        })

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
        "match_strategy": applied_edits[0].get("match_strategy") if len(applied_edits) == 1 else "multiple",
        "edit_count": len(applied_edits),
        "edits": applied_edits,
        "replacements": total_replacements,
        "additions": additions,
        "deletions": deletions,
    }


@tool(
    name="edit_file",
    display_name="Edit File",
    description=(
        "Edit a file by replacing old_string with new_string, or apply several "
        "independent edits to the same file in one atomic call with edits=[...]. "
        "Read the file with read_file first; edits are rejected if the file "
        "changed after that read. Exact matching is preferred, but the tool "
        "can tolerate line ending, trailing whitespace, indentation, normalized "
        "whitespace, and high-similarity block differences when the match is unique. "
        "When changing multiple places in the same file, prefer one edits array "
        "over several edit_file calls. If any edit fails, no changes are written. "
        "Use write_file for new files; edit_file for modifying existing ones."
    ),
    category="system",
    needs_context=True,
    params=[
        {"name": "file_path", "type": "string",
         "description": "Absolute path to the file to edit."},
        {"name": "old_string", "type": "string", "required": False,
         "description": "Single-edit mode: exact text to replace (copy from read_file output). Omit when using edits."},
        {"name": "new_string", "type": "string", "required": False,
         "description": "Single-edit mode: replacement text. Omit when using edits."},
        {"name": "replace_all", "type": "boolean",
         "description": "Single-edit default, or default for items in edits without replace_all (default false)."},
        {"name": "edits", "type": "array", "required": False,
         "description": "Batch mode: array of {old_string, new_string, replace_all?}. Applies sequentially and atomically to one file."},
    ],
    returns="dict with keys: success (bool), error (str|null), diff (str), match_strategy (str), edit_count (int), edits (array), replacements (int), additions (int), deletions (int)",
    examples=[
        "edit_file('/workspace/main.py', 'def foo():', 'def foo():\\n    return 42')",
        "edit_file('/workspace/main.py', edits=[{'old_string': 'a = 1', 'new_string': 'a = 2'}, {'old_string': 'b = 1', 'new_string': 'b = 2'}])",
    ],
)
def edit_file(
    ctx: ToolContext,
    file_path: str,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    edits: list[dict[str, Any]] | None = None,
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
        result = _edit_sync(file_path, old_string, new_string, replace_all, edits=edits, ctx=ctx)
    if result.get("success"):
        notify_asset_refresh(ctx, result.get("path") or file_path, reason="edit_file")
    return result
