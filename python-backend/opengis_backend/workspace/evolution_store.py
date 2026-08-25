"""Structured evolution candidate store.

This store keeps self-evolution candidates outside the live prompt path.
It is intentionally separate from memory because candidates are not facts:
they are proposals that still need evaluation and promotion.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = ".opengis/evolution"
_CANDIDATES_FILE = "candidates.jsonl"
_MAX_RECORDS = 500


@dataclass(frozen=True)
class EvolutionCandidate:
    id: str
    kind: str
    scope: str
    title: str
    summary: str
    proposed_change: str = ""
    target_component: str = ""
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_run_id: str = ""
    source_artifact: str = ""
    confidence: float = 0.7
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        scope: str,
        title: str,
        summary: str,
        proposed_change: str = "",
        target_component: str = "",
        evidence: list[str] | None = None,
        tags: list[str] | None = None,
        source_run_id: str = "",
        source_artifact: str = "",
        confidence: float = 0.7,
        status: str = "draft",
        metadata: dict[str, Any] | None = None,
    ) -> "EvolutionCandidate":
        return cls(
            id=uuid.uuid4().hex,
            kind=kind.strip() or "candidate",
            scope=scope.strip() or "project",
            title=title.strip(),
            summary=summary.strip(),
            proposed_change=proposed_change.strip(),
            target_component=target_component.strip(),
            evidence=[item.strip() for item in (evidence or []) if str(item).strip()],
            tags=[item.strip() for item in (tags or []) if str(item).strip()],
            source_run_id=source_run_id.strip(),
            source_artifact=source_artifact.strip(),
            confidence=max(0.0, min(1.0, float(confidence))),
            status=status.strip() or "draft",
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "title": self.title,
            "summary": self.summary,
            "proposed_change": self.proposed_change,
            "target_component": self.target_component,
            "evidence": list(self.evidence),
            "tags": list(self.tags),
            "source_run_id": self.source_run_id,
            "source_artifact": self.source_artifact,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionCandidate":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            kind=str(raw.get("kind") or "candidate"),
            scope=str(raw.get("scope") or "project"),
            title=str(raw.get("title") or ""),
            summary=str(raw.get("summary") or ""),
            proposed_change=str(raw.get("proposed_change") or ""),
            target_component=str(raw.get("target_component") or ""),
            evidence=[str(item) for item in raw.get("evidence") or []],
            tags=[str(item) for item in raw.get("tags") or []],
            source_run_id=str(raw.get("source_run_id") or ""),
            source_artifact=str(raw.get("source_artifact") or ""),
            confidence=float(raw.get("confidence") or 0.7),
            status=str(raw.get("status") or "draft"),
            created_at=float(raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class EvolutionStore:
    """Workspace-scoped self-evolution candidate store."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _EVOLUTION_DIR

    def add(self, candidate: EvolutionCandidate) -> None:
        if not candidate.summary and not candidate.proposed_change:
            return
        root = self.root
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / _CANDIDATES_FILE
            if self._is_duplicate(path, candidate):
                return
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(candidate.to_dict(), ensure_ascii=False, default=str) + "\n")
            self._compact(path)
        except Exception:
            logger.debug("evolution candidate add failed", exc_info=True)

    def add_many(self, candidates: list[EvolutionCandidate]) -> None:
        for candidate in candidates:
            self.add(candidate)

    def get(self, candidate_id: str) -> EvolutionCandidate | None:
        if not candidate_id:
            return None
        for record in self.list(limit=1000):
            if record.id == candidate_id:
                return record
        return None

    def update(
        self,
        candidate_id: str,
        *,
        status: str | None = None,
        confidence: float | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> EvolutionCandidate | None:
        root = self.root
        if root is None or not candidate_id:
            return None
        path = root / _CANDIDATES_FILE
        if not path.exists():
            return None
        try:
            rows = []
            updated: EvolutionCandidate | None = None
            for candidate in self._read_file(path):
                row = candidate.to_dict()
                if candidate.id == candidate_id:
                    if status is not None:
                        row["status"] = status
                    if confidence is not None:
                        row["confidence"] = max(0.0, min(1.0, float(confidence)))
                    if metadata_patch:
                        metadata = dict(row.get("metadata") or {})
                        metadata.update(metadata_patch)
                        row["metadata"] = metadata
                    updated = EvolutionCandidate.from_dict(row)
                    row = updated.to_dict()
                rows.append(row)
            if updated is None:
                return None
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                encoding="utf-8",
            )
            return updated
        except Exception:
            logger.debug("evolution candidate update failed", exc_info=True)
            return None

    def list(self, *, kinds: list[str] | None = None, status: str | None = None, limit: int = 200) -> list[EvolutionCandidate]:
        root = self.root
        if root is None or not root.exists():
            return []
        path = root / _CANDIDATES_FILE
        records = self._read_file(path)
        if kinds:
            kind_set = {item.lower() for item in kinds if item}
            records = [record for record in records if record.kind.lower() in kind_set]
        if status:
            records = [record for record in records if record.status.lower() == status.lower()]
        records.sort(key=lambda record: (-record.created_at, record.id))
        return records[: max(1, limit)]

    def search(self, query: str, *, kinds: list[str] | None = None, limit: int = 12) -> list[EvolutionCandidate]:
        terms = _tokenize(query)
        records = self.list(kinds=kinds, limit=1000)
        scored: list[tuple[float, EvolutionCandidate]] = []
        for record in records:
            score = self._score(record, terms)
            if score <= 0:
                continue
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], -item[1].created_at, item[1].id))
        return [record for _, record in scored[: max(1, limit)]]

    def _read_file(self, path: Path) -> list[EvolutionCandidate]:
        if not path.exists():
            return []
        out: list[EvolutionCandidate] = []
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
                    candidate = EvolutionCandidate.from_dict(raw)
                    if candidate.summary or candidate.proposed_change:
                        out.append(candidate)
        except Exception:
            logger.debug("evolution candidate read failed: %s", path, exc_info=True)
        return out

    def _compact(self, path: Path) -> None:
        records = self._read_file(path)
        if len(records) <= _MAX_RECORDS:
            return
        records.sort(key=lambda record: (-record.created_at, record.id))
        keep = records[:_MAX_RECORDS]
        try:
            path.write_text(
                "".join(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n" for record in keep),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("evolution candidate compact failed", exc_info=True)

    @staticmethod
    def _score(candidate: EvolutionCandidate, terms: list[str]) -> float:
        haystack = " ".join([
            candidate.kind,
            candidate.scope,
            candidate.title,
            candidate.summary,
            candidate.proposed_change,
            candidate.target_component,
            " ".join(candidate.tags),
            " ".join(candidate.evidence),
        ]).lower()
        if not terms:
            return 0.2 + candidate.confidence
        score = sum(1.0 for term in terms if term in haystack)
        if score <= 0:
            return 0.0
        return score + candidate.confidence

    @staticmethod
    def _is_duplicate(path: Path, candidate: EvolutionCandidate) -> bool:
        if not path.exists():
            return False
        needle = _fingerprint(candidate)
        for existing in EvolutionStore(None)._read_file(path):
            if _fingerprint(existing) == needle:
                return True
        return False


def _fingerprint(candidate: EvolutionCandidate) -> str:
    text = re.sub(r"\s+", " ", " ".join([
        candidate.kind,
        candidate.scope,
        candidate.title,
        candidate.summary,
        candidate.proposed_change,
        candidate.target_component,
        " ".join(candidate.tags),
        " ".join(candidate.evidence[:4]),
    ]).strip().lower())
    return text[:360]


def _tokenize(text: str) -> list[str]:
    lowered = (text or "").lower()
    raw = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped[:120]


__all__ = ["EvolutionCandidate", "EvolutionStore"]
