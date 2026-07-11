"""Task-scoped context projection for structured project memory."""

from __future__ import annotations

import logging
import re

from opengis_backend.agent.context.failure_memory import FailureMemoryProjector
from opengis_backend.workspace.memory_store import MemoryRecord, MemoryStore

logger = logging.getLogger(__name__)

_SCOPED_MAP_REQUEST_RE = re.compile(
    r"("
    r"颜色|色彩|分类设色|按类别|类别分|分一下颜色|样式|渲染|符号|"
    r"图层|底图|地图上|当前地图|现在地图|几个图层|多少图层|"
    r"打开|关闭|显示|隐藏|透明|边框|线宽|点大小|"
    r"color|colour|style|symbol|renderer|layer|basemap|visible|visibility"
    r")",
    re.IGNORECASE,
)
_MEMORY_REQUIRED_RE = re.compile(
    r"(报告|学术|论文|分析|统计|workflow|工作流|继续|上次|之前|数据|文件|csv|geojson|shp|gpkg|report|analysis)",
    re.IGNORECASE,
)


def is_short_scoped_map_request(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text:
        return False
    return len(text) <= 100 and bool(_SCOPED_MAP_REQUEST_RE.search(text)) and not _MEMORY_REQUIRED_RE.search(text)


class ContextProjector:
    """Select only task-relevant memory records for the next provider turn."""

    def __init__(self, workspace_path: str | None) -> None:
        self.workspace_path = workspace_path
        self.store = MemoryStore(workspace_path)

    def project(self, user_message: str, *, max_records: int = 12) -> str:
        if not self.workspace_path:
            return ""
        if is_short_scoped_map_request(user_message):
            failure_lessons = FailureMemoryProjector(self.workspace_path).project(user_message, limit=3)
            if failure_lessons:
                return (
                    "Project memory is intentionally hidden for this short scoped map/UI request, "
                    "except directly relevant learned failure lessons.\n"
                    f"{failure_lessons}\n"
                    "Use only the current user request, current map/layer state, and the matching failure lessons. "
                    "Do not start workflows, subagents, reports, or broad analysis unless explicitly asked."
                )
            return (
                "Project memory is intentionally hidden for this short scoped map/UI request. "
                "Use only the current user request and current map/layer state. "
                "Do not start workflows, subagents, reports, or broad analysis unless explicitly asked."
            )

        records = self.store.search(user_message, limit=max_records)
        if not records:
            return ""
        return self.format(records)

    @staticmethod
    def format(records: list[MemoryRecord]) -> str:
        grouped: dict[str, list[MemoryRecord]] = {}
        for record in records:
            grouped.setdefault(record.kind, []).append(record)

        lines = [
            "Use these retrieved project memories only when relevant. "
            "Each item includes its scope and source; prefer current tool state over stale memory.",
        ]
        order = ["failure_lesson", "dataset", "dataset_card", "recipe", "fact"]
        keys = sorted(grouped.keys(), key=lambda k: (order.index(k) if k in order else 99, k))
        for kind in keys:
            lines.append(f"\n### {kind}")
            for record in grouped[kind][:6]:
                title = f"{record.title}: " if record.title else ""
                source = f" source={record.source_run_id}" if record.source_run_id else ""
                scope = f" scope={record.scope}" if record.scope else ""
                lines.append(f"- [{record.id[:8]}]{scope}{source} {title}{record.content[:700]}")
        return "\n".join(lines).strip()


__all__ = ["ContextProjector", "is_short_scoped_map_request"]
