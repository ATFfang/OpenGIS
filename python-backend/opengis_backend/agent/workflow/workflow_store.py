"""Persistence for workflow documents attached to agent queue items."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from opengis_backend.agent.telemetry.script_archive import _app_data_base
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument

logger = logging.getLogger(__name__)


class WorkflowDocumentStore:
    """Store workflow documents so queued workflow runs can be resumed."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path

    @property
    def root(self) -> Path:
        if self.workspace_path:
            return Path(self.workspace_path).expanduser().resolve() / ".opengis" / "workflows"
        return _app_data_base() / "agent-workflows"

    def save(self, workflow: WorkflowDocument, *, workflow_id: str | None = None) -> dict[str, str]:
        wid = workflow_id or uuid.uuid4().hex
        path = self.root / f"{wid}.flow.json"
        payload = workflow.to_dict()
        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["workflow_id"] = wid
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"workflow_id": wid, "workflow_path": str(path)}

    def load(self, workflow_id: str | None = None, workflow_path: str | None = None) -> WorkflowDocument | None:
        path: Path | None = None
        if workflow_path:
            path = Path(workflow_path).expanduser()
        elif workflow_id:
            path = self.root / f"{workflow_id}.flow.json"
        if path is None or not path.exists():
            return None
        try:
            return WorkflowDocument.from_json(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            logger.debug("workflow document load failed: %s", path, exc_info=True)
            return None


__all__ = ["WorkflowDocumentStore"]
