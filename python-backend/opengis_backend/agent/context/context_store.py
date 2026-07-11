"""Conversation context cache, persistence, and post-run knowledge capture."""

from __future__ import annotations

import logging
from typing import Any

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.context.context_persistence import load_context, save_context
from opengis_backend.agent.context.knowledge_extractor import KnowledgeExtractor
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.runs import RunArchive


logger = logging.getLogger(__name__)


class AgentContextStore:
    """Owns per-conversation context lookup and persistence."""

    def __init__(self) -> None:
        self._contexts: dict[str, ContextManager] = {}

    def get(self, conversation_id: str | None, workspace: str | None) -> ContextManager:
        if conversation_id and conversation_id in self._contexts:
            context = self._contexts[conversation_id]
            logger.info(
                "Reusing context for conversation %s (%d messages)",
                conversation_id,
                context.total_messages,
            )
            return context

        logger.info(
            "Context lookup: conversation_id=%s, workspace=%s",
            conversation_id,
            workspace,
        )
        context = None
        if conversation_id and workspace:
            context = load_context(workspace, conversation_id)
            if context is not None:
                logger.info(
                    "Restored context from disk for conversation %s (%d messages)",
                    conversation_id,
                    context.total_messages,
                )
        if context is None:
            context = ContextManager()
            logger.info("Created new context for conversation %s", conversation_id)
        if conversation_id:
            self._contexts[conversation_id] = context
        return context

    def persist(self, workspace: str | None, conversation_id: str | None, context: ContextManager) -> None:
        if not workspace or not conversation_id:
            return
        try:
            save_context(workspace, conversation_id, context)
        except Exception:
            logger.exception("context persistence failed")

    def extract_knowledge_after_run(
        self,
        *,
        workspace: str | None,
        user_message: str,
        final_answer: Any,
        run_archive: RunArchive,
        workflow: WorkflowDocument | None,
    ) -> None:
        if not workspace or not final_answer:
            return
        try:
            KnowledgeExtractor(workspace).extract_run(
                user_message=user_message,
                final_answer=final_answer,
                run_archive=run_archive,
                workflow=workflow.to_dict() if workflow is not None else None,
            )
        except Exception:
            logger.debug("knowledge extraction failed (non-fatal)", exc_info=True)


__all__ = ["AgentContextStore"]
