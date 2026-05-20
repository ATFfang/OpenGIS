"""Persistent storage for user instructions.

Stores a single markdown file at ``~/.opengis/user_instructions.md``.
Each line is one instruction, prefixed with ``[user]`` or ``[agent]``.

The file is read/written atomically.  A hard cap of 2000 characters is
enforced — when the agent appends and the total exceeds the cap, the
oldest ``[agent]`` lines are dropped first (``[user]`` lines are never
touched).
"""

from __future__ import annotations

import os
from pathlib import Path

_MAX_CHARS = 2000
_FILE_PATH = Path.home() / ".opengis" / "user_instructions.md"


def _ensure_parent() -> None:
    _FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load() -> str:
    """Return the raw content of user_instructions.md (may be empty)."""
    if not _FILE_PATH.exists():
        return ""
    return _FILE_PATH.read_text(encoding="utf-8").strip()


def save(content: str) -> None:
    """Write content to user_instructions.md, truncating to the cap."""
    _ensure_parent()
    text = content.strip()[:_MAX_CHARS]
    _FILE_PATH.write_text(text + "\n", encoding="utf-8")


def append_agent_entry(entry: str) -> str:
    """Append a single ``[agent]`` entry and return the new full content.

    If the total exceeds ``_MAX_CHARS`` after appending, the oldest
    ``[agent]`` lines are dropped until the content fits.
    ``[user]`` lines are never removed.
    """
    current = load()
    line = f"[agent] {entry.strip()}"

    # Build new content
    if current:
        new_content = current + "\n" + line
    else:
        new_content = line

    # Enforce length cap by dropping oldest [agent] lines
    lines = new_content.split("\n")
    while len("\n".join(lines)) > _MAX_CHARS:
        # Find the first [agent] line to drop
        dropped = False
        for i, l in enumerate(lines):
            if l.startswith("[agent]"):
                lines.pop(i)
                dropped = True
                break
        if not dropped:
            # All remaining lines are [user] — hard truncate
            break

    final = "\n".join(lines)
    save(final)
    return final
