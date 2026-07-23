"""3D Tiles / point-cloud asset registry and static file serving helpers.

MapLibre GL cannot natively render OGC 3D Tiles (tileset.json + b3dm/i3dm/pnts
+ glTF) or bare point clouds (.las/.laz). The frontend renders these through
deck.gl, which fetches the tileset / point-cloud files over HTTP.

This module is the backend half: it registers a local directory (a 3D Tiles
set) or a single file (a point cloud) under an opaque ``asset_id`` and exposes a
guarded resolver that the FastAPI static endpoint uses to stream individual
files. This lets the agent point at a file on disk and have it served to the
renderer without exposing the whole filesystem.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

# glTF / 3D Tiles payload content types loaders.gl is happy to receive.
_EXTRA_MIME = {
    ".json": "application/json",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".b3dm": "application/octet-stream",
    ".i3dm": "application/octet-stream",
    ".pnts": "application/octet-stream",
    ".cmpt": "application/octet-stream",
    ".las": "application/octet-stream",
    ".laz": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".terrain": "application/octet-stream",
}


@dataclass(frozen=True)
class AssetRegistration:
    asset_id: str
    root: str          # absolute directory that is served
    entry: str         # relative entry file (tileset.json or the point cloud)
    kind: str          # "3dtiles" | "pointcloud"

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "root": self.root,
            "entry": self.entry,
            "kind": self.kind,
        }


_LOCK = RLock()
_REGISTRY: dict[str, AssetRegistration] = {}

# 常见的 3D Tiles 入口文件名。
_TILESET_CANDIDATES = ("tileset.json",)


def register_tileset(path: str) -> AssetRegistration:
    """Register a 3D Tiles set.

    ``path`` may be either the ``tileset.json`` file itself or the directory
    that contains it. The whole directory (recursively) becomes servable so the
    tileset's child tiles resolve relative to the entry file.
    """
    p = Path(path).expanduser().resolve()
    if p.is_dir():
        entry_file: Path | None = None
        for candidate in _TILESET_CANDIDATES:
            if (p / candidate).is_file():
                entry_file = p / candidate
                break
        if entry_file is None:
            json_files = sorted(p.glob("*.json"))
            if not json_files:
                raise FileNotFoundError(
                    f"No tileset.json (or *.json) found in directory: {p}"
                )
            entry_file = json_files[0]
        root = p
    else:
        if not p.exists():
            raise FileNotFoundError(f"Tileset file not found: {p}")
        root = p.parent
        entry_file = p

    reg = AssetRegistration(
        asset_id=f"a3d_{uuid4().hex}",
        root=str(root),
        entry=entry_file.name,
        kind="3dtiles",
    )
    with _LOCK:
        _REGISTRY[reg.asset_id] = reg
    return reg


def register_point_cloud(path: str) -> AssetRegistration:
    """Register a single point-cloud file (.las/.laz) for HTTP serving."""
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Point cloud file not found: {p}")
    reg = AssetRegistration(
        asset_id=f"apc_{uuid4().hex}",
        root=str(p.parent),
        entry=p.name,
        kind="pointcloud",
    )
    with _LOCK:
        _REGISTRY[reg.asset_id] = reg
    return reg


def get_asset(asset_id: str) -> AssetRegistration:
    with _LOCK:
        reg = _REGISTRY.get(asset_id)
    if not reg:
        raise KeyError(f"Asset '{asset_id}' is not registered.")
    return reg


def resolve_asset_file(asset_id: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` under the asset's served root, with traversal guard.

    Raises ``KeyError`` if the asset is unknown, ``PermissionError`` on an
    attempted path escape, and ``FileNotFoundError`` if the file is missing.
    """
    reg = get_asset(asset_id)
    root = Path(reg.root).resolve()
    rel = (rel_path or "").strip().lstrip("/\\")
    target = (root / rel).resolve()
    # Directory-traversal guard: target must stay within root.
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - defensive
        raise PermissionError(f"Path escapes asset root: {rel_path}") from exc
    if not target.is_file():
        raise FileNotFoundError(f"Asset file not found: {rel_path}")
    return target


def guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


__all__ = [
    "AssetRegistration",
    "get_asset",
    "guess_media_type",
    "register_point_cloud",
    "register_tileset",
    "resolve_asset_file",
]
