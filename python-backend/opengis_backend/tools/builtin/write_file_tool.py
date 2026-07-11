"""write_file tool — create or overwrite a file (OpenCode-style).

Writes content to a file, creating parent directories if needed.
Preserves BOM if the existing file has one.
Optionally runs a formatter (black/ruff) after writing.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from opengis_backend.tools.registry import tool
from opengis_backend.tools.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.tools.builtin._file_state import get_read_fingerprint, file_matches_fingerprint

logger = logging.getLogger(__name__)

# 常见格式化工具（按优先级）
_FORMATTERS = [
    "black",   # Python formatter
    "ruff",    # Python linter/formatter
    "prettier",  # JS/TS formatter
]


def _syntax_diagnostics(path: Path, content: str) -> list[dict[str, Any]]:
    ext = path.suffix.lower()
    diagnostics: list[dict[str, Any]] = []
    if ext == ".py":
        try:
            ast.parse(content, filename=str(path))
        except SyntaxError as e:
            diagnostics.append({
                "severity": "error",
                "source": "python",
                "line": e.lineno,
                "column": e.offset,
                "message": e.msg,
            })
    elif ext == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            diagnostics.append({
                "severity": "error",
                "source": "json",
                "line": e.lineno,
                "column": e.colno,
                "message": e.msg,
            })
    elif ext in {".ts", ".tsx", ".js", ".jsx"} and shutil.which("npx"):
        try:
            result = subprocess.run(
                ["npx", "--yes", "tsc", "--noEmit", "--pretty", "false", "--allowJs", str(path)],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=path.parent,
            )
            if result.returncode != 0:
                output = (result.stdout + "\n" + result.stderr).strip()
                diagnostics.append({
                    "severity": "error",
                    "source": "tsc",
                    "message": output[:4000],
                })
        except Exception as e:
            diagnostics.append({
                "severity": "warning",
                "source": "tsc",
                "message": f"diagnostic unavailable: {e}",
            })
    return diagnostics


def _detect_bom(data: bytes) -> tuple[str, bytes]:
    """Detect and return (encoding, bom_bytes)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", data[:3]
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le", data[:2]
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be", data[:2]
    return "utf-8", b""


def _write_sync(
    file_path: str,
    content: str,
    workspace_path: str | None = None,
    ctx=None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Synchronous file writer (runs in executor)."""
    path = Path(file_path).resolve()

    # 安全检查：必须是绝对路径，且必须在允许的工作区内
    if not path.is_absolute():
        return {
            "success": False,
            "error": f"Path must be absolute: {file_path}",
            "path": file_path,
        }

    # 防止路径遍历：检查解析后的路径是否在工作区内
    # 优先使用传入的 workspace_path，否则 fallback 到 cwd
    ws_raw = workspace_path or os.environ.get("WORKSPACE_PATH") or os.getcwd()
    workspace = Path(ws_raw).resolve()
    if not str(path).startswith(str(workspace)):
        return {
            "success": False,
            "error": f"Path outside workspace not allowed: {file_path}",
            "path": file_path,
            "workspace": str(workspace),
        }

    try:
        if path.exists() and not overwrite:
            fingerprint = get_read_fingerprint(ctx, path)
            if fingerprint is None:
                return {
                    "success": False,
                    "error": (
                        "Refusing to overwrite an existing file before read_file. "
                        "Read the current file first, then call write_file again."
                    ),
                    "path": str(path),
                    "requires_read": True,
                }
            if not file_matches_fingerprint(path, fingerprint):
                return {
                    "success": False,
                    "error": (
                        "File changed after it was read. Read it again before writing "
                        "to avoid overwriting user edits."
                    ),
                    "path": str(path),
                    "stale_read": True,
                }

        # 保留已有文件的 BOM
        existing_bom = b""
        if path.exists():
            with open(path, "rb") as f:
                raw = f.read(3)
            _, existing_bom = _detect_bom(raw)

        # 创建父目录
        path.parent.mkdir(parents=True, exist_ok=True)

        # 写入（保留 BOM）
        encoded = content.encode("utf-8")
        if existing_bom:
            encoded = existing_bom + encoded

        with open(path, "wb") as f:
            f.write(encoded)

        # 尝试格式化
        diagnostics: list[Any] = []
        try:
            for fmt in _FORMATTERS:
                result = subprocess.run(
                    [fmt, "--check", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    # 格式化工具可用，运行它
                    subprocess.run(
                        [fmt, str(path)],
                        capture_output=True,
                        timeout=30,
                    )
                    diagnostics.append({"severity": "info", "source": fmt, "message": f"Formatted with {fmt}"})
                    break
        except Exception:
            pass  # 格式化失败不影响写入成功

        try:
            final_content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            final_content = content
        diagnostics.extend(_syntax_diagnostics(path, final_content))

        return {
            "success": True,
            "path": str(path),
            "error": None,
            "diagnostics": diagnostics,
            "diagnostic_error_count": sum(
                1 for d in diagnostics if isinstance(d, dict) and d.get("severity") == "error"
            ),
        }

    except Exception as e:
        logger.error("[write_file] failed: %s", e)
        return {"success": False, "error": str(e), "path": file_path}


@tool(
    name="write_file",
    display_name="Write File",
    description=(
        "Write content to a file. Existing files must be read with read_file first "
        "unless overwrite=true is explicitly used. "
        "Creates parent directories if needed. "
        "Preserves BOM of existing files. "
        "Runs formatter (black/ruff/prettier) if available and returns syntax diagnostics. "
        "Prefer this for creating new files; use edit_file for modifying existing ones."
    ),
    category="system",
    params=[
        {"name": "file_path", "type": "string", "description": "Absolute path to the file to write."},
        {"name": "content", "type": "string", "description": "The full content to write to the file."},
        {"name": "overwrite", "type": "boolean", "required": False,
         "description": "Explicitly allow overwriting an existing file without prior read_file. Default false."},
    ],
    returns="dict with keys: success (bool), path (str), error (str|null), diagnostics (list), diagnostic_error_count (int)",
    needs_context=True,
    examples=[
        "write_file('/workspace/main.py', 'def hello():\\n    print(\"hello\")')",
    ],
)
def write_file(
    ctx,
    file_path: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write file content.

    NOTE: Synchronous — invoked from the agent's worker thread.
    """
    workspace_path = None
    if ctx is not None:
        workspace_path = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    result = _write_sync(
        file_path,
        content,
        workspace_path=workspace_path,
        ctx=ctx,
        overwrite=overwrite,
    )
    if result.get("success"):
        notify_asset_refresh(ctx, result.get("path") or file_path, reason="write_file")
    return result
