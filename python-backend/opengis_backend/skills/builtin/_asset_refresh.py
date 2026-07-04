"""Notify the frontend AssetExplorer after workspace file mutations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from opengis_backend.skills.context import SkillContext, run_async_from_sync

logger = logging.getLogger(__name__)


def notify_asset_refresh(
    ctx: SkillContext | None,
    path: str | Path | None = None,
    *,
    reason: str = "file_changed",
) -> None:
    """Best-effort Python -> frontend file-tree refresh notification."""
    if ctx is None:
        return

    payload: dict[str, Any] = {"reason": reason}
    if path is not None:
        payload["path"] = str(path)

    try:
        run_async_from_sync(ctx.notify("rpc.ui.fs.refresh_assets", payload))
    except Exception:
        logger.debug("asset refresh notification failed", exc_info=True)
