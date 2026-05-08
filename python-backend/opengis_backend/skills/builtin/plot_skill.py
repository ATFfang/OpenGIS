"""
plot_skill — let the LLM emit matplotlib/seaborn/plotly figures and have
them rendered inline in the chat panel.

Design rules (per implementation plan blazing-vortex-einstein.md):

  1. The image **bytes are NOT shipped over RPC**.  We save the figure
     into ``<workspace>/assets/plots/`` and only send the absolute path
     to the frontend.  The renderer reads it via electronAPI.readFileAsBuffer.

  2. The skill is fire-and-forget from the agent's POV: it returns the
     path string so the LLM has something to show in the transcript,
     but the actual UI delivery happens through ``rpc.ui.chat.show_image``.

  3. Pin-to-map is a frontend concern.  This skill doesn't know about
     coordinates — the user clicks the "Pin" button on the chat image
     and the renderer decides where to anchor it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from opengis_backend.skills.context import SkillContext, run_async_from_sync
from opengis_backend.skills.registry import skill


def _resolve_assets_dir(ctx: SkillContext) -> Path:
    """Pick the right place to write the figure.

    Priority:
      1. ``<workspace>/assets/plots/`` if a workspace is open.
      2. ``<cwd>/assets/plots/`` as a fallback for headless / no-ws runs.
    """
    workspace = (ctx.meta or {}).get("workspace_path")
    base = Path(workspace) if workspace else Path.cwd()
    target = base / "assets" / "plots"
    target.mkdir(parents=True, exist_ok=True)
    return target


@skill(
    name="save_plot",
    display_name="Save & Show Plot",
    description=(
        "Save the current matplotlib figure to the workspace assets/ folder "
        "AND show it in the chat panel. Use this right after building a "
        "matplotlib/seaborn chart (no need to call plt.show()). "
        "Returns the absolute path of the saved PNG. The user can click "
        "'Pin to Map' on the image to overlay it on the map."
    ),
    category="visualization",
    params=[
        {"name": "caption", "type": "string", "required": False,
         "description": "Optional caption shown under the image in chat."},
        {"name": "filename", "type": "string", "required": False,
         "description": "Custom filename stem (no extension). Auto-generated "
                        "from a timestamp if omitted."},
        {"name": "dpi", "type": "number", "required": False,
         "description": "PNG resolution. Default 150."},
    ],
    returns="Absolute path string of the saved PNG.",
    examples=[
        "Plot the histogram of housing prices and save_plot()",
        "Show the correlation heatmap in chat",
    ],
    tags=["plot", "matplotlib", "visualization", "chat"],
    needs_context=True,
)
def save_plot(
    ctx: SkillContext,
    caption: Optional[str] = None,
    filename: Optional[str] = None,
    dpi: Optional[float] = None,
    auto_close: bool = True,
) -> str:
    """Save matplotlib's current figure and notify the chat UI."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for save_plot. "
            "Install it with: pip install matplotlib"
        ) from exc

    fig = plt.gcf()
    if not fig.get_axes():
        raise RuntimeError(
            "save_plot: no active matplotlib figure to save. "
            "Build a chart first (plt.plot / sns.histplot / ...) then call save_plot()."
        )

    assets_dir = _resolve_assets_dir(ctx)
    stem = (filename or f"plot_{int(time.time() * 1000)}").strip()
    # Avoid path traversal. Force a flat filename.
    stem = Path(stem).name or f"plot_{int(time.time() * 1000)}"
    fpath = assets_dir / f"{stem}.png"

    fig.savefig(
        str(fpath),
        dpi=int(dpi) if dpi else 150,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
    )

    abs_path = str(fpath.resolve())

    # Push to chat panel — fire-and-forget. Even if the frontend isn't
    # listening (e.g. headless test), the file is already on disk.
    payload: dict = {"path": abs_path}
    if caption:
        payload["caption"] = caption
    run_async_from_sync(ctx.notify("rpc.ui.chat.show_image", payload))

    # Free the figure so subsequent calls don't pile up memory.
    if auto_close:
        plt.close(fig)

    return abs_path
