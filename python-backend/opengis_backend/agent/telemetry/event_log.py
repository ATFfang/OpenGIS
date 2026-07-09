"""Event-sourced run log and message part projection."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType


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
        id: str | None = None,
        status: str = "completed",
        text: str = "",
        tool: str = "",
        call_id: str = "",
        run_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> "MessagePart":
        return cls(
            id=id or uuid.uuid4().hex,
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
    prefix = run_id or "run"
    if event.type == AgentEventType.STREAM_DELTA:
        text = str(data.get("content") or "") if isinstance(event.data, dict) else str(event.data or "")
        return MessagePart.create(
            id=f"{prefix}:text:final",
            type="text",
            status="streaming",
            text=text,
            run_id=run_id,
        )
    if event.type == AgentEventType.CODE_BLOCK_START:
        step = data.get("step", "unknown")
        return MessagePart.create(id=f"{prefix}:code:{step}", type="code", status="running", run_id=run_id, data=data)
    if event.type == AgentEventType.CODE_DELTA:
        step = data.get("step", "unknown")
        return MessagePart.create(
            id=f"{prefix}:code:{step}",
            type="code",
            status="streaming",
            text=str(data.get("delta") or ""),
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.CODE_BLOCK_END:
        step = data.get("step", "unknown")
        return MessagePart.create(id=f"{prefix}:code:{step}", type="code", status="completed", run_id=run_id, data=data)
    if event.type == AgentEventType.CODE_BLOCK:
        step = data.get("step", "unknown")
        return MessagePart.create(
            id=f"{prefix}:code:{step}",
            type="code",
            status="completed",
            text=str(data.get("code") or ""),
            run_id=run_id,
            data={
                **data,
                "stepNumber": data.get("step"),
                "scriptPath": data.get("script_path"),
                "scriptAbsPath": data.get("script_abs_path"),
            },
        )
    if event.type == AgentEventType.CODE_RESULT:
        step = data.get("step", "unknown")
        return MessagePart.create(
            id=f"{prefix}:code-result:{step}",
            type="tool_output",
            status="failed" if data.get("error") else "completed",
            text=str(data.get("output") or ""),
            tool="execute_code",
            run_id=run_id,
            data={
                **data,
                "stepNumber": data.get("step"),
                "durationMs": data.get("duration_ms"),
            },
        )
    if event.type == AgentEventType.TOOL_START:
        call_id = str(data.get("call_id") or "unknown")
        return MessagePart.create(
            id=f"{prefix}:tool:{call_id}",
            type="tool",
            status="running",
            tool=str(data.get("name") or ""),
            call_id=call_id,
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.TOOL_OUTPUT_DELTA:
        call_id = str(data.get("call_id") or "unknown")
        return MessagePart.create(
            id=f"{prefix}:tool-output:{call_id}",
            type="tool_output",
            status="streaming",
            text=str(data.get("delta") or ""),
            tool=str(data.get("name") or ""),
            call_id=call_id,
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.TOOL_RESULT:
        call_id = str(data.get("call_id") or "unknown")
        return MessagePart.create(
            id=f"{prefix}:tool:{call_id}",
            type="tool",
            status="failed" if data.get("error") else "completed",
            tool=str(data.get("name") or ""),
            call_id=call_id,
            run_id=run_id,
            data=data,
        )
    if event.type == AgentEventType.PROGRESS:
        return MessagePart.create(id=f"{prefix}:progress:live", type="progress", status="running", run_id=run_id, data=data)
    if event.type == AgentEventType.ERROR:
        text = str(data.get("error") or "") if isinstance(event.data, dict) else str(event.data or "")
        return MessagePart.create(id=f"{prefix}:error", type="error", status="failed", text=text, run_id=run_id, data=data)
    if event.type == AgentEventType.STREAM_END:
        turn_data = dict(data)
        turn_data["kind"] = "stream_end"
        return MessagePart.create(id=f"{prefix}:turn:end", type="turn", status="completed", run_id=run_id, data=turn_data)
    return None


class AgentEventLog:
    """Append-only event log with MessagePart projection.

    Runs persist the raw event stream plus render-ready ``MessagePart`` rows.
    Live websocket delivery uses the same MessagePart projection.
    """

    def __init__(self, archive: Any, *, run_id: str) -> None:
        self.archive = archive
        self.run_id = run_id

    def append(self, event: AgentEvent) -> MessagePart | None:
        event_type = event.type.value if isinstance(event.type, AgentEventType) else str(event.type)
        data = self._with_run_id(event.data)
        self.archive.record_event(event_type, data)
        projected = event_to_message_part(AgentEvent(type=event.type, data=data))
        if projected is None:
            return None
        payload = projected.to_dict()
        payload.setdefault("run_id", self.run_id)
        if not payload.get("run_id"):
            payload["run_id"] = self.run_id
        self.archive.record_message_part(payload)
        return projected

    def _with_run_id(self, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("run_id"):
                return data
            updated = dict(data)
            updated["run_id"] = self.run_id
            return updated
        return data


__all__ = ["AgentEventLog", "MessagePart", "event_to_message_part"]
