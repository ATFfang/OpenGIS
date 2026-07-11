"""Tool: update_user_instructions — agent self-optimization.

Allows the agent to append learned user preferences to the global
user instructions file. Entries are tagged ``[agent]`` and subject
to a 2000-character cap (oldest agent entries are dropped first).
"""

from __future__ import annotations

from typing import Any

from opengis_backend.tools.registry import tool
from opengis_backend.tools.context import ToolContext, run_async_from_sync
from opengis_backend.user_prefs.store import append_agent_entry


@tool(
    name="update_user_instructions",
    display_name="Update User Instructions",
    description=(
        "Append a learned user preference to the global user instructions. "
        "Use this when you notice a recurring pattern in the user's requests "
        "(e.g. preferred coordinate system, language, libraries, visualization style). "
        "Each entry should be ONE concise sentence. "
        "Do NOT use this for conversation-specific context — only for "
        "preferences that should persist across ALL future conversations."
    ),
    category="orchestration",
    group="core",
    params=[
        {
            "name": "entry",
            "type": "string",
            "required": True,
            "description": "The preference to append (one sentence, e.g. 'User prefers CGCS2000 (EPSG:4490) as default CRS').",
        },
    ],
    returns="dict with 'content' (updated full instructions) and 'appended' (the entry added)",
    needs_context=True,
)
def update_user_instructions(ctx: ToolContext, entry: str) -> dict[str, Any]:
    """Append a [agent] entry to user instructions, enforce length limit."""
    updated = append_agent_entry(entry)

    # Push updated instructions to the frontend so settingsStore stays in sync.
    run_async_from_sync(
        ctx.notify("user_instructions.updated", {"content": updated})
    )

    return {
        "content": updated,
        "appended": f"[agent] {entry.strip()}",
    }
