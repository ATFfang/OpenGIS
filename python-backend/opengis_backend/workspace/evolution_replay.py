"""Replay task-set materialization for evolution candidates.

The suite is a bridge between archived runs and future execution-based replay.
Right now it materializes structured replay cases from historical runs and
records them on disk so the evaluator can reason about a stable task set.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opengis_backend.agent.context.failure_memory import failure_signature
from opengis_backend.runs.archive import RunArchive

from .evolution_store import EvolutionCandidate

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = ".opengis/evolution"
_REPLAY_CASES_FILE = "replay_cases.jsonl"
_MAX_RECORDS = 500


@dataclass(frozen=True)
class ReplayCase:
    id: str
    candidate_id: str
    candidate_kind: str
    source_run_id: str
    prompt: str
    expected_signal: str
    status: str = "pending"
    score: float = 0.0
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        candidate_kind: str,
        source_run_id: str,
        prompt: str,
        expected_signal: str,
        status: str = "pending",
        score: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "ReplayCase":
        return cls(
            id=uuid.uuid4().hex,
            candidate_id=candidate_id,
            candidate_kind=candidate_kind,
            source_run_id=source_run_id,
            prompt=prompt.strip(),
            expected_signal=expected_signal.strip(),
            status=status.strip() or "pending",
            score=max(0.0, min(1.0, float(score))),
            notes=notes.strip(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "source_run_id": self.source_run_id,
            "prompt": self.prompt,
            "expected_signal": self.expected_signal,
            "status": self.status,
            "score": self.score,
            "notes": self.notes,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReplayCase":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            candidate_id=str(raw.get("candidate_id") or ""),
            candidate_kind=str(raw.get("candidate_kind") or "candidate"),
            source_run_id=str(raw.get("source_run_id") or ""),
            prompt=str(raw.get("prompt") or ""),
            expected_signal=str(raw.get("expected_signal") or ""),
            status=str(raw.get("status") or "pending"),
            score=float(raw.get("score") or 0.0),
            notes=str(raw.get("notes") or ""),
            created_at=float(raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class ReplaySuite:
    """Materialize replay cases from historical runs for a candidate."""

    def __init__(self, workspace_path: str | None, *, replay_limit: int = 20) -> None:
        self.workspace_path = workspace_path
        self.replay_limit = max(1, int(replay_limit))

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _EVOLUTION_DIR

    def build(self, candidate: EvolutionCandidate) -> list[ReplayCase]:
        if not self.workspace_path:
            return []
        cases: list[ReplayCase] = []
        for run_index in RunArchive.list_runs(self.workspace_path, limit=self.replay_limit):
            archive = RunArchive.load(self.workspace_path, run_index.run_id)
            if archive is None:
                continue
            case = self._materialize_case(candidate, archive)
            if case is not None:
                cases.append(case)
        self.record(cases)
        return cases

    def summarize(self, cases: list[ReplayCase]) -> dict[str, Any]:
        if not cases:
            return {
                "replay_case_count": 0,
                "replay_pass_count": 0,
                "replay_score": 0.0,
                "supporting_run_ids": [],
            }
        passed = [case for case in cases if case.score >= 0.75 and case.status != "failed"]
        score = sum(case.score for case in cases) / max(1, len(cases))
        return {
            "replay_case_count": len(cases),
            "replay_pass_count": len(passed),
            "replay_score": round(score, 4),
            "supporting_run_ids": [case.source_run_id for case in cases[:8]],
        }

    def list_cases(self, *, limit: int = 200) -> list[ReplayCase]:
        root = self.root
        if root is None or not root.exists():
            return []
        path = root / _REPLAY_CASES_FILE
        records = self._read_file(path)
        records.sort(key=lambda case: (-case.created_at, case.id))
        return records[: max(1, limit)]

    def record(self, cases: list[ReplayCase]) -> None:
        root = self.root
        if root is None or not cases:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / _REPLAY_CASES_FILE
            existing = self._read_file(path)
            by_id = {case.id: case for case in existing}
            for case in cases:
                by_id[case.id] = case
            rows = sorted(by_id.values(), key=lambda item: (-item.created_at, item.id))
            if len(rows) > _MAX_RECORDS:
                rows = rows[:_MAX_RECORDS]
            path.write_text(
                "".join(json.dumps(case.to_dict(), ensure_ascii=False, default=str) + "\n" for case in rows),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("replay case record failed", exc_info=True)

    def _materialize_case(self, candidate: EvolutionCandidate, archive: RunArchive) -> ReplayCase | None:
        prompt = str(archive._meta.get("prompt") or "").strip()
        final_answer = str(archive._meta.get("final_answer") or "").strip()
        if candidate.kind == "failure_repair":
            signature = self._failure_signature_from_archive(archive, candidate)
            if signature is None:
                return None
            expected = "same failure signature should resolve after repair"
            notes = f"failure={signature.error_type} target={signature.target_id or signature.tool_name}"
            return ReplayCase.create(
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                source_run_id=archive.run_id,
                prompt=prompt,
                expected_signal=expected,
                score=0.92 if final_answer else 0.84,
                notes=notes,
                status="materialized",
                metadata={
                    "tool_name": signature.tool_name,
                    "target_id": signature.target_id,
                    "error_type": signature.error_type,
                },
            )
        if candidate.kind == "recipe":
            tool_names = [str(item) for item in candidate.metadata.get("tool_names") or [] if str(item).strip()]
            if len(tool_names) < 2:
                return None
            archived_tools = [str(call.get("name") or "") for call in archive.read_tool_calls() if call.get("name")]
            if len(archived_tools) < 2:
                return None
            window = " -> ".join(archived_tools[: max(2, min(len(tool_names), 6))])
            if not any(name in window for name in tool_names[:4]):
                return None
            return ReplayCase.create(
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                source_run_id=archive.run_id,
                prompt=prompt,
                expected_signal="tool sequence should remain reusable and stable",
                score=min(0.9, 0.6 + 0.05 * min(len(archived_tools), 6)),
                notes=f"tool_chain={window}",
                status="materialized",
                metadata={
                    "tool_count": len(archived_tools),
                    "tool_names": archived_tools[:12],
                },
            )
        if candidate.kind == "operation_candidate":
            operation_ids = [str(item) for item in candidate.metadata.get("operation_ids") or [] if str(item).strip()]
            if not operation_ids:
                return None
            matched = self._matched_operation_ids(archive, operation_ids)
            if not matched:
                return None
            return ReplayCase.create(
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                source_run_id=archive.run_id,
                prompt=prompt,
                expected_signal="operation contract should be reusable across reruns",
                score=min(0.92, 0.68 + 0.04 * len(matched)),
                notes="matched_operation_ids=" + ",".join(matched[:8]),
                status="materialized",
                metadata={
                    "operation_ids": matched,
                    "operation_call_count": len([call for call in archive.read_tool_calls() if str(call.get("name") or "") in {
                        "create_operation",
                        "copy_operation_to_workspace",
                        "edit_operation",
                        "promote_script_to_operation",
                        "run_operation",
                        "validate_operation",
                    }]),
                },
            )
        if candidate.kind == "weakness_cluster":
            source_run_ids = {str(item) for item in candidate.metadata.get("weakness_source_run_ids") or [] if str(item)}
            cluster_key = str(candidate.metadata.get("weakness_cluster_key") or "")
            if source_run_ids and archive.run_id not in source_run_ids:
                return None
            if not cluster_key:
                return None
            evidence_hit = self._archive_matches_weakness(archive, candidate)
            if not evidence_hit:
                return None
            return ReplayCase.create(
                candidate_id=candidate.id,
                candidate_kind=candidate.kind,
                source_run_id=archive.run_id,
                prompt=prompt,
                expected_signal="targeted harness edit should reduce this correction cluster without broad regression",
                score=min(0.94, 0.72 + 0.04 * int(candidate.metadata.get("weakness_support_count") or 1)),
                notes=f"weakness_cluster={cluster_key}",
                status="materialized",
                metadata={
                    "weakness_cluster_key": cluster_key,
                    "weakness_kind": candidate.metadata.get("weakness_kind"),
                    "weakness_support_count": candidate.metadata.get("weakness_support_count"),
                },
            )
        return None

    @staticmethod
    def _failure_signature_from_archive(archive: RunArchive, candidate: EvolutionCandidate):
        target_id = str(candidate.metadata.get("target_id") or candidate.target_component or "")
        tool_name = str(candidate.target_component or "").strip()
        for call in archive.read_tool_calls():
            signature = failure_signature(call)
            if signature is None:
                continue
            if target_id and signature.target_id == target_id:
                return signature
            if tool_name and signature.tool_name == tool_name:
                return signature
        return None

    @staticmethod
    def _matched_operation_ids(archive: RunArchive, operation_ids: list[str]) -> list[str]:
        matched: list[str] = []
        for call in archive.read_tool_calls():
            if str(call.get("name") or "") not in {
                "create_operation",
                "copy_operation_to_workspace",
                "edit_operation",
                "promote_script_to_operation",
                "run_operation",
                "validate_operation",
            }:
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            op_id = str(args.get("operation_id") or args.get("id") or "")
            if op_id and op_id in operation_ids and op_id not in matched:
                matched.append(op_id)
        return matched

    @staticmethod
    def _archive_matches_weakness(archive: RunArchive, candidate: EvolutionCandidate) -> bool:
        component = str(candidate.target_component or "").strip()
        target_id = str(candidate.metadata.get("target_id") or candidate.metadata.get("weakness_cluster_key") or "")
        for call in archive.read_tool_calls():
            name = str(call.get("name") or "")
            if component and name == component:
                return True
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            if target_id and target_id in str(args):
                return True
            signature = failure_signature(call)
            if signature and component and signature.tool_name == component:
                return True
        return False

    @staticmethod
    def _read_file(path: Path) -> list[ReplayCase]:
        if not path.exists():
            return []
        out: list[ReplayCase] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    case = ReplayCase.from_dict(raw)
                    if case.candidate_id:
                        out.append(case)
        except Exception:
            logger.debug("replay case read failed: %s", path, exc_info=True)
        return out


__all__ = ["ReplayCase", "ReplaySuite"]
