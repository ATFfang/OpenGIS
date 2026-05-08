"""edit_file skill — edit file with string replacement and fuzzy matching (OpenCode-style).

Replaces oldString with newString in a file.
Tries multiple fuzzy matchers in order; stops at first unique match.
Supports replace_all.
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
# We use threading.Lock (not asyncio.Semaphore) because edit_file is
# invoked synchronously from the agent's tool-bridge worker thread.
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


def _leven_ratio(a: str, b: str) -> float:
    """Return similarity ratio using difflib (0.0 ~ 1.0)."""
    return difflib.SequenceMatcher(None, a, b).ratio()


# ── Matchers (tried in order) ──────────────────────────────────────────

def _match_exact(content: str, old: str) -> list[int]:
    idx = content.find(old)
    return [idx] if idx != -1 else []


def _match_line_trimmed(content: str, old: str) -> list[int]:
    lines_c = [ln.rstrip() for ln in content.splitlines(True)]
    lines_o = [ln.rstrip() for ln in old.splitlines(True)]
    joined_c = "".join(lines_c)
    joined_o = "".join(lines_o)
    idx = joined_c.find(joined_o)
    return [idx] if idx != -1 else []


def _match_block_anchor(content: str, old: str, threshold: float = 0.6) -> list[int]:
    """Use first/last line as anchors; fuzzy-match middle lines."""
    c_lines = content.splitlines(True)
    o_lines = old.splitlines(True)
    if len(o_lines) < 2:
        return []
    first = o_lines[0].strip()
    last = o_lines[-1].strip()
    # Find anchor candidates
    starts = [i for i, ln in enumerate(c_lines) if ln.strip() == first]
    ends = [i for i, ln in enumerate(c_lines) if ln.strip() == last]
    candidates = []
    for s in starts:
        for e in ends:
            if e > s:
                candidates.append((s, e))
    if not candidates:
        return []
    # Score each candidate
    scored = []
    o_middle = [ln.strip() for ln in o_lines[1:-1]]
    for s, e in candidates:
        c_middle = [ln.strip() for ln in c_lines[s + 1 : e]]
        if not o_middle:
            scored.append((1.0, s))
            continue
        ratio = _leven_ratio("\n".join(o_middle), "\n".join(c_middle))
        if ratio >= threshold:
            scored.append((ratio, s))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    return [scored[0][1]]


def _match_whitespace_normalized(content: str, old: str) -> list[int]:
    a = " ".join(content.split())
    b = " ".join(old.split())
    idx = a.find(b)
    return [idx] if idx != -1 else []


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


# ── Core edit logic ───────────────────────────────────────────────────

def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> str | None:
    """
    Try matchers in order; return new content or None if no unique match.
    """
    matchers = [
        _match_exact,
        _match_line_trimmed,
        _match_whitespace_normalized,
        lambda c, o: _match_block_anchor(c, o, 0.6),
        lambda c, o: _match_block_anchor(c, o, 0.4),
    ]
    for matcher in matchers:
        indices = matcher(content, old)
        if not indices:
            continue
        if not replace_all and len(indices) > 1:
            continue  # ambiguous — try next matcher
        # Apply
        if replace_all:
            for idx in reversed(indices):
                end = idx + len(old)
                content = content[:idx] + new + content[end:]
            return content
        else:
            idx = indices[0]
            return content[:idx] + new + content[idx + len(old) :]

    # Fallback: try difflib get_close_matches on lines
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
        return {
            "success": False,
            "error": (
                "Could not find a unique match for old_string. "
                "Try making the match text more specific, "
                "or include the first and last line of the block."
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
    import difflib as _difflib

    old_lines = original.splitlines(True)
    new_lines = result.splitlines(True)
    diff = "\n".join(
        _difflib.unified_diff(
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
        "Uses fuzzy matching (exact → line-trimmed → whitespace-normalized "
        "→ block-anchor with Levenshtein). "
        "Make sure old_string is a unique, specific match. "
        "Prefer editing existing files; use write_file for new files."
    ),
    category="system",
    needs_context=True,  # For Gap #2: track file edits
    params=[
        {"name": "file_path", "type": "string",
         "description": "Absolute path to the file to edit."},
        {"name": "old_string", "type": "string",
         "description": "The exact or fuzzy text to replace."},
        {"name": "new_string", "type": "string",
         "description": "The text to replace it with."},
        {"name": "replace_all", "type": "boolean",
         "description": "Replace all occurrences (default False)."},
    ],
    returns="dict with keys: success (bool), error (str|null), diff (str)",
    examples=[
        "edit_file('/workspace/main.py', 'def foo():', 'def foo():\n    return 42')",
    ],
)
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Edit file with fuzzy matching.

    NOTE: Synchronous on purpose — invoked from the agent's tool-bridge
    worker thread with no event loop. See docs/ARCHITECTURE.md
    §Skill Invocation for why we must not be ``async def`` here.
    """
    # Per-file lock to serialize concurrent edits to the same path.
    lock_key = str(Path(file_path).resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.setdefault(lock_key, threading.Lock())
    with lock:
        return _edit_sync(file_path, old_string, new_string, replace_all)
