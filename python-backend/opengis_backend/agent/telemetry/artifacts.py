"""Artifact index for agent runs and sessions."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    kind: str
    path: str | None = None
    layer_id: str | None = None
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        path: str | None = None,
        layer_id: str | None = None,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "ArtifactRef":
        return cls(
            id=uuid.uuid4().hex,
            kind=kind,
            path=path,
            layer_id=layer_id,
            title=title,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "layer_id": self.layer_id,
            "title": self.title,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ArtifactPointer:
    """Compact provider-facing pointer to a retained artifact."""

    kind: str
    path: str | None = None
    layer_id: str | None = None
    artifact_id: str | None = None
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_ref(cls, ref: ArtifactRef) -> "ArtifactPointer":
        return cls(
            kind=ref.kind,
            path=ref.path,
            layer_id=ref.layer_id,
            artifact_id=ref.id,
            title=ref.title,
            metadata=dict(ref.metadata),
        )

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None, *, default_kind: str = "artifact") -> "ArtifactPointer | None":
        metadata = metadata or {}
        path = _first_str(metadata, "retained_output_path", "artifact_path", "script_abs_path", "script_path")
        layer_id = _first_str(metadata, "artifact_layer_id")
        artifact_id = _first_str(metadata, "artifact_id")
        title = _first_str(metadata, "artifact_title", "artifact_layer_name") or ""
        if not path and not layer_id and not artifact_id:
            return None
        kind = str(metadata.get("artifact_kind") or ("layer" if layer_id and not path else default_kind))
        return cls(
            kind=kind,
            path=path,
            layer_id=layer_id,
            artifact_id=artifact_id,
            title=title,
            metadata={
                key: value
                for key, value in metadata.items()
                if key in {"run_id", "session_id", "tool_name", "call_id", "script_path", "retained_output_path"}
            },
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
        }
        if self.artifact_id:
            out["artifact_id"] = self.artifact_id
        if self.path:
            out["path"] = self.path
        if self.layer_id:
            out["layer_id"] = self.layer_id
        if self.title:
            out["title"] = self.title
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


class ArtifactIndex:
    """Append-only artifact index under ``.opengis/artifacts.jsonl``."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def path(self) -> Path | None:
        if not self.workspace_path:
            return None
        return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "artifacts.jsonl"

    def append(self, artifact: ArtifactRef) -> None:
        path = self.path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(artifact.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("artifact append failed (non-fatal)", exc_info=True)

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        path = self.path
        if path is None or not path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("malformed artifact index line in %s", path)
                    continue
                if isinstance(item, dict):
                    out.append(item)
        except Exception:
            logger.debug("artifact index read failed", exc_info=True)
        out.sort(key=lambda a: a.get("created_at", 0), reverse=True)
        return out[: max(1, limit)]


def artifacts_from_tool_result(tool_name: str, content: str, metadata: dict[str, Any] | None = None) -> list[ArtifactRef]:
    """Best-effort extraction of artifact references from tool output."""
    out: list[ArtifactRef] = []
    seen: set[tuple[str, str]] = set()
    metadata = metadata or {}

    def _append_file(path: str, *, kind: str = "file", title: str | None = None) -> None:
        key = (kind, path)
        if key in seen:
            return
        seen.add(key)
        out.append(
            ArtifactRef.create(
                kind=kind,
                path=path,
                title=title or Path(path).name,
                metadata=metadata,
            )
        )

    def _append_layer(layer_id: str, *, title: str | None = None) -> None:
        key = ("layer", layer_id)
        if key in seen:
            return
        seen.add(key)
        out.append(
            ArtifactRef.create(
                kind="layer",
                layer_id=layer_id,
                title=title or layer_id,
                metadata=metadata,
            )
        )

    retained_output_path = metadata.get("retained_output_path")
    if isinstance(retained_output_path, str) and retained_output_path:
        _append_file(retained_output_path, kind="tool_output", title=f"{tool_name} full output")
    artifact_path = metadata.get("artifact_path")
    if isinstance(artifact_path, str) and artifact_path:
        _append_file(artifact_path)
    artifact_layer_id = metadata.get("artifact_layer_id")
    if isinstance(artifact_layer_id, str) and artifact_layer_id:
        _append_layer(
            artifact_layer_id,
            title=str(metadata.get("artifact_layer_name") or artifact_layer_id),
        )
    try:
        data = json.loads(content)
    except Exception:
        data = None

    if isinstance(data, dict):
        path = data.get("path") or data.get("output_path") or data.get("save_path")
        if isinstance(path, str) and path:
            _append_file(path)
        layer_id = data.get("layer_id")
        if isinstance(layer_id, str) and layer_id:
            _append_layer(layer_id, title=data.get("name", layer_id))

    return out


def artifact_pointer_from_metadata(metadata: dict[str, Any] | None, *, default_kind: str = "artifact") -> dict[str, Any]:
    pointer = ArtifactPointer.from_metadata(metadata, default_kind=default_kind)
    return pointer.to_dict() if pointer else {}


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "ArtifactIndex",
    "ArtifactPointer",
    "ArtifactRef",
    "artifact_pointer_from_metadata",
    "artifacts_from_tool_result",
]
