"""Structured project memory store.

This supersedes the old free-form ``.opengis/memory.md`` path for agent
prompt injection. The markdown file may still exist for human notes, but the
agent reads and writes structured JSONL records so memories can be searched,
scoped, aged, and traced back to the run that produced them.
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

_MEMORY_DIR = ".opengis/memory"
_FACTS_FILE = "facts.jsonl"
_RECIPES_FILE = "recipes.jsonl"
_DATASETS_FILE = "datasets.jsonl"
_FAILURES_FILE = "failures.jsonl"
_MAX_RECORDS_PER_KIND = 500


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: str
    scope: str
    content: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    source_run_id: str = ""
    source_artifact: str = ""
    confidence: float = 0.7
    created_at: float = field(default_factory=time.time)
    last_used_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        scope: str,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        source_run_id: str = "",
        source_artifact: str = "",
        confidence: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> "MemoryRecord":
        return cls(
            id=uuid.uuid4().hex,
            kind=kind,
            scope=scope,
            content=content.strip(),
            title=title.strip(),
            tags=tags or [],
            source_run_id=source_run_id,
            source_artifact=source_artifact,
            confidence=max(0.0, min(1.0, float(confidence))),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "source_run_id": self.source_run_id,
            "source_artifact": self.source_artifact,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MemoryRecord":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            kind=str(raw.get("kind") or "fact"),
            scope=str(raw.get("scope") or "project"),
            title=str(raw.get("title") or ""),
            content=str(raw.get("content") or ""),
            tags=[str(x) for x in raw.get("tags") or []],
            source_run_id=str(raw.get("source_run_id") or ""),
            source_artifact=str(raw.get("source_artifact") or ""),
            confidence=float(raw.get("confidence") or 0.7),
            created_at=float(raw.get("created_at") or time.time()),
            last_used_at=float(raw.get("last_used_at") or 0.0),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class MemoryStore:
    """Workspace-scoped structured memory index."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def root(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / _MEMORY_DIR

    def add(self, record: MemoryRecord) -> None:
        if not record.content:
            return
        root = self.root
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / self._filename_for(record.kind)
            if self._is_duplicate(path, record):
                return
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
            self._compact(path)
        except Exception:
            logger.debug("structured memory add failed", exc_info=True)

    def add_many(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self.add(record)

    def list(self, *, kinds: list[str] | None = None, limit: int = 200) -> list[MemoryRecord]:
        root = self.root
        if root is None or not root.exists():
            return []
        filenames = [self._filename_for(kind) for kind in kinds] if kinds else [
            _FACTS_FILE,
            _RECIPES_FILE,
            _DATASETS_FILE,
            _FAILURES_FILE,
        ]
        out: list[MemoryRecord] = []
        for filename in filenames:
            out.extend(self._read_file(root / filename))
        out.sort(key=lambda r: max(r.last_used_at, r.created_at), reverse=True)
        return out[: max(1, limit)]

    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 12,
        touch: bool = True,
    ) -> list[MemoryRecord]:
        terms = _tokenize(query)
        records = self.list(kinds=kinds, limit=1000)
        scored: list[tuple[float, MemoryRecord]] = []
        scope_set = {s.lower() for s in scopes or []}
        for record in records:
            if scope_set and record.scope.lower() not in scope_set:
                continue
            score = self._score(record, terms)
            if score <= 0:
                continue
            scored.append((score, record))
        # Deterministic ordering: relevance desc, then newest-first, then id.
        # A stable tie-breaker keeps the retrieved set byte-identical across
        # turns so any cacheable prompt text that embeds it does not drift.
        scored.sort(key=lambda item: (-item[0], -item[1].created_at, item[1].id))
        selected = [record for _, record in scored[: max(1, limit)]]
        # ``_touch`` mutates last_used_at, which reorders future retrievals.
        # The prompt-projection path passes ``touch=False`` so reading memory
        # for the prompt never perturbs subsequent retrievals (a structural
        # source of turn-to-turn drift).
        if touch:
            self._touch(selected)
        return selected

    def _read_file(self, path: Path) -> list[MemoryRecord]:
        if not path.exists():
            return []
        out: list[MemoryRecord] = []
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
                    record = MemoryRecord.from_dict(raw)
                    if record.content:
                        out.append(record)
        except Exception:
            logger.debug("structured memory read failed: %s", path, exc_info=True)
        return out

    def _touch(self, records: list[MemoryRecord]) -> None:
        # Deliberately best-effort and coarse: only update if there are few hits.
        if not records or len(records) > 20:
            return
        root = self.root
        if root is None:
            return
        by_file: dict[Path, set[str]] = {}
        for record in records:
            by_file.setdefault(root / self._filename_for(record.kind), set()).add(record.id)
        now = time.time()
        for path, ids in by_file.items():
            current = self._read_file(path)
            changed = False
            rows: list[dict[str, Any]] = []
            for record in current:
                row = record.to_dict()
                if record.id in ids:
                    row["last_used_at"] = now
                    changed = True
                rows.append(row)
            if changed:
                try:
                    path.write_text(
                        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                        encoding="utf-8",
                    )
                except Exception:
                    logger.debug("structured memory touch failed", exc_info=True)

    @staticmethod
    def _filename_for(kind: str) -> str:
        normalized = (kind or "fact").lower()
        if normalized in {"recipe", "procedure"}:
            return _RECIPES_FILE
        if normalized in {"dataset", "dataset_card", "layer", "artifact"}:
            return _DATASETS_FILE
        if normalized in {"failure", "failure_lesson", "bug_pattern", "error_lesson"}:
            return _FAILURES_FILE
        return _FACTS_FILE

    @staticmethod
    def _score(record: MemoryRecord, terms: list[str]) -> float:
        haystack = " ".join([
            record.kind,
            record.scope,
            record.title,
            record.content,
            " ".join(record.tags),
        ]).lower()
        if not terms:
            base = 0.2
        else:
            base = sum(1.0 for term in terms if term in haystack)
        if base <= 0:
            return 0.0
        # Deterministic score: NO wall-clock term. A time-based recency factor
        # made the float score (and thus the ordering) drift every call, which
        # broke prompt-prefix stability. Recency is now expressed only through
        # the stable ``created_at`` tie-breaker in :py:meth:`search`.
        return base + record.confidence

    @staticmethod
    def _is_duplicate(path: Path, record: MemoryRecord) -> bool:
        if not path.exists():
            return False
        needle = _fingerprint(record)
        for existing in MemoryStore(None)._read_file(path):
            if _fingerprint(existing) == needle:
                return True
        return False

    @staticmethod
    def _compact(path: Path) -> None:
        records = MemoryStore(None)._read_file(path)
        if len(records) <= _MAX_RECORDS_PER_KIND:
            return
        records.sort(key=lambda r: max(r.last_used_at, r.created_at), reverse=True)
        keep = records[:_MAX_RECORDS_PER_KIND]
        try:
            path.write_text(
                "".join(json.dumps(r.to_dict(), ensure_ascii=False, default=str) + "\n" for r in keep),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("structured memory compact failed", exc_info=True)


def _tokenize(text: str) -> list[str]:
    lowered = (text or "").lower()
    raw = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    tokens: list[str] = []
    for item in raw:
        tokens.append(item)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", item):
            tokens.extend(item[i : i + 2] for i in range(0, len(item) - 1))
            tokens.extend(item[i : i + 3] for i in range(0, len(item) - 2))
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped[:120]


def _fingerprint(record: MemoryRecord) -> str:
    if record.kind == "failure_lesson" and record.metadata.get("fingerprint"):
        return f"{record.kind}:{record.scope}:{record.metadata['fingerprint']}"
    text = re.sub(r"\s+", " ", record.content.strip().lower())
    return f"{record.kind}:{record.scope}:{text[:300]}"


__all__ = ["MemoryRecord", "MemoryStore"]
