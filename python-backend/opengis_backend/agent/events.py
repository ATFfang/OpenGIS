"""
Agent event layer — framework-agnostic.

This module owns the *protocol* between the agent core and whoever is
listening (today: the JSON-RPC handler; tomorrow: HTTP SSE, a CLI, a
test harness). It deliberately imports nothing from the agent loop
internals or from our JSON-RPC stack, so events can be produced and
consumed by arbitrary orchestrators.

Two things live here:

1. ``AgentEvent`` / ``AgentEventType`` — the in-process event schema.
2. ``EventTranslator`` — a pure mapping from ``AgentEvent`` to the
   canonical ``chat.*`` JSON-RPC tuple ``(method, params)``.

v3.1 (2026-04): Removed smolagents MemoryStep introspection helpers
(_extract_code, _extract_observations, _extract_error). These are no
longer needed since our custom AgentStep already has parsed fields.
The helpers are kept as no-op stubs for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Event schema
# ──────────────────────────────────────────────────────────────────────
class AgentEventType(str, Enum):
    """Types of events emitted by the agent during execution."""

    STREAM_DELTA = "stream_delta"      # Streaming text token (final answer)
    STREAM_END = "stream_end"          # Stream completed
    REASONING_DELTA = "reasoning_delta"      # Streaming chunk of agent's thinking (pre-code thought)
    REASONING_END = "reasoning_end"          # Reasoning paragraph finalised (next thing is code or new round)
    REASONING_PROMOTE = "reasoning_promote"  # Promote the current reasoning msg to a normal text reply
    CODE_BLOCK = "code_block"          # Agent emitted a code block (full, post-execution)
    CODE_BLOCK_START = "code_block_start"  # Streaming code block has started
    CODE_DELTA = "code_delta"          # Streaming chunk of code being written
    CODE_BLOCK_END = "code_block_end"  # Streaming code block finished writing
    CODE_RESULT = "code_result"        # Code block executed; observation
    TOOL_START = "tool_start"          # Skill execution started
    TOOL_OUTPUT_DELTA = "tool_output_delta"  # Live stdout from a running tool
    TOOL_RESULT = "tool_result"        # Skill execution finished
    PROGRESS = "progress"              # Execution progress indicator
    THINKING = "thinking"              # LLM is being called (pre-response indicator)
    TITLE_GENERATED = "title_generated" # Auto-generated conversation title
    MAX_STEPS_REACHED = "max_steps_reached"
    ERROR = "error"                    # Error occurred


@dataclass
class AgentEvent:
    type: AgentEventType
    data: Any = None


# ──────────────────────────────────────────────────────────────────────
# Legacy MemoryStep introspection stubs (kept for backward compat)
# ──────────────────────────────────────────────────────────────────────

def _extract_code(step: Any) -> str:
    """Legacy stub — returns code from an AgentStep or empty string."""
    return getattr(step, "code", "") or ""


def _extract_observations(step: Any) -> str:
    """Legacy stub — returns output from an AgentStep or empty string."""
    return getattr(step, "output", "") or ""


def _extract_error(step: Any) -> Optional[str]:
    """Legacy stub — returns error from an AgentStep or None."""
    return getattr(step, "error", None)


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


# ──────────────────────────────────────────────────────────────────────
# Event → JSON-RPC translator
# ──────────────────────────────────────────────────────────────────────

class EventTranslator:
    """Pure mapping from AgentEvent to (JSON-RPC method, params dict).

    The wire contract is:
      AgentEventType.STREAM_DELTA        → "chat.stream_delta"      {"content": <text>}
      AgentEventType.STREAM_END          → "chat.stream_end"        {}
      AgentEventType.CODE_BLOCK          → "chat.code_block"        <dict payload>
      AgentEventType.CODE_RESULT         → "chat.code_result"       <dict payload>
      AgentEventType.TOOL_START          → "chat.tool_start"        <dict payload>
      AgentEventType.TOOL_RESULT         → "chat.tool_result"       <dict payload>
      AgentEventType.MAX_STEPS_REACHED   → "chat.max_steps_reached" <dict payload>
      AgentEventType.ERROR               → "chat.error"             {"error": <text>}
    """

    METHOD_PREFIX: str = "chat."

    METHOD_SUFFIX: dict = {
        AgentEventType.STREAM_DELTA:       "stream_delta",
        AgentEventType.STREAM_END:         "stream_end",
        AgentEventType.REASONING_DELTA:    "reasoning_delta",
        AgentEventType.REASONING_END:      "reasoning_end",
        AgentEventType.REASONING_PROMOTE:  "reasoning_promote",
        AgentEventType.CODE_BLOCK:         "code_block",
        AgentEventType.CODE_BLOCK_START:   "code_block_start",
        AgentEventType.CODE_DELTA:         "code_delta",
        AgentEventType.CODE_BLOCK_END:     "code_block_end",
        AgentEventType.CODE_RESULT:        "code_result",
        AgentEventType.TOOL_START:         "tool_start",
        AgentEventType.TOOL_OUTPUT_DELTA:  "tool_output_delta",
        AgentEventType.TOOL_RESULT:        "tool_result",
        AgentEventType.PROGRESS:           "progress",
        AgentEventType.THINKING:           "thinking",
        AgentEventType.TITLE_GENERATED:    "title_generated",
        AgentEventType.MAX_STEPS_REACHED:  "max_steps_reached",
        AgentEventType.ERROR:              "error",
    }

    @classmethod
    def method_for(cls, ev_type: AgentEventType) -> str:
        """Return the canonical JSON-RPC method name for an event type."""
        suffix = cls.METHOD_SUFFIX.get(ev_type)
        if suffix is None:
            logger.warning(
                "EventTranslator: no method mapping for %r, falling back to chat.unknown",
                ev_type,
            )
            return cls.METHOD_PREFIX + "unknown"
        return cls.METHOD_PREFIX + suffix

    @classmethod
    def translate(cls, event: AgentEvent) -> Tuple[str, dict]:
        """Return ``(method, params)`` for the given event."""
        method = cls.method_for(event.type)
        data = event.data

        if event.type == AgentEventType.STREAM_DELTA:
            return method, {"content": data if data is not None else ""}

        if event.type == AgentEventType.STREAM_END:
            return method, {}

        if event.type == AgentEventType.REASONING_DELTA:
            if isinstance(data, dict):
                return method, data
            return method, {"delta": data if data is not None else ""}

        if event.type == AgentEventType.REASONING_END:
            if isinstance(data, dict):
                return method, data
            return method, {}

        if event.type == AgentEventType.REASONING_PROMOTE:
            if isinstance(data, dict):
                return method, data
            return method, {}

        if event.type == AgentEventType.ERROR:
            return method, {"error": data}

        if event.type == AgentEventType.PROGRESS:
            if isinstance(data, dict):
                return method, data
            return method, {"stage": data or "processing"}

        if event.type == AgentEventType.THINKING:
            if isinstance(data, dict):
                return method, data
            return method, {"stage": data or "thinking", "message": ""}

        if event.type == AgentEventType.TITLE_GENERATED:
            if isinstance(data, dict):
                return method, data
            return method, {"title": data or ""}

        if event.type == AgentEventType.CODE_BLOCK:
            if isinstance(data, dict):
                return method, data
            return method, {"code": data}

        if event.type == AgentEventType.CODE_BLOCK_START:
            if isinstance(data, dict):
                return method, data
            return method, {}

        if event.type == AgentEventType.CODE_DELTA:
            if isinstance(data, dict):
                return method, data
            return method, {"delta": data if data is not None else ""}

        if event.type == AgentEventType.CODE_BLOCK_END:
            if isinstance(data, dict):
                return method, data
            return method, {}

        if event.type == AgentEventType.CODE_RESULT:
            if isinstance(data, dict):
                return method, data
            return method, {"output": data}

        # TOOL_START, TOOL_RESULT, MAX_STEPS_REACHED, etc.
        if isinstance(data, dict):
            return method, data
        if data is None:
            return method, {}
        return method, {"data": data}


__all__ = [
    "AgentEvent",
    "AgentEventType",
    "EventTranslator",
    "_enqueue",
    "_extract_code",
    "_extract_error",
    "_extract_observations",
]
