"""Context persistence — save/load ContextManager state to disk.

Persists conversation context to `<workspace>/.opengis/contexts/<conversation_id>.json`
so the agent retains memory across app restarts.

Usage:
    from opengis_backend.agent.context.context_persistence import save_context, load_context

    # After each agent run
    save_context(workspace, conversation_id, context_manager)

    # Before first run in a conversation
    ctx = load_context(workspace, conversation_id)
    if ctx is None:
        ctx = ContextManager()  # fresh
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from opengis_backend.agent.context.context_manager import ContextManager

logger = logging.getLogger(__name__)

_CONTEXTS_DIR = ".opengis/contexts"


def _context_path(workspace: str, conversation_id: str) -> Path:
    """Return the path to the context file for a conversation."""
    return Path(workspace) / _CONTEXTS_DIR / f"{conversation_id}.json"


def save_context(workspace: str, conversation_id: str, ctx: ContextManager) -> None:
    """Persist a ContextManager's state to disk.

    Safe to call after every agent run. Creates the directory if needed.
    Never raises — logs errors and returns silently.
    """
    try:
        path = _context_path(workspace, conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = ctx.to_dict()
        # Add metadata for debugging
        data["_meta"] = {
            "conversation_id": conversation_id,
            "message_count": len(ctx.messages),
            "has_summary": ctx._summary is not None,
        }

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Context saved: %s (%d messages)", conversation_id, len(ctx.messages))
    except Exception as e:
        logger.warning("Failed to save context for %s: %s", conversation_id, e)


def load_context(workspace: str, conversation_id: str) -> Optional[ContextManager]:
    """Load a ContextManager from disk.

    Returns None if no persisted context exists or if loading fails.
    """
    try:
        path = _context_path(workspace, conversation_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        ctx = ContextManager.from_dict(data)
        logger.info(
            "Context restored: %s (%d messages, summary=%s)",
            conversation_id,
            len(ctx.messages),
            "yes" if ctx._summary else "no",
        )
        return ctx
    except Exception as e:
        logger.warning("Failed to load context for %s: %s", conversation_id, e)
        return None


def delete_context(workspace: str, conversation_id: str) -> None:
    """Delete a persisted context file. Safe to call if file doesn't exist."""
    try:
        path = _context_path(workspace, conversation_id)
        if path.exists():
            path.unlink()
            logger.debug("Context deleted: %s", conversation_id)
    except Exception as e:
        logger.warning("Failed to delete context for %s: %s", conversation_id, e)
