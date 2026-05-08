"""glob skill — find files by glob pattern (OpenCode-style).

Uses rg --files if available, falls back to pathlib.glob.
Results sorted by mtime (newest first), truncated to 100.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

_MAX_RESULTS = 100
_RG_TIMEOUT = 15  # seconds


def _has_ripgrep() -> bool:
    try:
        import subprocess
        subprocess.run(["rg", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _glob_ripgrep(pattern: str, search_path: str) -> dict[str, Any]:
    """Use rg --files with --glob for fast file finding."""
    import json as _json
    import subprocess as _sp

    args = [
        "rg",
        "--files",
        "--no-config",
        f"--glob={pattern}",
        "--glob=!.git/*",
        search_path,
    ]
    try:
        result = _sp.run(args, capture_output=True, text=True, timeout=_RG_TIMEOUT)
        raw = result.stdout.strip().splitlines()
        files = []
        for line in raw[: _MAX_RESULTS + 1]:
            p = line.strip()
            if not p:
                continue
            try:
                mtime = os.path.getmtime(p)
            except Exception:
                mtime = 0
            files.append({"path": p, "mtime": mtime})

        truncated = len(files) > _MAX_RESULTS
        files = sorted(files, key=lambda x: x["mtime"], reverse=True)[:_MAX_RESULTS]

        output = "\n".join(f['path'] for f in files)
        if truncated:
            output += f"\n(Results are truncated: showing first {_MAX_RESULTS} results...)"

        return {
            "output": output,
            "count": len(files),
            "truncated": truncated,
            "error": None,
        }
    except Exception as e:
        logger.warning("[glob] rg failed, falling back to pure Python: %s", e)
        return _glob_pure(pattern, search_path)


def _glob_pure(pattern: str, search_path: str) -> dict[str, Any]:
    """Pure-Python glob using pathlib."""
    path = Path(search_path)
    if not path.exists():
        return {
            "output": f"Path not found: {search_path}",
            "count": 0,
            "truncated": False,
            "error": "path_not_found",
        }

    try:
        files = list(path.glob(f"**/{pattern}"))
    except Exception as e:
        return {
            "output": f"Invalid glob pattern: {e}",
            "count": 0,
            "truncated": False,
            "error": str(e),
        }

    items = []
    for f in files:
        if not f.is_file():
            continue
        if f.name.startswith("."):
            continue
        try:
            mtime = f.stat().st_mtime
        except Exception:
            mtime = 0
        items.append({"path": str(f), "mtime": mtime})
        if len(items) > _MAX_RESULTS:
            break

    truncated = len(items) > _MAX_RESULTS
    items = sorted(items, key=lambda x: x["mtime"], reverse=True)[:_MAX_RESULTS]

    output = "\n".join(i["path"] for i in items)
    if truncated:
        output += f"\n(Results are truncated: showing first {_MAX_RESULTS} results...)"

    return {
        "output": output,
        "count": len(items),
        "truncated": truncated,
        "error": None,
    }


def _glob_sync(
    pattern: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Synchronous glob (runs in executor)."""
    search_path = path or os.getcwd()
    if _has_ripgrep():
        return _glob_ripgrep(pattern, search_path)
    return _glob_pure(pattern, search_path)


@skill(
    name="glob",
    display_name="Glob Files",
    description=(
        "Find files by glob pattern (e.g. '**/*.py', 'src/**/*.ts'). "
        "Uses ripgrep if available (much faster), "
        "otherwise falls back to pure Python. "
        "Results sorted by modification time (newest first)."
    ),
    category="system",
    params=[
        {"name": "pattern", "type": "string", "description": "The glob pattern to match files against."},
        {"name": "path", "type": "string", "description": "Directory to search in (default current dir)."},
    ],
    returns="dict with keys: output (str), count (int), truncated (bool), error (str|null)",
    examples=[
        "glob('**/*.py')",
        "glob('*.ts', path='/workspace/src')",
    ],
)
def glob(
    pattern: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Glob files.

    NOTE: Synchronous — invoked from the agent's worker thread.
    """
    return _glob_sync(pattern, path)
