"""Shared file-read state for mutation safety.

The agent tool layer uses this to enforce a mainstream read-before-write
contract without persisting sensitive file hashes outside the current run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from opengis_backend.tools.context import ToolContext


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def mark_file_read(ctx: ToolContext | None, path: Path) -> dict[str, Any] | None:
    if ctx is None or ctx.meta is None or not path.exists() or not path.is_file():
        return None
    try:
        fingerprint = file_fingerprint(path)
    except Exception:
        return None
    reads = ctx.meta.setdefault("_read_file_fingerprints", {})
    reads[str(path.resolve())] = fingerprint
    return fingerprint


def get_read_fingerprint(ctx: ToolContext | None, path: Path) -> dict[str, Any] | None:
    if ctx is None or ctx.meta is None:
        return None
    reads = ctx.meta.get("_read_file_fingerprints")
    if not isinstance(reads, dict):
        return None
    value = reads.get(str(path.resolve()))
    return value if isinstance(value, dict) else None


def file_matches_fingerprint(path: Path, fingerprint: dict[str, Any]) -> bool:
    try:
        current = file_fingerprint(path)
    except Exception:
        return False
    return (
        current.get("size") == fingerprint.get("size")
        and current.get("mtime_ns") == fingerprint.get("mtime_ns")
        and current.get("sha256") == fingerprint.get("sha256")
    )
