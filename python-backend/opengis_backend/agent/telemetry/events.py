"""
Agent event layer — framework-agnostic.

This module owns the in-process protocol between the agent core and whoever
is listening. Live transport projection is handled by ``event_log`` and the
JSON-RPC handler, which emit MessagePart-first ``chat.message_part`` events.

"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Event schema
# ──────────────────────────────────────────────────────────────────────
class AgentEventType(str, Enum):
    """Types of events emitted by the agent during execution."""

    STREAM_DELTA = "stream_delta"      # Streaming text token (final answer)
    STREAM_END = "stream_end"          # Stream completed
    CODE_BLOCK = "code_block"          # Agent emitted a code block (full, post-execution)
    CODE_BLOCK_START = "code_block_start"  # Streaming code block has started
    CODE_DELTA = "code_delta"          # Streaming chunk of code being written
    CODE_BLOCK_END = "code_block_end"  # Streaming code block finished writing
    CODE_RESULT = "code_result"        # Code block executed; observation
    TOOL_START = "tool_start"          # Tool execution started
    TOOL_OUTPUT_DELTA = "tool_output_delta"  # Live stdout from a running tool
    TOOL_RESULT = "tool_result"        # Tool execution finished
    PROGRESS = "progress"              # Execution progress indicator
    TITLE_GENERATED = "title_generated" # Auto-generated conversation title
    ERROR = "error"                    # Error occurred


@dataclass
class AgentEvent:
    type: AgentEventType
    data: Any = None


# ──────────────────────────────────────────────────────────────────────
# Cross-thread event plumbing
# ──────────────────────────────────────────────────────────────────────

def _enqueue(
    loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[Optional[AgentEvent]]",
    event: Optional[AgentEvent],
) -> None:
    """
    Thread-safe enqueue of an AgentEvent onto an asyncio.Queue that lives
    on ``loop``.

    Used by the synchronous step callbacks running in the agent loop's
    worker thread to push events back to the main event loop.
    """
    try:
        if loop.is_closed():
            return
        loop.call_soon_threadsafe(queue.put_nowait, event)
    except RuntimeError:
        # Loop already shut down (e.g. websocket closed mid-run).
        logger.debug("enqueue dropped — loop closed")


__all__ = [
    "AgentEvent",
    "AgentEventType",
    "_enqueue",
]
