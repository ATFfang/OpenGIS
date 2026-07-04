"""Agent session tree model.

A mainstream agent needs an explicit session model: user turns, workflow
nodes, and subagents should all be addressable units. This module is a small
data model that current loops can adopt incrementally without changing the
wire protocol yet.
"""

from __future__ import annotations

import time
import uuid
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionKind(str, Enum):
    CHAT = "chat"
    WORKFLOW = "workflow"
    WORKFLOW_NODE = "workflow_node"
    SUBAGENT = "subagent"
    SYSTEM = "system"


class SessionStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class InboxStatus(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class AgentInboxItem:
    """Durable admission record for one user prompt."""

    id: str
    prompt: str
    conversation_id: str | None = None
    profile_name: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    status: InboxStatus = InboxStatus.ACCEPTED
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        prompt: str,
        conversation_id: str | None = None,
        profile_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "AgentInboxItem":
        return cls(
            id=uuid.uuid4().hex,
            prompt=prompt,
            conversation_id=conversation_id,
            profile_name=profile_name,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "conversation_id": self.conversation_id,
            "profile_name": self.profile_name,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class AgentSession:
    """One node in the agent session/run tree."""

    id: str
    kind: SessionKind
    profile_name: str
    parent_id: str | None = None
    run_id: str | None = None
    title: str = ""
    status: SessionStatus = SessionStatus.RUNNING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    children: list[str] = field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        kind: SessionKind,
        profile_name: str,
        parent_id: str | None = None,
        run_id: str | None = None,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "AgentSession":
        return cls(
            id=uuid.uuid4().hex,
            kind=kind,
            profile_name=profile_name,
            parent_id=parent_id,
            run_id=run_id,
            title=title,
            metadata=metadata or {},
        )

    def finish(
        self,
        *,
        status: SessionStatus,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.summary = summary
        self.updated_at = time.time()
        if metadata:
            self.metadata.update(metadata)

    def add_child(self, child: "AgentSession") -> None:
        if child.id not in self.children:
            self.children.append(child.id)
        child.parent_id = self.id
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "profile_name": self.profile_name,
            "parent_id": self.parent_id,
            "run_id": self.run_id,
            "title": self.title,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "children": list(self.children),
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }


class SessionStore:
    """Workspace-level session tree persistence."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def path(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "sessions.json"

    def upsert(self, session: AgentSession) -> None:
        path = self.path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._load_raw(path)
            sessions = data.setdefault("sessions", {})
            sessions[session.id] = session.to_dict()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("session store upsert failed (non-fatal)", exc_info=True)

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        path = self.path
        if path is None or not path.exists():
            return []
        data = self._load_raw(path)
        sessions = list((data.get("sessions") or {}).values())
        sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return sessions[: max(1, limit)]

    def add_inbox(self, item: AgentInboxItem) -> None:
        path = self.path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._load_raw(path)
            inbox = data.setdefault("inbox", {})
            inbox[item.id] = item.to_dict()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("session inbox add failed (non-fatal)", exc_info=True)

    def update_inbox(
        self,
        item_id: str,
        *,
        status: InboxStatus | str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        path = self.path
        if path is None:
            return
        try:
            data = self._load_raw(path)
            inbox = data.setdefault("inbox", {})
            raw = inbox.get(item_id)
            if not isinstance(raw, dict):
                return
            if status is not None:
                raw["status"] = status.value if isinstance(status, InboxStatus) else str(status)
            if session_id is not None:
                raw["session_id"] = session_id
            if run_id is not None:
                raw["run_id"] = run_id
            if error is not None:
                raw["error"] = error
            if metadata:
                current = raw.get("metadata")
                if not isinstance(current, dict):
                    current = {}
                current.update(metadata)
                raw["metadata"] = current
            raw["updated_at"] = time.time()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("session inbox update failed (non-fatal)", exc_info=True)

    def list_inbox(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        path = self.path
        if path is None or not path.exists():
            return []
        data = self._load_raw(path)
        items = list((data.get("inbox") or {}).values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return items[: max(1, limit)]

    def get_inbox(self, item_id: str) -> dict[str, Any] | None:
        path = self.path
        if path is None or not path.exists():
            return None
        data = self._load_raw(path)
        raw = (data.get("inbox") or {}).get(item_id)
        return dict(raw) if isinstance(raw, dict) else None

    def find_inbox_by_queue_id(self, queue_id: str) -> dict[str, Any] | None:
        for item in self.list_inbox(limit=10000):
            metadata = item.get("metadata")
            if isinstance(metadata, dict) and metadata.get("queue_id") == queue_id:
                return item
        return None

    def find_inbox_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        for item in self.list_inbox(limit=10000):
            if item.get("run_id") == run_id:
                return item
        return None

    def list_resumable_inbox(self, limit: int = 100) -> list[dict[str, Any]]:
        terminal = {
            InboxStatus.SUCCESS.value,
            InboxStatus.CANCELLED.value,
        }
        return [
            item
            for item in self.list_inbox(limit=limit)
            if item.get("status") not in terminal
        ]

    @staticmethod
    def _load_raw(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"sessions": {}, "inbox": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"sessions": {}, "inbox": {}}
            data.setdefault("sessions", {})
            data.setdefault("inbox", {})
            return data
        except Exception:
            return {"sessions": {}, "inbox": {}}


__all__ = [
    "AgentSession",
    "AgentInboxItem",
    "InboxStatus",
    "SessionStore",
    "SessionKind",
    "SessionStatus",
]
