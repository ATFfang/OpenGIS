"""Project-level memory — persistent knowledge per workspace.

Stores key facts, preferences, and context at ``<workspace>/.opengis/memory.md``.
Injected into the agent's system prompt so it remembers across conversations.

Unlike ``user_instructions.md`` (global, manual), this file is:
- Per-workspace (each project has its own memory)
- Semi-automatic (agent can append entries after runs)
- Structured (sections for different types of knowledge)

File format:
    ## User Preferences
    - 偏好中文回复
    - 使用学术风格

    ## Project Context
    - 当前在分析上海市饮品店空间分布
    - 数据来源：美团POI

    ## Known Issues
    - geopandas 的 to_crs 在 macOS 上有时会报 PROJ 错误

    ## Frequently Used
    - 常用底图：carto-light-nolabels
    - 常用输出格式：GeoJSON
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MEMORY_FILENAME = ".opengis/memory.md"
_MAX_CHARS = 5000


def _memory_path(workspace: str) -> Path:
    return Path(workspace) / _MEMORY_FILENAME


def load(workspace: str) -> str:
    """Return the raw content of memory.md (may be empty)."""
    path = _memory_path(workspace)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("Failed to load memory from %s: %s", path, e)
        return ""


def save(workspace: str, content: str) -> None:
    """Write content to memory.md, truncating to the cap."""
    path = _memory_path(workspace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content.strip()[:_MAX_CHARS]
        path.write_text(text + "\n", encoding="utf-8")
        logger.debug("Memory saved to %s (%d chars)", path, len(text))
    except Exception as e:
        logger.warning("Failed to save memory to %s: %s", path, e)


def append(workspace: str, section: str, entry: str) -> str:
    """Append an entry under a section header. Creates section if missing.

    Returns the updated full content.
    """
    current = load(workspace)
    entry = entry.strip()
    if not entry:
        return current

    section_header = f"## {section}"

    if section_header in current:
        # Append under existing section
        lines = current.split("\n")
        insert_idx = len(lines)  # default: end
        in_section = False
        for i, line in enumerate(lines):
            if line.strip() == section_header:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                insert_idx = i
                break
        # Insert before the next section (or at end)
        lines.insert(insert_idx, f"- {entry}")
        new_content = "\n".join(lines)
    else:
        # Create new section at end
        if current:
            new_content = f"{current}\n\n{section_header}\n- {entry}"
        else:
            new_content = f"{section_header}\n- {entry}"

    # Enforce length cap — drop oldest entries from non-essential sections
    if len(new_content) > _MAX_CHARS:
        new_content = _truncate_to_fit(new_content)

    save(workspace, new_content)
    return new_content


def _truncate_to_fit(content: str) -> str:
    """Truncate content to _MAX_CHARS by dropping oldest non-user entries."""
    lines = content.split("\n")
    # Keep dropping from the end until it fits
    while len("\n".join(lines)) > _MAX_CHARS and len(lines) > 1:
        lines.pop()
    return "\n".join(lines)
