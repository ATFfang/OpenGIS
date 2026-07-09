"""
StepRecorder — turn AgentStep objects into AgentEvents.

Every time the agent loop finishes a step it calls our registered
``step_callback(agent_step)``. The callback needs to do three things:

1. Persist the code to disk (so the UI can link to ``scripts/step-N.py``).
2. Enqueue CODE_BLOCK / CODE_RESULT events for the main event loop.
3. (Optionally) mirror the step into the RunArchive.

The recorder never raises out of ``on_step`` — any error is logged
and swallowed, because a raising callback will take down the agent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from opengis_backend.agent.loop.types import AgentStep
from opengis_backend.agent.telemetry.events import (
    AgentEvent,
    AgentEventType,
    _enqueue,
)
from opengis_backend.agent.telemetry.script_archive import ScriptArchive

logger = logging.getLogger(__name__)


# ── Thinking stage labels (for progress_callback) ──
# These map internal stage names to user-friendly descriptions.
THINKING_STAGES = {
    "calling_llm": "calling_llm",
    "thinking_next_step": "thinking_next_step",
    "executing_code": "executing_code",
    "generating_summary": "generating_summary",
}


@dataclass
class StepRecorder:
    """Adapter from AgentStep → ScriptArchive + AgentEvent queue.

    Parameters
    ----------
    archive:
        Where to persist emitted code.
    loop:
        The asyncio event loop on which ``queue`` lives. StepRecorder is
        normally invoked from the agent loop's worker thread, so we use
        ``call_soon_threadsafe`` via :func:`_enqueue` to hop back.
    queue:
        Event queue the :class:`AgentRunner` is draining.
    user_message:
        Original user message, stored in each script's header.
    """

    archive: ScriptArchive
    loop: asyncio.AbstractEventLoop
    queue: "asyncio.Queue[Optional[AgentEvent]]"
    user_message: str

    def on_step(self, step: AgentStep) -> None:
        """Agent loop step_callback entry point.

        Must not raise. Errors are logged; the agent run continues.
        """
        try:
            self._record(step)
        except Exception:  # pragma: no cover — defensive; logged
            logger.exception("step_callback failed for step %s", step.step_num)

    def on_progress(self, stage: str, detail: str = "") -> None:
        """Emit a THINKING event to the UI.

        Called by the agent loop BEFORE an LLM call or code execution
        so the user sees real-time status instead of a blank screen.

        Must not raise. Errors are logged; the agent run continues.
        """
        try:
            _enqueue(
                self.loop,
                self.queue,
                AgentEvent(
                    type=AgentEventType.THINKING,
                    data={
                        "stage": stage,
                        "message": detail,
                        "run_id": self.archive.run_id,
                    },
                ),
            )
        except Exception:
            logger.exception("on_progress failed for stage %s", stage)

    # ── internals ──

    def _record(self, step: AgentStep) -> None:
        # Skip pure text replies — they don't produce code to archive.
        if step.is_text_reply:
            return

        # Skip steps with no code (shouldn't happen, but defensive).
        if not step.code:
            return

        step_no = step.step_num

        # Emit PROGRESS event — indicates what stage we're in.
        stage = self._detect_stage(step.code)
        _enqueue(
            self.loop,
            self.queue,
            AgentEvent(
                type=AgentEventType.PROGRESS,
                data={
                    "stage": stage,
                    "step": step_no,
                    "run_id": self.archive.run_id,
                },
            ),
        )

        # Persist the code to disk.
        abs_path = self.archive.write_step(
            step=step_no,
            code=step.code,
            user_message=self.user_message,
            observations=step.output,
            error=step.error,
        )
        rel_path = self.archive.to_relative(abs_path)

        # Emit CODE_BLOCK event.
        _enqueue(
            self.loop,
            self.queue,
            AgentEvent(
                type=AgentEventType.CODE_BLOCK,
                data={
                    "step": step_no,
                    "code": step.code,
                    "script_path": rel_path,
                    "script_abs_path": str(abs_path),
                    "run_id": self.archive.run_id,
                },
            ),
        )

        # Emit CODE_RESULT event.
        _enqueue(
            self.loop,
            self.queue,
            AgentEvent(
                type=AgentEventType.CODE_RESULT,
                data={
                    "step": step_no,
                    "output": step.output or "",
                    "error": step.error,
                    "run_id": self.archive.run_id,
                    "duration_ms": step.duration_ms,
                },
            ),
        )

    @staticmethod
    def _detect_stage(code: str) -> str:
        """Heuristically detect what the code is doing for progress display."""
        code_lower = code.lower()

        if "pip install" in code_lower or "subprocess" in code_lower:
            return "installing_packages"
        if "import geopandas" in code_lower or "import fiona" in code_lower or "gpd.read_file" in code_lower:
            return "loading_geodata"
        if "import rasterio" in code_lower or "rasterio.open" in code_lower:
            return "loading_raster"
        if "import pandas" in code_lower or "pd.read_csv" in code_lower or "pd.read_excel" in code_lower:
            return "loading_data"
        if "buffer" in code_lower or "intersection" in code_lower or "union" in code_lower or "overlay" in code_lower:
            return "spatial_analysis"
        if "plot" in code_lower or "matplotlib" in code_lower or "plt." in code_lower:
            return "generating_visualization"
        if "add_layer" in code_lower or "fly_to" in code_lower:
            return "rendering_map"
        if "to_file" in code_lower or "to_csv" in code_lower or "save" in code_lower:
            return "saving_results"

        return "executing_code"


__all__ = ["StepRecorder"]
