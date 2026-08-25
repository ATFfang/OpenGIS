"""Post-run evolution candidate extraction.

This layer converts completed runs into self-evolution candidates without
touching the live agent loop. It is intentionally conservative: only verified
failures and clearly reusable procedures are turned into candidates.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from opengis_backend.agent.context.failure_memory import FailureMemoryExtractor
from opengis_backend.runs.archive import RunArchive
from opengis_backend.workspace.evolution_store import EvolutionCandidate, EvolutionStore

logger = logging.getLogger(__name__)

_OPERATION_LIFECYCLE_TOOLS = {
    "create_operation",
    "copy_operation_to_workspace",
    "edit_operation",
    "promote_script_to_operation",
    "run_operation",
    "validate_operation",
}
_REUSE_INTENT_RE = re.compile(
    r"(复用|沉淀|固化|工作流|workflow|operation|recipe|skill|template|模板|pipeline|流程)",
    re.IGNORECASE,
)


class EvolutionExtractor:
    """Extract draft self-evolution candidates from a completed run."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path
        self.store = EvolutionStore(workspace_path)

    def extract_run(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
        workflow: dict[str, Any] | None = None,
    ) -> list[EvolutionCandidate]:
        if not self.workspace_path:
            return []
        candidates: list[EvolutionCandidate] = []
        candidates.extend(self._failure_candidates(user_message=user_message, final_answer=final_answer, run_archive=run_archive))
        candidates.extend(self._recipe_candidates(user_message=user_message, final_answer=final_answer, run_archive=run_archive, workflow=workflow))
        candidates.extend(self._operation_candidates(user_message=user_message, final_answer=final_answer, run_archive=run_archive))
        self.store.add_many(candidates)
        logger.info("EvolutionExtractor stored %d candidate(s) for run=%s", len(candidates), run_archive.run_id)
        return candidates

    def _failure_candidates(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
    ) -> list[EvolutionCandidate]:
        lessons = FailureMemoryExtractor().extract_run(
            user_message=user_message,
            final_answer=final_answer,
            run_archive=run_archive,
        )
        candidates: list[EvolutionCandidate] = []
        for lesson in lessons:
            tool_name = str(lesson.metadata.get("tool_name") or lesson.title.split(":", 1)[0] or "tool")
            target_id = str(lesson.metadata.get("target_id") or "")
            repair_tools = [str(item) for item in (lesson.metadata.get("repair_tools") or []) if str(item).strip()]
            summary = _first_sentence(lesson.content) or f"Repair {tool_name} failures"
            proposed_change = "Repair the target contract in place and rerun the same path before choosing any alternate path."
            if repair_tools:
                proposed_change = " -> ".join(repair_tools + [tool_name]) + ": keep the repair path on the same target."
            candidates.append(
                EvolutionCandidate.create(
                    kind="failure_repair",
                    scope="tool_failure",
                    title=f"{tool_name} repair candidate",
                    summary=summary,
                    proposed_change=proposed_change,
                    target_component=tool_name,
                    evidence=[lesson.content[:800]],
                    tags=["failure", "repair", tool_name, target_id] + list(lesson.tags[:3]),
                    source_run_id=lesson.source_run_id or run_archive.run_id,
                    confidence=min(0.95, max(0.7, lesson.confidence)),
                    metadata={
                        "fingerprint": lesson.metadata.get("fingerprint"),
                        "target_id": target_id,
                        "verified_by_tool": lesson.metadata.get("verified_by_tool"),
                        "repair_tools": repair_tools,
                    },
                )
            )
        return candidates

    def _recipe_candidates(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
        workflow: dict[str, Any] | None,
    ) -> list[EvolutionCandidate]:
        tool_calls = run_archive.read_tool_calls()
        artifacts = run_archive.read_artifacts()
        reuse_requested = _looks_reuse_requested(user_message, final_answer)
        if not workflow and not artifacts and not reuse_requested:
            return []
        if len(tool_calls) < 3 and not workflow:
            return []
        tool_names = [
            str(call.get("name") or "")
            for call in tool_calls
            if call.get("name") and str(call.get("status") or "completed") not in {"running", "started"}
        ]
        if len(tool_names) < 3 and not workflow:
            return []
        title = "Reusable procedure candidate"
        summary = _first_sentence(final_answer) or user_message.strip()[:240]
        evidence: list[str] = []
        if tool_names:
            evidence.append("Tool sequence: " + " -> ".join(tool_names[:20]))
        if workflow:
            evidence.append(f"Workflow: {workflow.get('name', 'workflow')}")
        if artifacts:
            evidence.append("Artifacts: " + ", ".join(str(item.get("path") or item.get("title") or "") for item in artifacts[:8]))
        if final_answer:
            evidence.append("Outcome: " + final_answer.strip()[:400])
        target_component = "workflow" if workflow else "operation"
        return [
            EvolutionCandidate.create(
                kind="recipe",
                scope="procedure",
                title=title,
                summary=summary,
                proposed_change="Promote this stable procedure into a reusable recipe and keep its input contract explicit.",
                target_component=target_component,
                evidence=evidence,
                tags=["recipe", "procedure", target_component],
                source_run_id=run_archive.run_id,
                confidence=0.72 if workflow or artifacts else 0.64,
                metadata={
                    "tool_count": len(tool_calls),
                    "tool_names": tool_names[:20],
                    "workflow": workflow.get("name") if workflow else None,
                    "artifact_count": len(artifacts),
                    "reuse_requested": reuse_requested,
                },
            )
        ]

    def _operation_candidates(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
    ) -> list[EvolutionCandidate]:
        tool_calls = run_archive.read_tool_calls()
        operation_calls = [
            call for call in tool_calls
            if str(call.get("name") or "") in _OPERATION_LIFECYCLE_TOOLS
        ]
        if not operation_calls:
            return []
        operation_ids: list[str] = []
        for call in operation_calls:
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            op_id = str(args.get("operation_id") or args.get("id") or "")
            if op_id and op_id not in operation_ids:
                operation_ids.append(op_id)
        title = "Operation candidate"
        summary = _first_sentence(final_answer) or user_message.strip()[:240]
        evidence = [
            "Operation calls: " + " -> ".join(str(call.get("name") or "") for call in operation_calls[:20]),
        ]
        if operation_ids:
            evidence.append("Operation ids: " + ", ".join(operation_ids[:8]))
        return [
            EvolutionCandidate.create(
                kind="operation_candidate",
                scope="operation",
                title=title,
                summary=summary,
                proposed_change="Capture this operation as a reusable capsule with a stable input contract and verified rerun path.",
                target_component="operation",
                evidence=evidence,
                tags=["operation", "candidate"] + operation_ids[:4],
                source_run_id=run_archive.run_id,
                confidence=0.72,
                metadata={
                    "operation_ids": operation_ids,
                    "operation_call_count": len(operation_calls),
                },
            )
        ]


def _first_sentence(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    for separator in ("。", ".", "!", "？", "?"):
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip()
    return cleaned[:240]


def _looks_reuse_requested(*texts: str) -> bool:
    return any(_REUSE_INTENT_RE.search(str(text or "")) for text in texts)


__all__ = ["EvolutionExtractor"]
