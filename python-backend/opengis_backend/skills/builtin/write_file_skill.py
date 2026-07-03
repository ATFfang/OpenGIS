"""write_file skill — create or overwrite a file (OpenCode-style).

Writes content to a file, creating parent directories if needed.
Preserves BOM if the existing file has one.
Optionally runs a formatter (black/ruff) after writing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from opengis_backend.skills.registry import skill

logger = logging.getLogger(__name__)

# 常见格式化工具（按优先级）
_FORMATTERS = [
    "black",   # Python formatter
    "ruff",    # Python linter/formatter
    "prettier",  # JS/TS formatter
]


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
        diagnostics: list[str] = []
        try:
            import subprocess
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
                    diagnostics.append(f"Formatted with {fmt}")
                    break
        except Exception:
            pass  # 格式化失败不影响写入成功

        return {
            "success": True,
            "path": str(path),
            "error": None,
            "diagnostics": diagnostics,
        }

    except Exception as e:
        logger.error("[write_file] failed: %s", e)
        return {"success": False, "error": str(e), "path": file_path}


@skill(
    name="write_file",
    display_name="Write File",
    description=(
        "Write content to a file (create or overwrite). "
        "Creates parent directories if needed. "
        "Preserves BOM of existing files. "
        "Runs formatter (black/ruff) if available. "
        "Prefer this for creating new files; use edit_file for modifying existing ones."
    ),
    category="system",
    params=[
        {"name": "file_path", "type": "string", "description": "Absolute path to the file to write."},
        {"name": "content", "type": "string", "description": "The full content to write to the file."},
    ],
    returns="dict with keys: success (bool), path (str), error (str|null), diagnostics (list)",
    needs_context=True,
    examples=[
        "write_file('/workspace/main.py', 'def hello():\\n    print(\"hello\")')",
    ],
)
def write_file(
    ctx,
    file_path: str,
    content: str,
) -> dict[str, Any]:
    """Write file content.

    NOTE: Synchronous — invoked from the agent's worker thread.
    """
    workspace_path = None
    if ctx is not None:
        workspace_path = (getattr(ctx, "meta", None) or {}).get("workspace_path")
    return _write_sync(file_path, content, workspace_path=workspace_path)
