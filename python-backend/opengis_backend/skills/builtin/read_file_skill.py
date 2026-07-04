"""read_file skill — read file contents with offset/limit (OpenCode-style).

Reads a file with line-level pagination, binary detection,
and optional truncation for large files.
"""

from __future__ import annotations

import base64
import difflib
import logging
import os
from pathlib import Path
from typing import Any

from opengis_backend.skills.context import SkillContext
from opengis_backend.skills.registry import skill
from opengis_backend.skills.builtin._file_state import mark_file_read

logger = logging.getLogger(__name__)

_MAX_LINE_LEN = 2000
_MAX_BYTES = 50 * 1024   # 50 KB per read
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
_IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_BINARY_EXTS = {
    ".zip", ".exe", ".dll", ".so", ".dylib", ".bin", ".class",
    ".jar", ".war", ".ear", ".pyc", ".pyo", ".o", ".a",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv",
    ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
}


def _is_binary_content(sample: bytes) -> bool:
    """Heuristic: >30% non-printable bytes → binary."""
    if not sample:
        return False
    non_printable = sum(1 for b in sample if b < 0x20 and b not in (b"\n", b"\r", b"\t", b"\f", b"\v"))
    return non_printable / len(sample) > 0.3


def _suggest_paths(path: Path) -> list[str]:
    parent = path.parent if str(path.parent) else Path(".")
    try:
        candidates = [p.name for p in parent.iterdir()]
    except Exception:
        return []
    names = difflib.get_close_matches(path.name, candidates, n=5, cutoff=0.45)
    return [str((parent / name).resolve()) for name in names]


def _attachment_response(path: Path, mime: str, kind: str) -> dict[str, Any]:
    size = path.stat().st_size
    if size > _MAX_ATTACHMENT_BYTES:
        return {
            "output": (
                f"<path>{path}</path>\n<type>{kind}</type>\n"
                f"<note>File is {size} bytes, above attachment limit "
                f"{_MAX_ATTACHMENT_BYTES} bytes. Use a smaller file or extract text first.</note>"
            ),
            "error": "attachment_too_large",
            "truncated": False,
            "type": kind,
            "path": str(path),
            "mime": mime,
            "size": size,
        }
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "output": f"<path>{path}</path>\n<type>{kind}</type>\n<mime>{mime}</mime>\n<attachment>base64</attachment>",
        "error": None,
        "truncated": False,
        "type": kind,
        "path": str(path),
        "mime": mime,
        "encoding": "base64",
        "content": data,
        "size": size,
        "file": {
            "name": path.name,
            "mime": mime,
            "encoding": "base64",
            "data": data,
        },
    }


def _read_lines_sync(
    file_path: str,
    offset: int = 1,
    limit: int = 2000,
    ctx: SkillContext | None = None,
) -> dict[str, Any]:
    """Synchronous file reader (runs in executor)."""
    path = Path(file_path)

    if not path.exists():
        suggestions = _suggest_paths(path)
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\nDid you mean:\n" + "\n".join(f"- {item}" for item in suggestions)
        return {
            "output": f"File not found: {file_path}{suggestion_text}",
            "error": "file_not_found",
            "truncated": False,
            "suggestions": suggestions,
        }

    if not path.is_file():
        # Directory listing
        try:
            entries = sorted(p.name for p in path.iterdir())
        except PermissionError as e:
            return {"output": f"Permission denied: {e}", "error": "permission_denied", "truncated": False}
        listing = "\n".join(entries[:100])
        if len(entries) > 100:
            listing += f"\n... ({len(entries)} total entries, showing first 100)"
        return {
            "output": f"<path>{file_path}</path>\n<type>directory</type>\n<entries>\n{listing}\n</entries>",
            "error": None,
            "truncated": len(entries) > 100,
        }

    # Binary detection
    ext = path.suffix.lower()
    if ext in _IMAGE_MIME_BY_EXT:
        mark_file_read(ctx, path)
        return _attachment_response(path, _IMAGE_MIME_BY_EXT[ext], "image")
    if ext == ".pdf":
        mark_file_read(ctx, path)
        return _attachment_response(path, "application/pdf", "pdf")

    is_bin = ext in _BINARY_EXTS
    if not is_bin:
        try:
            with open(path, "rb") as f:
                sample = f.read(4096)
            is_bin = _is_binary_content(sample)
        except Exception:
            pass

    if is_bin:
        return {
            "output": f"<path>{file_path}</path>\n<type>binary</type>\n<note>Binary file ({ext}), not shown.</note>",
            "error": None,
            "truncated": False,
        }

    # Text read with offset/limit
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = []
            byte_count = 0
            truncated = False
            skip = max(0, offset - 1)

            for i, line in enumerate(f, start=1):
                if i <= skip:
                    continue
                if limit and len(lines) >= limit:
                    truncated = True
                    break
                # Truncate very long lines
                if len(line) > _MAX_LINE_LEN:
                    line = line[:_MAX_LINE_LEN] + "...(line truncated)\n"
                lines.append(line)
                byte_count += len(line.encode("utf-8", errors="replace"))
                if byte_count > _MAX_BYTES:
                    truncated = True
                    break

        content = "".join(lines)
        with open(path, encoding="utf-8", errors="replace") as count_f:
            total_lines = sum(1 for _ in count_f)
        preview = content[:200] + ("..." if len(content) > 200 else "")
        mark_file_read(ctx, path)

        output = f"<path>{file_path}</path>\n<type>file</type>\n<content>\n{content}</content>"
        return {
            "output": output,
            "error": None,
            "truncated": truncated,
            "preview": preview,
            "total_lines": total_lines,
        }

    except Exception as e:
        logger.error("[read_file] failed: %s", e)
        return {"output": f"Error reading file: {e}", "error": str(e), "truncated": False}


@skill(
    name="read_file",
    display_name="Read File",
    description=(
        "Read the contents of a file. "
        "Supports line-level pagination via offset/limit. "
        "Automatically detects binary files. "
        "If given a directory path, lists its contents (up to 100 entries). "
        "Prefer reading larger files in chunks rather than all at once."
    ),
    category="system",
    needs_context=True,
    params=[
        {"name": "file_path", "type": "string", "description": "Absolute path to the file to read."},
        {"name": "offset", "type": "number", "description": "1-indexed line number to start from (default 1)."},
        {"name": "limit", "type": "number", "description": "Maximum number of lines to read (default 2000)."},
    ],
    returns="dict with keys: output (str), error (str|null), truncated (bool), total_lines (int)",
    examples=[
        "read_file('/workspace/main.py')",
        "read_file('/workspace/data.csv', offset=1, limit=100)",
    ],
)
def read_file(
    ctx: SkillContext,
    file_path: str,
    offset: int = 1,
    limit: int = 2000,
) -> dict[str, Any]:
    """Read file contents.

    NOTE: This skill is invoked from the agent's tool-bridge worker thread
    (no event loop bound), so it MUST stay synchronous. Do NOT wrap with
    asyncio.run_in_executor — see docs/ARCHITECTURE.md §Skill Invocation.
    """
    return _read_lines_sync(file_path, offset, limit, ctx=ctx)
