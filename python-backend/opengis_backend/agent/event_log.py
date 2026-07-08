"""Event-sourced run log and message part projection."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from opengis_backend.agent.events import AgentEvent, AgentEventType


@dataclass(frozen=True)
class MessagePart:
    id: str
    type: str
    status: str = "completed"
    text: str = ""
    tool: str = ""
    call_id: str = ""
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        type: str,
        status: str = "completed",
        text: str = "",
        tool: str = "",
        call_id: str = "",
        run_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> "MessagePart":
        return cls(
            id=uuid.uuid4().hex,
            type=type,
            status=status,
            text=text,
            tool=tool,
            call_id=call_id,
            run_id=run_id,
            data=data or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "text": self.text,
            "tool": self.tool,
            "call_id": self.call_id,
            "run_id": self.run_id,
            "data": dict(self.data),
            "created_at": self.created_at,
        }


def event_to_message_part(event: AgentEvent) -> MessagePart | None:
    data = event.data if isinstance(event.data, dict) else {}
    run_id = str(data.get("run_id") or "")
    if event.type == AgentEventType.STREAM_DELTA:
        text = event.data if isinstance(event.data, str) else str(event.data or "")
        return MessagePart.create(type="text", status="streaming", text=text, run_id=run_id)
    if event.type == AgentEventType.REASONING_DELTA:
        return MessagePart.create(
            type="reasoning",
            status="streaming" if data.get("open") or data.get("delta") else "running",
            text=str(data.get("delta") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.CODE_BLOCK_START:
        return MessagePart.create(type="code", status="running", run_id=run_id, data=data)
    if event.type == AgentEventType.CODE_DELTA:
        return MessagePart.create(
            type="code",
            status="streaming",
            text=str(data.get("delta") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.CODE_BLOCK_END:
        return MessagePart.create(type="code", status="completed", run_id=run_id, data=data)
    if event.type == AgentEventType.TOOL_START:
        return MessagePart.create(
            type="tool",
            status="running",
            tool=str(data.get("name") or ""),
            call_id=str(data.get("call_id") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.TOOL_OUTPUT_DELTA:
        return MessagePart.create(
            type="tool_output",
            status="streaming",
            text=str(data.get("delta") or ""),
            tool=str(data.get("name") or ""),
            call_id=str(data.get("call_id") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.TOOL_RESULT:
        return MessagePart.create(
            type="tool",
            status="failed" if data.get("error") else "completed",
            tool=str(data.get("name") or ""),
            call_id=str(data.get("call_id") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.PROGRESS:
        return MessagePart.create(type="progress", status="running", run_id=run_id, data=data)
    if event.type == AgentEventType.ERROR:
        return MessagePart.create(type="error", status="failed", text=str(event.data or ""), run_id=run_id)
    if event.type == AgentEventType.STREAM_END:
        return MessagePart.create(type="turn", status="completed", run_id=run_id, data=data)
    return None


__all__ = ["MessagePart", "event_to_message_part"]
