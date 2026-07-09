"""Per-run agent session lifecycle helpers."""

from __future__ import annotations

import logging
from typing import Any

from opengis_backend.agent.governance.profile import AgentProfile
from opengis_backend.agent.session.session import (
    AgentSession,
    SessionKind,
    SessionStatus,
    SessionStore,
)
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.runs import RunArchive
from opengis_backend.tools.context import ToolContext


logger = logging.getLogger(__name__)


def create_run_session(
    *,
    ctx: ToolContext,
    workspace: str | None,
    conversation_id: str | None,
    workflow: WorkflowDocument | None,
    agent_profile: AgentProfile,
    run_id: str,
    title: str,
) -> AgentSession:
    session = AgentSession.create(
        kind=SessionKind.WORKFLOW if workflow is not None else SessionKind.CHAT,
        profile_name=agent_profile.name,
        run_id=run_id,
        title=title[:80],
        metadata={
            "conversation_id": conversation_id,
            "workspace_path": workspace,
            "inbox_id": (ctx.meta or {}).get("_inbox_id"),
        },
    )
    if ctx.meta is not None:
        ctx.meta.setdefault("_agent_session", session)
    persist_run_session(workspace, session, reason="initial")
    return session


def persist_run_session(
    workspace: str | None,
    session: AgentSession,
    *,
    reason: str,
) -> None:
    if not workspace:
        return
    try:
        SessionStore(workspace).upsert(session)
    except Exception:
        logger.debug("%s session persistence failed (non-fatal)", reason, exc_info=True)


def finish_run_session(
    *,
    ctx: ToolContext,
    workspace: str | None,
    session: AgentSession,
    run_archive: RunArchive,
    status: str,
    final_answer: Any,
    error: Any,
) -> None:
    try:
        status_map = {
            "success": SessionStatus.SUCCESS,
            "error": SessionStatus.ERROR,
            "cancelled": SessionStatus.CANCELLED,
        }
        session.finish(
            status=status_map.get(status, SessionStatus.ERROR),
            summary=final_answer or error or "",
        )
        if ctx.meta is not None:
            ctx.meta["_agent_session"] = session
        run_archive.record_session(session.to_dict())
        persist_run_session(workspace, session, reason="final")
    except Exception:
        logger.debug("session finalization failed (non-fatal)", exc_info=True)


__all__ = ["create_run_session", "finish_run_session", "persist_run_session"]
