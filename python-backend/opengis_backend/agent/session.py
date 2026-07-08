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
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STALE_RUNNING_SECONDS = 12 * 60 * 60


def _iso_created_at_is_stale(raw: Any, *, max_age_seconds: float) -> bool:
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > max_age_seconds
    except Exception:
        return False


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
        self.reconcile_stale_running()
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
        self.reconcile_stale_running()
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
        self.reconcile_stale_running()
        terminal = {
            InboxStatus.SUCCESS.value,
            InboxStatus.CANCELLED.value,
            InboxStatus.ERROR.value,
        }
        return [
            item
            for item in self.list_inbox(limit=limit)
            if item.get("status") not in terminal
        ]

    def reconcile_stale_running(self, *, max_age_seconds: float = STALE_RUNNING_SECONDS) -> int:
        """Recover persisted running records that cannot still be active.

        Active in-process tasks live only in the current backend process. After
        a restart or crash, durable ``running`` records with terminal parents,
        terminal run archives, or very old timestamps are stale UI state and
        should not keep the control panel spinning forever.
        """
        path = self.path
        if path is None or not path.exists():
            return 0
        data = self._load_raw(path)
        sessions = data.setdefault("sessions", {})
        inbox = data.setdefault("inbox", {})
        now = time.time()
        changed = 0

        if isinstance(sessions, dict):
            for raw in sessions.values():
                if not isinstance(raw, dict) or raw.get("status") != SessionStatus.RUNNING.value:
                    continue
                status, reason = self._recovered_session_status(raw, sessions, now, max_age_seconds)
                if not status:
                    continue
                raw["status"] = status
                raw["summary"] = str(raw.get("summary") or reason)
                raw["updated_at"] = now
                metadata = raw.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["recovered_from_running"] = True
                metadata["recovery_reason"] = reason
                raw["metadata"] = metadata
                changed += 1

        if isinstance(inbox, dict):
            for raw in inbox.values():
                if not isinstance(raw, dict) or raw.get("status") != InboxStatus.RUNNING.value:
                    continue
                status, reason = self._recovered_inbox_status(raw, now, max_age_seconds)
                if not status:
                    continue
                raw["status"] = status
                raw["error"] = str(raw.get("error") or reason)
                raw["updated_at"] = now
                metadata = raw.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["recovered_from_running"] = True
                metadata["recovery_reason"] = reason
                raw["metadata"] = metadata
                changed += 1

        if changed:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                logger.debug("session stale-running reconciliation failed", exc_info=True)
        return changed

    def _recovered_session_status(
        self,
        raw: dict[str, Any],
        sessions: dict[str, Any],
        now: float,
        max_age_seconds: float,
    ) -> tuple[str | None, str]:
        parent_id = raw.get("parent_id")
        if isinstance(parent_id, str) and parent_id:
            parent = sessions.get(parent_id)
            if isinstance(parent, dict) and parent.get("status") in {
                SessionStatus.SUCCESS.value,
                SessionStatus.ERROR.value,
                SessionStatus.CANCELLED.value,
            }:
                return (
                    SessionStatus.ERROR.value,
                    f"Recovered stale running session: parent session is {parent.get('status')}.",
                )

        run_status, run_reason = self._status_from_run_meta(raw.get("run_id"), now, max_age_seconds)
        if run_status:
            return run_status, run_reason

        updated_at = raw.get("updated_at") or raw.get("created_at")
        if isinstance(updated_at, (int, float)) and now - float(updated_at) > max_age_seconds:
            return (
                SessionStatus.ERROR.value,
                "Recovered stale running session: no active backend runner after restart.",
            )
        return None, ""

    def _recovered_inbox_status(
        self,
        raw: dict[str, Any],
        now: float,
        max_age_seconds: float,
    ) -> tuple[str | None, str]:
        run_status, run_reason = self._status_from_run_meta(raw.get("run_id"), now, max_age_seconds)
        if run_status:
            inbox_status = {
                SessionStatus.SUCCESS.value: InboxStatus.SUCCESS.value,
                SessionStatus.ERROR.value: InboxStatus.ERROR.value,
                SessionStatus.CANCELLED.value: InboxStatus.CANCELLED.value,
            }.get(run_status, InboxStatus.ERROR.value)
            return inbox_status, run_reason

        updated_at = raw.get("updated_at") or raw.get("created_at")
        if isinstance(updated_at, (int, float)) and now - float(updated_at) > max_age_seconds:
            return (
                InboxStatus.ERROR.value,
                "Recovered stale running inbox item: no active backend runner after restart.",
            )
        return None, ""

    def _status_from_run_meta(
        self,
        run_id: Any,
        now: float,
        max_age_seconds: float,
    ) -> tuple[str | None, str]:
        if not isinstance(run_id, str) or not run_id:
            return None, ""
        meta_path = self._run_meta_path(run_id)
        if meta_path is None or not meta_path.exists():
            return None, ""
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None, ""
        status = str(meta.get("status") or "")
        if status in {"success", "completed"}:
            return SessionStatus.SUCCESS.value, f"Recovered running session from terminal run status: {status}."
        if status == "cancelled":
            return SessionStatus.CANCELLED.value, "Recovered running session from cancelled run status."
        if status == "error":
            return SessionStatus.ERROR.value, "Recovered running session from errored run status."
        if status == "running" and meta.get("finished_at"):
            return SessionStatus.ERROR.value, "Recovered running session: run archive has finished_at but remained running."
        created_at = meta.get("created_at")
        if status == "running" and _iso_created_at_is_stale(created_at, max_age_seconds=max_age_seconds):
            return SessionStatus.ERROR.value, "Recovered stale running session: run archive is stale."
        return None, ""

    def _run_meta_path(self, run_id: str) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "runs" / run_id / "meta.json"

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
