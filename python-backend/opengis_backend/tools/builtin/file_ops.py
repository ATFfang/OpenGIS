"""file_ops tools — common filesystem operations.

Provides create_directory, delete_file, move_file, copy_file,
file_exists, and list_directory as individual tools, so the agent
doesn't need to write Python code or bash for basic file operations.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool
from opengis_backend.tools.builtin._asset_refresh import notify_asset_refresh

logger = logging.getLogger(__name__)


def _workspace(ctx: ToolContext | None) -> Path | None:
    raw = (getattr(ctx, "meta", None) or {}).get("workspace_path") if ctx else None
    if not raw:
        return None
    return Path(str(raw)).expanduser().resolve()


def _resolve_for_workspace(ctx: ToolContext | None, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    workspace = _workspace(ctx)
    if workspace:
        return (workspace / path).resolve()
    return path.resolve()

# ─── create_directory ───────────────────────────────────────────

@tool(
    name="create_directory",
    display_name="Create Directory",
    description="Create a directory (and any missing parent directories). Equivalent to 'mkdir -p'.",
    category="system",
    params=[
        {"name": "path", "type": "string", "description": "Absolute path of the directory to create."},
    ],
    returns="dict with keys: success (bool), path (str), created (bool), error (str|null)",
    needs_context=True,
)
def create_directory(ctx: ToolContext, path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        existed = p.exists()
        p.mkdir(parents=True, exist_ok=True)
        notify_asset_refresh(ctx, p, reason="create_directory")
        return {"success": True, "path": str(p), "created": not existed, "error": None}
    except Exception as e:
        return {"success": False, "path": str(p), "created": False, "error": str(e)}


# ─── delete_file ────────────────────────────────────────────────

@tool(
    name="delete_file",
    display_name="Delete File or Directory",
    description="Delete a file or directory. Use with caution — this cannot be undone.",
    category="system",
    params=[
        {"name": "path", "type": "string", "description": "Absolute path of the file or directory to delete."},
        {"name": "recursive", "type": "boolean", "description": "If True and path is a directory, delete recursively. Default False."},
    ],
    returns="dict with keys: success (bool), path (str), error (str|null)",
    needs_context=True,
)
def delete_file(ctx: ToolContext, path: str, recursive: bool = False) -> dict[str, Any]:
    p = Path(path)
    try:
        if not p.exists():
            return {"success": True, "path": str(p), "error": None}  # already gone
        if p.is_dir():
            if recursive:
                shutil.rmtree(p)
            else:
                p.rmdir()  # only works if empty
        else:
            p.unlink()
        notify_asset_refresh(ctx, p.parent, reason="delete_file")
        return {"success": True, "path": str(p), "error": None}
    except Exception as e:
        return {"success": False, "path": str(p), "error": str(e)}


# ─── move_file ──────────────────────────────────────────────────

@tool(
    name="move_file",
    display_name="Move / Rename File",
    description="Move or rename a file or directory. Creates parent directories of the destination if needed.",
    category="system",
    params=[
        {"name": "src", "type": "string", "description": "Absolute path of the source file or directory."},
        {"name": "dst", "type": "string", "description": "Absolute path of the destination."},
    ],
    returns="dict with keys: success (bool), src (str), dst (str), error (str|null)",
    needs_context=True,
)
def move_file(ctx: ToolContext, src: str, dst: str) -> dict[str, Any]:
    try:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        notify_asset_refresh(ctx, Path(src).parent, reason="move_file")
        notify_asset_refresh(ctx, Path(dst), reason="move_file")
        return {"success": True, "src": src, "dst": dst, "error": None}
    except Exception as e:
        return {"success": False, "src": src, "dst": dst, "error": str(e)}


# ─── copy_file ──────────────────────────────────────────────────

@tool(
    name="copy_file",
    display_name="Copy File or Directory",
    description="Copy a file or directory. Creates parent directories of the destination if needed.",
    category="system",
    params=[
        {"name": "src", "type": "string", "description": "Absolute path of the source file or directory."},
        {"name": "dst", "type": "string", "description": "Absolute path of the destination."},
    ],
    returns="dict with keys: success (bool), src (str), dst (str), error (str|null)",
    needs_context=True,
)
def copy_file(ctx: ToolContext, src: str, dst: str) -> dict[str, Any]:
    try:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        s = Path(src)
        if s.is_dir():
            shutil.copytree(s, dst)
        else:
            shutil.copy2(s, dst)
        notify_asset_refresh(ctx, Path(dst), reason="copy_file")
        return {"success": True, "src": src, "dst": dst, "error": None}
    except Exception as e:
        return {"success": False, "src": src, "dst": dst, "error": str(e)}


# ─── file_exists ────────────────────────────────────────────────

@tool(
    name="file_exists",
    display_name="Check File Exists",
    description="Check if a file or directory exists and return its metadata.",
    category="system",
    params=[
        {"name": "path", "type": "string", "description": "Absolute path to check."},
    ],
    returns="dict with keys: exists (bool), is_file (bool), is_dir (bool), size (int|null), error (str|null)",
    needs_context=True,
)
def file_exists(ctx: ToolContext, path: str) -> dict[str, Any]:
    p = _resolve_for_workspace(ctx, path)
    try:
        if not p.exists():
            return {
                "exists": False,
                "path": str(p),
                "is_file": False,
                "is_dir": False,
                "size": None,
                "error": None,
            }
        stat = p.stat()
        return {
            "exists": True,
            "path": str(p),
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
            "size": stat.st_size if p.is_file() else None,
            "error": None,
        }
    except Exception as e:
        return {"exists": False, "path": str(p), "is_file": False, "is_dir": False, "size": None, "error": str(e)}


# ─── list_directory ─────────────────────────────────────────────

@tool(
    name="list_directory",
    display_name="List Directory",
    description="List files and subdirectories in a directory. Returns names, types, and sizes.",
    category="system",
    params=[
        {"name": "path", "type": "string", "description": "Absolute path of the directory to list."},
        {"name": "pattern", "type": "string", "description": "Optional glob pattern to filter (e.g. '*.geojson'). Default: list all."},
    ],
    returns="dict with keys: entries (list of {name, is_file, is_dir, size}), count (int), error (str|null)",
    needs_context=True,
)
def list_directory(ctx: ToolContext, path: str, pattern: str | None = None) -> dict[str, Any]:
    p = _resolve_for_workspace(ctx, path)
    try:
        if not p.is_dir():
            return {"entries": [], "count": 0, "path": str(p), "error": f"Not a directory: {p}"}
        items = p.glob(pattern) if pattern else p.iterdir()
        entries = []
        for item in sorted(items, key=lambda x: x.name):
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "is_file": item.is_file(),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                })
            except Exception:
                entries.append({"name": item.name, "is_file": False, "is_dir": False, "size": None})
        return {"entries": entries, "count": len(entries), "path": str(p), "error": None}
    except Exception as e:
        return {"entries": [], "count": 0, "path": str(p), "error": str(e)}
