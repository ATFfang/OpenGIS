"""Script asset tools for discovering and reading reusable agent code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool
from opengis_backend.tools.builtin._file_state import mark_file_read


def _workspace(ctx: ToolContext) -> Path:
    workspace = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    if not workspace:
        raise RuntimeError("A workspace is required to inspect reusable scripts.")
    path = Path(str(workspace)).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"workspace_path does not exist: {workspace}")
    return path


def _resolve_script_path(ctx: ToolContext, script_path: str) -> Path:
    workspace = _workspace(ctx)
    raw = Path(script_path).expanduser()
    path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    script_root = (workspace / "script").resolve()
    if path.suffix.lower() != ".py":
        raise ValueError(f"script_path must point to a .py file: {script_path}")
    if script_root != path and script_root not in path.parents:
        raise ValueError(f"script_path must be under the workspace script directory: {script_path}")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"script not found: {script_path}")
    return path


def _rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace))
    except Exception:
        return str(path.resolve())


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _script_record(workspace: Path, path: Path) -> dict[str, Any]:
    meta = _load_json(path.with_suffix(".metadata.json"))
    stat = path.stat()
    return {
        "id": _rel(workspace, path),
        "path": _rel(workspace, path),
        "abs_path": str(path.resolve()),
        "name": path.name,
        "semantic_name": meta.get("semantic_name") or path.stem,
        "description": meta.get("description") or "",
        "run_id": meta.get("run_id"),
        "step": meta.get("step"),
        "timestamp": meta.get("timestamp"),
        "loop_kind": meta.get("loop_kind"),
        "workflow": bool(meta.get("workflow")),
        "has_error": bool(meta.get("has_error")),
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
    }


@tool(
    name="list_scripts",
    display_name="List Reusable Scripts",
    description=(
        "List persisted agent Python scripts under the workspace script/ directory. "
        "Use this before writing new code when a task may reuse or patch prior code."
    ),
    category="system",
    params=[
        {"name": "query", "type": "string", "description": "Optional fuzzy text filter over name/path/description.", "required": False},
        {"name": "kind", "type": "string", "description": "Optional filter: chat, workflow, error, ok.", "required": False},
        {"name": "limit", "type": "number", "description": "Maximum scripts to return. Default 20.", "required": False},
    ],
    returns="dict with success, scripts list, and script_root.",
    needs_context=True,
)
def list_scripts(
    ctx: ToolContext,
    query: str = "",
    kind: str = "",
    limit: int | float = 20,
) -> dict[str, Any]:
    workspace = _workspace(ctx)
    script_root = workspace / "script"
    if not script_root.exists():
        return {"success": True, "scripts": [], "script_root": str(script_root)}

    max_items = max(1, min(100, int(limit or 20)))
    needle = str(query or "").strip().lower()
    kind_filter = str(kind or "").strip().lower()
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in sorted(script_root.rglob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True):
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        record = _script_record(workspace, path)
        haystack = " ".join(
            str(record.get(key) or "")
            for key in ("name", "path", "semantic_name", "description", "run_id")
        ).lower()
        if needle and needle not in haystack:
            continue
        if kind_filter == "workflow" and not record["workflow"]:
            continue
        if kind_filter == "chat" and record["workflow"]:
            continue
        if kind_filter == "error" and not record["has_error"]:
            continue
        if kind_filter == "ok" and record["has_error"]:
            continue
        records.append(record)
        if len(records) >= max_items:
            break

    return {"success": True, "scripts": records, "script_root": str(script_root)}


@tool(
    name="read_script",
    display_name="Read Reusable Script",
    description=(
        "Read a persisted agent script by path/id and mark it as read for safe edit_file patching. "
        "Use before editing a reusable script."
    ),
    category="system",
    params=[
        {"name": "script_path", "type": "string", "description": "Script path or id from list_scripts."},
        {"name": "max_chars", "type": "number", "description": "Maximum content characters. Default 20000.", "required": False},
    ],
    returns="dict with success, path, abs_path, metadata, content, truncated.",
    needs_context=True,
)
def read_script(
    ctx: ToolContext,
    script_path: str,
    max_chars: int | float = 20000,
) -> dict[str, Any]:
    workspace = _workspace(ctx)
    path = _resolve_script_path(ctx, script_path)
    mark_file_read(ctx, path)
    content = path.read_text(encoding="utf-8", errors="replace")
    limit = max(1000, min(200000, int(max_chars or 20000)))
    truncated = len(content) > limit
    return {
        "success": True,
        "path": _rel(workspace, path),
        "abs_path": str(path),
        "metadata": _load_json(path.with_suffix(".metadata.json")),
        "content": content[:limit],
        "truncated": truncated,
        "size_chars": len(content),
    }
