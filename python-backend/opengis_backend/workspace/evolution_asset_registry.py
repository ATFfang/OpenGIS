"""Activation registry for self-evolution materialized assets.

Self-evolution may produce reusable Operations or Skills, but those assets
must not automatically mutate the active harness. This registry is the narrow
activation boundary: promoted assets are recorded as draft, then a user or
approved control path can enable, archive, or reject them.
"""

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
_ASSETS_FILE = "assets.jsonl"
_ASSET_EVENTS_FILE = "asset_events.jsonl"
_MAX_RECORDS = 1000
VALID_ASSET_STATES = {"draft", "enabled", "archived", "rejected"}
VALID_ASSET_KINDS = {"operation", "skill", "prompt_patch", "tool_policy"}


@dataclass(frozen=True)
class EvolutionAssetRecord:
    id: str
    kind: str
    name: str
    path: str
    state: str = "draft"
    revision: int = 1
    source_candidate_id: str = ""
    source_materialization_id: str = ""
    source_run_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "state": self.state,
            "revision": self.revision,
            "source_candidate_id": self.source_candidate_id,
            "source_materialization_id": self.source_materialization_id,
            "source_run_id": self.source_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionAssetRecord":
        kind = _normalise_kind(raw.get("kind"))
        state = _normalise_state(raw.get("state"))
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            kind=kind,
            name=str(raw.get("name") or raw.get("asset_id") or "").strip(),
            path=str(raw.get("path") or raw.get("asset_path") or "").strip(),
            state=state,
            revision=max(1, int(raw.get("revision") or 1)),
            source_candidate_id=str(raw.get("source_candidate_id") or raw.get("candidate_id") or ""),
            source_materialization_id=str(raw.get("source_materialization_id") or raw.get("materialization_id") or ""),
            source_run_id=str(raw.get("source_run_id") or ""),
            created_at=float(raw.get("created_at") or time.time()),
            updated_at=float(raw.get("updated_at") or raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


@dataclass(frozen=True)
class EvolutionAssetEvent:
    id: str
    asset_id: str
    kind: str
    name: str
    event_type: str
    from_state: str = ""
    to_state: str = ""
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "kind": self.kind,
            "name": self.name,
            "event_type": self.event_type,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def create(
        cls,
        *,
        asset: EvolutionAssetRecord,
        event_type: str,
        from_state: str = "",
        to_state: str = "",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "EvolutionAssetEvent":
        return cls(
            id=uuid.uuid4().hex,
            asset_id=asset.id,
            kind=asset.kind,
            name=asset.name,
            event_type=str(event_type or "state_changed"),
            from_state=str(from_state or ""),
            to_state=str(to_state or ""),
            reason=str(reason or ""),
            metadata=metadata or {},
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionAssetEvent":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            asset_id=str(raw.get("asset_id") or ""),
            kind=str(raw.get("kind") or ""),
            name=str(raw.get("name") or ""),
            event_type=str(raw.get("event_type") or "state_changed"),
            from_state=str(raw.get("from_state") or ""),
            to_state=str(raw.get("to_state") or ""),
            reason=str(raw.get("reason") or ""),
            created_at=float(raw.get("created_at") or time.time()),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        )


class EvolutionAssetRegistry:
    """Workspace-scoped activation state for evolution-produced assets."""

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
        return root / _ASSETS_FILE if root is not None else None

    @property
    def events_path(self) -> Path | None:
        root = self.root
        return root / _ASSET_EVENTS_FILE if root is not None else None

    def upsert_from_materialization(
        self,
        materialization: Any,
        *,
        default_state: str = "draft",
    ) -> EvolutionAssetRecord | None:
        kind = _normalise_kind(getattr(materialization, "target_kind", ""))
        name = str(getattr(materialization, "asset_id", "") or "").strip()
        if not name:
            return None
        state = _normalise_state(default_state)
        existing = self.find(kind, name)
        now = time.time()
        if existing is not None:
            record = EvolutionAssetRecord(
                id=existing.id,
                kind=kind,
                name=name,
                path=str(getattr(materialization, "asset_path", "") or existing.path),
                state=existing.state,
                revision=existing.revision + 1,
                source_candidate_id=str(getattr(materialization, "candidate_id", "") or existing.source_candidate_id),
                source_materialization_id=str(getattr(materialization, "id", "") or existing.source_materialization_id),
                source_run_id=str(getattr(materialization, "source_run_id", "") or existing.source_run_id),
                created_at=existing.created_at,
                updated_at=now,
                metadata={**existing.metadata, "materialization_status": getattr(materialization, "status", "")},
            )
        else:
            record = EvolutionAssetRecord(
                id=uuid.uuid4().hex,
                kind=kind,
                name=name,
                path=str(getattr(materialization, "asset_path", "") or ""),
                state=state,
                revision=1,
                source_candidate_id=str(getattr(materialization, "candidate_id", "") or ""),
                source_materialization_id=str(getattr(materialization, "id", "") or ""),
                source_run_id=str(getattr(materialization, "source_run_id", "") or ""),
                created_at=now,
                updated_at=now,
                metadata={
                    "asset_name": getattr(materialization, "asset_name", ""),
                    "materialization_mode": getattr(materialization, "materialization_mode", ""),
                    "materialization_status": getattr(materialization, "status", ""),
                },
            )
        self.upsert(record)
        if existing is None:
            self._append_event(EvolutionAssetEvent.create(
                asset=record,
                event_type="materialized",
                to_state=record.state,
                reason="materialized evolution candidate as gated asset",
                metadata={"materialization_status": getattr(materialization, "status", "")},
            ))
        return record

    def upsert(self, record: EvolutionAssetRecord) -> None:
        path = self.path
        if path is None or not record.name:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            records = self.list(limit=_MAX_RECORDS)
            by_key = {item.key: item for item in records}
            by_key[record.key] = record
            self._write_records(list(by_key.values()))
        except Exception:
            logger.debug("evolution asset upsert failed", exc_info=True)

    def list(
        self,
        *,
        kind: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[EvolutionAssetRecord]:
        path = self.path
        if path is None or not path.exists():
            return []
        records = self._read_file(path)
        if kind:
            kind_value = _normalise_kind(kind)
            records = [record for record in records if record.kind == kind_value]
        if state:
            state_value = _normalise_state(state)
            records = [record for record in records if record.state == state_value]
        records.sort(key=lambda item: (-item.updated_at, -item.created_at, item.kind, item.name))
        return records[: max(1, min(_MAX_RECORDS, int(limit or 200)))]

    def find(self, kind: str, name: str) -> EvolutionAssetRecord | None:
        clean_name = str(name or "").strip()
        if not clean_name:
            return None
        kind_value = _normalise_kind(kind)
        for record in self.list(kind=kind_value, limit=_MAX_RECORDS):
            if record.name == clean_name:
                return record
        return None

    def find_by_id(self, asset_id: str) -> EvolutionAssetRecord | None:
        clean = str(asset_id or "").strip()
        if not clean:
            return None
        for record in self.list(limit=_MAX_RECORDS):
            if record.id == clean:
                return record
        return None

    def set_state(self, asset_id: str, state: str, *, reason: str = "") -> EvolutionAssetRecord:
        target = self.find_by_id(asset_id)
        if target is None:
            raise KeyError(f"evolution asset not found: {asset_id}")
        next_state = _normalise_state(state)
        if target.state == next_state:
            return target
        record = EvolutionAssetRecord(
            id=target.id,
            kind=target.kind,
            name=target.name,
            path=target.path,
            state=next_state,
            revision=target.revision + 1,
            source_candidate_id=target.source_candidate_id,
            source_materialization_id=target.source_materialization_id,
            source_run_id=target.source_run_id,
            created_at=target.created_at,
            updated_at=time.time(),
            metadata=target.metadata,
        )
        self.upsert(record)
        self._append_event(EvolutionAssetEvent.create(
            asset=record,
            event_type="state_changed",
            from_state=target.state,
            to_state=next_state,
            reason=reason or f"{target.state} -> {next_state}",
        ))
        return record

    def rollback(self, asset_id: str, *, reason: str = "") -> EvolutionAssetRecord:
        target = self.find_by_id(asset_id)
        if target is None:
            raise KeyError(f"evolution asset not found: {asset_id}")
        history = [
            event for event in self.history(asset_id, limit=100)
            if event.to_state in VALID_ASSET_STATES
        ]
        previous = ""
        for event in history:
            if event.to_state != target.state:
                previous = event.to_state
                break
        if not previous:
            previous = "draft" if target.state != "draft" else target.state
        if previous == target.state:
            return target
        record = EvolutionAssetRecord(
            id=target.id,
            kind=target.kind,
            name=target.name,
            path=target.path,
            state=previous,
            revision=target.revision + 1,
            source_candidate_id=target.source_candidate_id,
            source_materialization_id=target.source_materialization_id,
            source_run_id=target.source_run_id,
            created_at=target.created_at,
            updated_at=time.time(),
            metadata=target.metadata,
        )
        self.upsert(record)
        self._append_event(EvolutionAssetEvent.create(
            asset=record,
            event_type="rollback",
            from_state=target.state,
            to_state=previous,
            reason=reason or f"rollback to previous state {previous}",
        ))
        return record

    def archive_unloadable(self, *, reason: str = "") -> list[EvolutionAssetRecord]:
        archived: list[EvolutionAssetRecord] = []
        for asset in self.list(limit=_MAX_RECORDS):
            if asset.state in {"archived", "rejected"}:
                continue
            health = self.inspect(asset)
            if health.get("loadable"):
                continue
            record = self.set_state(
                asset.id,
                "archived",
                reason=reason or "archive unloadable evolution asset",
            )
            archived.append(record)
        return archived

    def is_enabled(self, kind: str, name: str) -> bool:
        record = self.find(kind, name)
        return record is None or record.state == "enabled"

    def state_for(self, kind: str, name: str) -> str | None:
        record = self.find(kind, name)
        return record.state if record is not None else None

    def history(self, asset_id: str, *, limit: int = 20) -> list[EvolutionAssetEvent]:
        path = self.events_path
        if path is None or not path.exists():
            return []
        clean = str(asset_id or "").strip()
        events = [
            event for event in self._read_events(path)
            if event.asset_id == clean
        ]
        events.sort(key=lambda item: (-item.created_at, item.id))
        return events[: max(1, min(_MAX_RECORDS, int(limit or 20)))]

    def inspect(self, asset: EvolutionAssetRecord) -> dict[str, Any]:
        path_exists = False
        entry_exists = False
        manifest_exists = False
        issues: list[str] = []
        asset_path = Path(asset.path).expanduser() if asset.path else None
        if asset_path is None:
            issues.append("missing asset path")
        else:
            try:
                path_exists = asset_path.exists()
            except OSError:
                path_exists = False
            if not path_exists:
                issues.append("asset path does not exist")
            elif asset.kind == "operation":
                manifest_exists = (asset_path / "operation.json").is_file()
                entry_exists = (asset_path / "main.py").is_file()
                if not manifest_exists:
                    issues.append("operation.json missing")
                if not entry_exists:
                    issues.append("main.py missing")
            elif asset.kind == "skill":
                manifest_exists = (asset_path / "SKILL.md").is_file()
                entry_exists = manifest_exists
                if not manifest_exists:
                    issues.append("SKILL.md missing")
            elif asset.kind == "prompt_patch":
                manifest_exists = (asset_path / "prompt_patch.json").is_file()
                entry_exists = (asset_path / "PATCH.md").is_file()
                if not manifest_exists:
                    issues.append("prompt_patch.json missing")
                if not entry_exists:
                    issues.append("PATCH.md missing")
            elif asset.kind == "tool_policy":
                manifest_exists = (asset_path / "tool_policy.json").is_file()
                entry_exists = (asset_path / "POLICY.md").is_file()
                if not manifest_exists:
                    issues.append("tool_policy.json missing")
                if not entry_exists:
                    issues.append("POLICY.md missing")
        if asset.state == "enabled" and issues:
            issues.append("enabled asset is not loadable")
        return {
            "path_exists": path_exists,
            "entry_exists": entry_exists,
            "manifest_exists": manifest_exists,
            "loadable": path_exists and entry_exists and manifest_exists and not (asset.state == "enabled" and issues),
            "issues": issues,
        }

    def _read_file(self, path: Path) -> list[EvolutionAssetRecord]:
        out: list[EvolutionAssetRecord] = []
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
                    try:
                        record = EvolutionAssetRecord.from_dict(raw)
                    except ValueError:
                        logger.debug("skipping invalid evolution asset record: %s", raw, exc_info=True)
                        continue
                    if record.name and record.kind in VALID_ASSET_KINDS:
                        out.append(record)
        except Exception:
            logger.debug("evolution asset registry read failed: %s", path, exc_info=True)
        return out

    def _write_records(self, records: list[EvolutionAssetRecord]) -> None:
        path = self.path
        if path is None:
            return
        records.sort(key=lambda item: (-item.updated_at, -item.created_at, item.kind, item.name))
        records = records[:_MAX_RECORDS]
        path.write_text(
            "".join(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n" for record in records),
            encoding="utf-8",
        )

    def _append_event(self, event: EvolutionAssetEvent) -> None:
        path = self.events_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
            self._compact_events(path)
        except Exception:
            logger.debug("evolution asset event append failed", exc_info=True)

    def _read_events(self, path: Path) -> list[EvolutionAssetEvent]:
        out: list[EvolutionAssetEvent] = []
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
                    event = EvolutionAssetEvent.from_dict(raw)
                    if event.asset_id:
                        out.append(event)
        except Exception:
            logger.debug("evolution asset event read failed: %s", path, exc_info=True)
        return out

    def _compact_events(self, path: Path) -> None:
        events = self._read_events(path)
        if len(events) <= _MAX_RECORDS:
            return
        events.sort(key=lambda item: (-item.created_at, item.id))
        keep = events[:_MAX_RECORDS]
        path.write_text(
            "".join(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n" for event in keep),
            encoding="utf-8",
        )


def _normalise_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in VALID_ASSET_KINDS:
        raise ValueError(f"invalid evolution asset kind: {kind or value}")
    return kind


def _normalise_state(value: Any) -> str:
    state = str(value or "draft").strip().lower()
    if state not in VALID_ASSET_STATES:
        raise ValueError(f"invalid evolution asset state: {state or value}")
    return state


__all__ = [
    "EvolutionAssetRecord",
    "EvolutionAssetEvent",
    "EvolutionAssetRegistry",
    "VALID_ASSET_KINDS",
    "VALID_ASSET_STATES",
]
