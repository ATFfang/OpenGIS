"""Workspace lifecycle utilities.

OpenGIS treats every user workspace as a git-tracked directory so that
agent runs can be snapshotted (pre / post) and reverted. The concrete
snapshot / revert logic lives in later modules; this package currently
exposes only :class:`WorkspaceManager` which is responsible for
initialising a workspace on first use.

Design tenet (MEMORY "OpenGIS 第 1 号产品定位"):
    We do NOT sandbox via Docker. We rely on `git init` + per-run
    snapshots as the safety net, exactly like Claude Code. Everything
    here must therefore be cheap enough to run on every chat turn.
"""

from __future__ import annotations

from .manager import (
    GitNotAvailableError,
    WorkspaceInfo,
    WorkspaceManager,
    WorkspaceManagerError,
)

__all__ = [
    "GitNotAvailableError",
    "WorkspaceInfo",
    "WorkspaceManager",
    "WorkspaceManagerError",
]
