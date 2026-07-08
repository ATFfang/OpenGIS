"""Post-run knowledge extraction into structured project memory."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from opengis_backend.runs.archive import RunArchive
from opengis_backend.workspace.memory_store import MemoryRecord, MemoryStore

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(
    r"(/[\w\u4e00-\u9fff ./\-]+?\.(?:geojson|shp|gpkg|csv|json|tif|tiff|png|jpg|jpeg|pdf|md|py))"
)
_NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:条|个|家|features?|rows?|%)", re.IGNORECASE)
_DATA_EXTS = {".csv", ".json", ".geojson", ".shp", ".gpkg", ".tif", ".tiff"}


class KnowledgeExtractor:
    """Extract facts, recipes, and dataset cards from a completed run."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path
        self.store = MemoryStore(workspace_path)

    def extract_run(
        self,
        *,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
        workflow: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        if not self.workspace_path:
            return []
        records: list[MemoryRecord] = []
        if self._should_skip(user_message, final_answer):
            return []

        run_id = run_archive.run_id
        records.extend(self._fact_records(user_message, final_answer, run_id))
        records.extend(self._dataset_cards(user_message, final_answer, run_archive))
        records.extend(self._recipe_records(user_message, final_answer, run_archive, workflow))
        self.store.add_many(records)
        logger.info("KnowledgeExtractor stored %d record(s) for run=%s", len(records), run_id)
        return records

    @staticmethod
    def _should_skip(user_message: str, final_answer: str) -> bool:
        text = (user_message or "").strip()
        answer = (final_answer or "").strip()
        if not text:
            return True
        transient = re.search(
            r"(颜色|样式|图层|底图|打开|关闭|显示|隐藏|透明|几个图层|color|style|layer|basemap)",
            text,
            re.IGNORECASE,
        )
        durable = re.search(
            r"(报告|分析|统计|workflow|工作流|数据|文件|csv|geojson|shp|gpkg|recipe|脚本)",
            text,
            re.IGNORECASE,
        )
        return len(text) <= 80 and transient and not durable and not _PATH_RE.search(answer)

    def _fact_records(self, user_message: str, final_answer: str, run_id: str) -> list[MemoryRecord]:
        answer = (final_answer or "").strip()
        paths = sorted(set(_PATH_RE.findall(answer)))[:8]
        numbers = _NUMBER_RE.findall(answer)[:6]
        summary = self._first_sentence(answer)
        if not summary and not paths and not numbers:
            return []
        content = f"Task: {user_message.strip()[:240]}"
        if summary:
            content += f"\nResult: {summary[:500]}"
        if paths:
            content += "\nArtifacts: " + ", ".join(paths)
        if numbers:
            content += "\nKey numbers: " + ", ".join(numbers)
        return [
            MemoryRecord.create(
                kind="fact",
                scope="project",
                title="Run result",
                content=content,
                tags=["run", "result"],
                source_run_id=run_id,
                confidence=0.72,
            )
        ]

    def _dataset_cards(
        self,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
    ) -> list[MemoryRecord]:
        candidates: set[str] = set(_PATH_RE.findall(user_message + "\n" + final_answer))
        for artifact in run_archive.read_artifacts():
            path = artifact.get("path")
            if isinstance(path, str):
                candidates.add(path)
        for call in run_archive.read_tool_calls():
            for value in (call.get("arguments") or {}).values():
                if isinstance(value, str) and Path(value).suffix.lower() in _DATA_EXTS:
                    candidates.add(value)

        records: list[MemoryRecord] = []
        workspace = Path(self.workspace_path).expanduser().resolve() if self.workspace_path else None
        for raw in sorted(candidates):
            suffix = Path(raw).suffix.lower()
            if suffix not in _DATA_EXTS:
                continue
            path = Path(raw)
            if not path.is_absolute() and workspace is not None:
                path = workspace / path
            content = f"Dataset path: {path}"
            metadata: dict[str, Any] = {"path": str(path), "ext": suffix}
            try:
                if path.exists():
                    stat = path.stat()
                    metadata["size_bytes"] = stat.st_size
                    content += f"\nSize: {stat.st_size} bytes"
            except Exception:
                pass
            records.append(
                MemoryRecord.create(
                    kind="dataset",
                    scope="dataset",
                    title=path.name,
                    content=content,
                    tags=["dataset", suffix.lstrip(".")],
                    source_run_id=run_archive.run_id,
                    source_artifact=str(path),
                    confidence=0.8,
                    metadata=metadata,
                )
            )
        return records[:12]

    def _recipe_records(
        self,
        user_message: str,
        final_answer: str,
        run_archive: RunArchive,
        workflow: dict[str, Any] | None,
    ) -> list[MemoryRecord]:
        tool_calls = run_archive.read_tool_calls()
        steps = run_archive.read_steps()
        if not workflow and len(tool_calls) < 2 and not steps:
            return []
        tool_names = [str(call.get("name") or "") for call in tool_calls if call.get("name")]
        scripts = [str(step.get("script_path") or "") for step in steps if step.get("script_path")]
        content = f"Reusable procedure from task: {user_message.strip()[:240]}"
        if workflow:
            content += f"\nWorkflow: {workflow.get('name', 'workflow')}"
        if tool_names:
            content += "\nTool sequence: " + " -> ".join(tool_names[:20])
        if scripts:
            content += "\nScripts: " + ", ".join(scripts[:8])
        summary = self._first_sentence(final_answer)
        if summary:
            content += f"\nOutcome: {summary[:400]}"
        return [
            MemoryRecord.create(
                kind="recipe",
                scope="procedure",
                title="Reusable agent procedure",
                content=content,
                tags=["recipe", "procedure"],
                source_run_id=run_archive.run_id,
                confidence=0.68,
                metadata={"tool_count": len(tool_calls), "script_count": len(steps)},
            )
        ]

    @staticmethod
    def _first_sentence(text: str) -> str:
        stripped = (text or "").strip()
        for sep in ["。", "\n", ". "]:
            if sep in stripped:
                candidate = stripped.split(sep)[0].strip()
                if len(candidate) >= 10:
                    return candidate
        return stripped[:300]


__all__ = ["KnowledgeExtractor"]
