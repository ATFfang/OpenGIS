"""Agent queue primitives.

The current UI still waits for ``chat.user_message`` to finish, but the
runtime needs an explicit queue boundary so prompt admission, execution,
retry, and resume are not fused into one RPC handler.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opengis_backend.agent.session import AgentInboxItem
from opengis_backend.agent.workflow_loop import WorkflowDocument
from opengis_backend.agent.workflow_store import WorkflowDocumentStore


class AgentQueueStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class AgentQueueItem:
    id: str
    inbox: AgentInboxItem
    message: str
    workspace_path: str | None = None
    workflow: WorkflowDocument | None = None
    skill_groups: list[str] | None = None
    user_instructions: str | None = None
    profile_name: str | None = None
    conversation_id: str | None = None
    status: AgentQueueStatus = AgentQueueStatus.QUEUED
    run_id: str | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        inbox: AgentInboxItem,
        message: str,
        workspace_path: str | None = None,
        workflow: WorkflowDocument | None = None,
        skill_groups: list[str] | None = None,
        user_instructions: str | None = None,
        profile_name: str | None = None,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "AgentQueueItem":
        return cls(
            id=uuid.uuid4().hex,
            inbox=inbox,
            message=message,
            workspace_path=workspace_path,
            workflow=workflow,
            skill_groups=skill_groups,
            user_instructions=user_instructions,
            profile_name=profile_name,
            conversation_id=conversation_id,
            metadata=metadata or {},
        )

    def mark(
        self,
        status: AgentQueueStatus,
        *,
        run_id: str | None = None,
        error: str = "",
    ) -> None:
        self.status = status
        if run_id is not None:
            self.run_id = run_id
        if error:
            self.error = error
        self.updated_at = time.time()

    def reset_for_retry(self) -> None:
        self.status = AgentQueueStatus.QUEUED
        self.error = ""
        self.updated_at = time.time()

    def cancel(self, *, error: str = "cancelled") -> None:
        self.mark(AgentQueueStatus.CANCELLED, error=error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "inbox_id": self.inbox.id,
            "status": self.status.value,
            "run_id": self.run_id,
            "workspace_path": self.workspace_path,
            "profile_name": self.profile_name,
            "conversation_id": self.conversation_id,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


class AgentQueue:
    """In-process queue registry for observable agent execution items."""

    def __init__(self, *, max_history: int = 200) -> None:
        self.max_history = max(10, int(max_history))
        self._items: dict[str, AgentQueueItem] = {}
        self._order: list[str] = []

    def enqueue(self, item: AgentQueueItem) -> AgentQueueItem:
        self._items[item.id] = item
        self._order.append(item.id)
        self._trim()
        return item

    def get(self, item_id: str) -> AgentQueueItem | None:
        return self._items.get(item_id)

    def get_by_inbox_id(self, inbox_id: str) -> AgentQueueItem | None:
        for item in self._items.values():
            if item.inbox.id == inbox_id:
                return item
        return None

    def cancel(self, item_id: str, *, error: str = "cancelled") -> AgentQueueItem | None:
        item = self.get(item_id)
        if item is None:
            return None
        item.cancel(error=error)
        return item

    def retry(self, item_id: str) -> AgentQueueItem | None:
        item = self.get(item_id)
        if item is None:
            return None
        item.reset_for_retry()
        return item

    def ensure_from_inbox(self, raw: dict[str, Any]) -> AgentQueueItem:
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        queue_id = metadata.get("queue_id")
        if isinstance(queue_id, str):
            existing = self.get(queue_id)
            if existing is not None:
                return existing
        existing = self.get_by_inbox_id(str(raw.get("id") or ""))
        if existing is not None:
            return existing
        inbox = AgentInboxItem(
            id=str(raw.get("id") or uuid.uuid4().hex),
            prompt=str(raw.get("prompt") or ""),
            conversation_id=raw.get("conversation_id"),
            profile_name=raw.get("profile_name"),
            session_id=raw.get("session_id"),
            run_id=raw.get("run_id"),
            error=str(raw.get("error") or ""),
            metadata=metadata,
        )
        item = AgentQueueItem.create(
            inbox=inbox,
            message=inbox.prompt,
            workspace_path=metadata.get("workspace_path"),
            workflow=WorkflowDocumentStore(metadata.get("workspace_path")).load(
                workflow_id=metadata.get("workflow_id"),
                workflow_path=metadata.get("workflow_path"),
            ),
            skill_groups=metadata.get("skill_groups") if isinstance(metadata.get("skill_groups"), list) else None,
            profile_name=inbox.profile_name,
            conversation_id=inbox.conversation_id,
            metadata={**metadata, "restored_from_inbox": True},
        )
        item.id = str(queue_id) if isinstance(queue_id, str) and queue_id else item.id
        item.run_id = inbox.run_id
        if raw.get("status") == "running":
            item.status = AgentQueueStatus.ERROR
            item.error = "Recovered from an interrupted running state."
        elif raw.get("status") == "cancelled":
            item.status = AgentQueueStatus.CANCELLED
        elif raw.get("status") == "success":
            item.status = AgentQueueStatus.SUCCESS
        else:
            item.status = AgentQueueStatus.QUEUED
        self.enqueue(item)
        return item

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item_id in reversed(self._order):
            item = self._items.get(item_id)
            if item is None:
                continue
            if status and item.status.value != status:
                continue
            out.append(item.to_dict())
            if len(out) >= max(1, limit):
                break
        return out

    def next_queued(self, *, workspace_path: str | None = None) -> AgentQueueItem | None:
        for item_id in self._order:
            item = self._items.get(item_id)
            if item is None or item.status != AgentQueueStatus.QUEUED:
                continue
            if workspace_path is not None and item.workspace_path != workspace_path:
                continue
            return item
        return None

    def _trim(self) -> None:
        terminal = {
            AgentQueueStatus.SUCCESS,
            AgentQueueStatus.ERROR,
            AgentQueueStatus.CANCELLED,
        }
        while len(self._order) > self.max_history:
            old = self._order.pop(0)
            item = self._items.get(old)
            if item is not None and item.status not in terminal:
                self._order.insert(0, old)
                break
            self._items.pop(old, None)


__all__ = ["AgentQueue", "AgentQueueItem", "AgentQueueStatus"]
