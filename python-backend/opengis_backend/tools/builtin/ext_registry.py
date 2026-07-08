"""Tool: list_extensions — query available map rendering extensions.

Returns the capabilities of all registered extension renderers so the agent
knows what enhanced visualization options are available beyond the base layer
rendering (fill, line, circle, heatmap, etc.).

Extensions are self-describing: each declares a name, description, and
parameter schema. This tool module aggregates them into a single list for the
agent's system prompt or ad-hoc queries.
"""

from __future__ import annotations

from opengis_backend.tools.registry import tool

# ── Extension registry (static for now, future: dynamic discovery) ──────
# Each extension dict mirrors ExtensionCapability on the frontend side.

EXTENSIONS: list[dict] = []


@tool(
    name="list_extensions",
    display_name="List Extensions",
    description=(
        "List all available map rendering extensions and their capabilities. "
        "Use this to discover enhanced visualization options beyond the base "
        "layer rendering (e.g. chart overlays, trajectory animation, elevation profiles)."
    ),
    category="data",
    group="core",
    params=[],
    returns="dict with 'extensions' list, each containing name, display_name, description, params",
)
def list_extensions() -> dict:
    """Return all registered extension capabilities."""
    return {"extensions": EXTENSIONS}


def register_extension(ext: dict) -> None:
    """Register an extension capability at module level.

    Called by extension modules during tool discovery to announce
    their rendering capabilities to the agent.
    """
    name = ext.get("name")
    if not name:
        return
    # Deduplicate by name
    for i, existing in enumerate(EXTENSIONS):
        if existing.get("name") == name:
            EXTENSIONS[i] = ext
            return
    EXTENSIONS.append(ext)
