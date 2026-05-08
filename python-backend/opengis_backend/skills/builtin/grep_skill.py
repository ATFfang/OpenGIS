"""grep skill — regex search over file contents (OpenCode-style).

Uses rg (ripgrep) if available, falls back to pure Python re.search.
Results are sorted by mtime (newest first) and truncated to 100 matches.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

_MAX_RESULTS = 100
_RG_TIMEOUT = 15  # seconds


def _has_ripgrep() -> bool:
    try:
        subprocess.run(
            ["rg", "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:
        return False


def _grep_ripgrep(
    pattern: str,
    search_path: str,
    include: str | None = None,
) -> dict[str, Any]:
    """Use rg (ripgrep) for fast regex search."""
    args = [
        "rg",
        "--json",
        "--no-config",
        "--hidden",
        "--glob=!.git/*",
        "--no-messages",
    ]
    if include:
        args.append(f"--glob={include}")
    args.append(pattern)
    args.append(search_path)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_RG_TIMEOUT,
        )
        # rg returns 1 when no matches (not an error)
        lines = result.stdout.strip().splitlines()
        items: list[dict] = []
        for line in lines[: _MAX_RESULTS + 1]:
            try:
                import json

                obj = json.loads(line)
                if obj.get("type") != "match":
                    continue
                data = obj["data"]
                p = data["path"]["text"]
                for sub in data.get("submatches", []):
                    items.append(
                        {
                            "file": p,
                            "line": sub["line_number"],
                            "text": sub["matched_text"],
                        }
                    )
            except Exception:
                continue

        truncated = len(items) > _MAX_RESULTS
        items = items[:_MAX_RESULTS]

        # Sort by file mtime descending
        def _mtime(item):
            try:
                return os.path.getmtime(item["file"])
            except Exception:
                return 0

        items.sort(key=_mtime, reverse=True)

        output_lines = []
        seen_files: set[str] = set()
        for item in items:
            if item["file"] not in seen_files:
                output_lines.append(f"{item['file']}:")
                seen_files.add(item["file"])
            output_lines.append(f"  Line {item['line']}: {item['text']}")

        output = (
            f"Found {len(items)} matches"
            f"{' (showing first 100)' if truncated else ''}\n"
        ) + "\n".join(output_lines)

        return {
            "output": output,
            "matches": len(items),
            "truncated": truncated,
            "error": None,
        }

    except Exception as e:
        logger.warning("[grep] rg failed, falling back to pure Python: %s", e)
        return _grep_pure(pattern, search_path, include)


def _grep_pure(
    pattern: str,
    search_path: str,
    include: str | None = None,
) -> dict[str, Any]:
    """Pure-Python regex search (rg not available)."""
    try:
        reg = re.compile(pattern)
    except re.error as e:
        return {
            "output": f"Invalid regex: {e}",
            "matches": 0,
            "truncated": False,
            "error": str(e),
        }

    path = Path(search_path)
    if not path.exists():
        return {
            "output": f"Path not found: {search_path}",
            "matches": 0,
            "truncated": False,
            "error": "path_not_found",
        }

    if path.is_file():
        files = [path]
    else:
        files = list(path.rglob("*"))
        if include:
            # Simple glob filter
            import fnmatch

            files = [f for f in files if fnmatch.fnmatch(f.name, include)]

    items: list[dict] = []
    for fp in files:
        if not fp.is_file():
            continue
        if fp.name.startswith("."):
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, start=1):
                    if reg.search(line):
                        items.append(
                            {
                                "file": str(fp),
                                "line": i,
                                "text": line.strip(),
                            }
                        )
                        if len(items) >= _MAX_RESULTS:
                            break
        except Exception:
            continue
        if len(items) >= _MAX_RESULTS:
            break

    truncated = len(items) >= _MAX_RESULTS
    items = items[:_MAX_RESULTS]

    def _mtime(item):
        try:
            return os.path.getmtime(item["file"])
        except Exception:
            return 0

    items.sort(key=_mtime, reverse=True)

    output_lines = []
    seen_files: set[str] = set()
    for item in items:
        if item["file"] not in seen_files:
            output_lines.append(f"{item['file']}:")
            seen_files.add(item["file"])
        output_lines.append(f"  Line {item['line']}: {item['text']}")

    output = (
        f"Found {len(items)} matches"
        f"{' (showing first 100)' if truncated else ''}\n"
    ) + "\n".join(output_lines)

    return {
        "output": output,
        "matches": len(items),
        "truncated": truncated,
        "error": None,
    }


def _grep_sync(
    pattern: str,
    path: str | None = None,
    include: str | None = None,
) -> dict[str, Any]:
    """Synchronous grep (runs in executor)."""
    search_path = path or os.getcwd()
    if _has_ripgrep():
        return _grep_ripgrep(pattern, search_path, include)
    return _grep_pure(pattern, search_path, include)


@skill(
    name="grep",
    display_name="Regex Search",
    description=(
        "Search file contents using a regex pattern. "
        "Uses ripgrep if available (much faster), "
        "otherwise falls back to pure Python. "
        "Results are sorted by file modification time (newest first)."
    ),
    category="system",
    params=[
        {"name": "pattern", "type": "string", "description": "The regex pattern to search for."},
        {"name": "path", "type": "string", "description": "Directory to search in (default current dir)."},
        {"name": "include", "type": "string", "description": "File pattern to include (e.g. '*.py', '*.{ts,tsx}')."},
    ],
    returns="dict with keys: output (str), matches (int), truncated (bool), error (str|null)",
    examples=[
        "grep('TODO')",
        "grep('def .*agent', path='/workspace/src', include='*.py')",
    ],
)
def grep(
    pattern: str,
    path: str | None = None,
    include: str | None = None,
) -> dict[str, Any]:
    """Regex search.

    NOTE: Synchronous — invoked from the agent's worker thread.
    """
    return _grep_sync(pattern, path, include)
