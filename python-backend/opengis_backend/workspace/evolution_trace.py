"""Run-level observability for the self-evolution pipeline."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = ".opengis/evolution"
_TRACES_FILE = "run_traces.jsonl"
_MAX_RECORDS = 500


@dataclass
class EvolutionRunTrace:
    run_id: str
    workspace_path: str | None
    candidate_count: int = 0
    weakness_cluster_count: int = 0
    weakness_candidate_count: int = 0
    evaluation_attempt_count: int = 0
    evaluation_error_count: int = 0
    promoted_count: int = 0
    rejected_count: int = 0
    materialized_count: int = 0
    materialization_error_count: int = 0
    stage_statuses: dict[str, str] = field(default_factory=dict)
    stage_durations_ms: dict[str, float] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_path": self.workspace_path,
            "candidate_count": self.candidate_count,
            "weakness_cluster_count": self.weakness_cluster_count,
            "weakness_candidate_count": self.weakness_candidate_count,
            "evaluation_attempt_count": self.evaluation_attempt_count,
            "evaluation_error_count": self.evaluation_error_count,
            "promoted_count": self.promoted_count,
            "rejected_count": self.rejected_count,
            "materialized_count": self.materialized_count,
            "materialization_error_count": self.materialization_error_count,
            "stage_statuses": dict(self.stage_statuses),
            "stage_durations_ms": dict(self.stage_durations_ms),
            "candidate_ids": list(self.candidate_ids),
            "issues": list(self.issues),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionRunTrace":
        return cls(
            run_id=str(raw.get("run_id") or ""),
            workspace_path=str(raw.get("workspace_path") or "") or None,
            candidate_count=int(raw.get("candidate_count") or 0),
            weakness_cluster_count=int(raw.get("weakness_cluster_count") or 0),
            weakness_candidate_count=int(raw.get("weakness_candidate_count") or 0),
            evaluation_attempt_count=int(raw.get("evaluation_attempt_count") or 0),
            evaluation_error_count=int(raw.get("evaluation_error_count") or 0),
            promoted_count=int(raw.get("promoted_count") or 0),
            rejected_count=int(raw.get("rejected_count") or 0),
            materialized_count=int(raw.get("materialized_count") or 0),
            materialization_error_count=int(raw.get("materialization_error_count") or 0),
            stage_statuses=raw.get("stage_statuses") if isinstance(raw.get("stage_statuses"), dict) else {},
            stage_durations_ms=raw.get("stage_durations_ms") if isinstance(raw.get("stage_durations_ms"), dict) else {},
            candidate_ids=[str(item) for item in raw.get("candidate_ids") or [] if str(item).strip()],
            issues=[str(item) for item in raw.get("issues") or [] if str(item).strip()],
            created_at=float(raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class EvolutionTraceStore:
    """Persist run-level traces for evolution observability."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _EVOLUTION_DIR

    @property
    def path(self) -> Path | None:
        root = self.root
        return root / _TRACES_FILE if root is not None else None

    def record(self, trace: EvolutionRunTrace) -> None:
        path = self.path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = {item.run_id: item for item in self.list(limit=_MAX_RECORDS)}
            rows[trace.run_id] = trace
            self._write_records(list(rows.values()))
        except Exception:
            logger.debug("evolution trace record failed", exc_info=True)

    def list(self, *, limit: int = 200) -> list[EvolutionRunTrace]:
        path = self.path
        if path is None or not path.exists():
            return []
        records = self._read_file(path)
        records.sort(key=lambda item: (-item.created_at, item.run_id))
        return records[: max(1, min(_MAX_RECORDS, int(limit or 200)))]

    def get(self, run_id: str) -> EvolutionRunTrace | None:
        clean = str(run_id or "").strip()
        if not clean:
            return None
        for trace in self.list(limit=_MAX_RECORDS):
            if trace.run_id == clean:
                return trace
        return None

    def _read_file(self, path: Path) -> list[EvolutionRunTrace]:
        out: list[EvolutionRunTrace] = []
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
                    trace = EvolutionRunTrace.from_dict(raw)
                    if trace.run_id:
                        out.append(trace)
        except Exception:
            logger.debug("evolution trace read failed: %s", path, exc_info=True)
        return out

    def _write_records(self, records: list[EvolutionRunTrace]) -> None:
        path = self.path
        if path is None:
            return
        records.sort(key=lambda item: (-item.created_at, item.run_id))
        records = records[:_MAX_RECORDS]
        path.write_text(
            "".join(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n" for record in records),
            encoding="utf-8",
        )


def new_trace_id() -> str:
    return uuid.uuid4().hex


__all__ = ["EvolutionRunTrace", "EvolutionTraceStore", "new_trace_id"]
