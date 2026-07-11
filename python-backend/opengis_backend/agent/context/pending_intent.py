"""Short follow-up resolution for same-session pending actions.

This is not long-term memory and not generic query rewriting. It resolves
tiny user confirmations such as "ok" against the immediately preceding
assistant offer, then emits a structured turn objective for the runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_AFFIRMATION_RE = re.compile(
    r"^\s*(ok|okay|yes|y|sure|go ahead|continue|继续|好|好的|可以|行|嗯|嗯嗯|加载吧|展示吧|显示吧|确认|同意)[。.!！\s]*$",
    re.IGNORECASE,
)
_MAP_LOAD_OFFER_RE = re.compile(
    r"(需要|要不要|是否|要我|帮你|我可以).{0,20}(加载|显示|展示|加到|放到).{0,20}(地图|图层|map|layer)",
    re.IGNORECASE,
)
_PATH_RE = re.compile(
    r"(?P<path>(?:/[\w\u4e00-\u9fff ._\-]+/)?[\w\u4e00-\u9fff ._\-]+?\.(?:geojson|shp|gpkg|csv|json|tif|tiff|png|jpg|jpeg))"
)
_DATA_EXTENSIONS = {".geojson", ".shp", ".gpkg", ".csv", ".json", ".tif", ".tiff"}


@dataclass(frozen=True)
class PendingIntent:
    kind: str
    source_text: str
    resolved_objective: str
    expected_tools: tuple[str, ...] = ()
    avoid_tools: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prompt(self, original_user_message: str) -> str:
        lines = [
            "## Resolved Turn Objective",
            f"Original user message: {original_user_message}",
            f"Resolved objective: {self.resolved_objective}",
            f"Resolution source: previous assistant pending offer ({self.kind})",
        ]
        if self.artifacts:
            lines.append("Artifacts from previous turn:")
            lines.extend(f"- {item}" for item in self.artifacts[:8])
        if self.expected_tools:
            lines.append("Preferred tools: " + ", ".join(self.expected_tools))
        if self.avoid_tools:
            lines.append("Avoid unless preferred tools fail: " + ", ".join(self.avoid_tools))
        lines.append(
            "Use this resolved objective as the current task. Do not reinterpret "
            "the short confirmation as a request to repeat previous analysis."
        )
        return "\n".join(lines)


class PendingIntentResolver:
    """Resolve short confirmations against recent assistant offers."""

    def __init__(self, workspace_path: str | None = None) -> None:
        self.workspace_path = workspace_path

    def resolve(self, messages: list[dict[str, Any]], user_message: str) -> PendingIntent | None:
        if not is_short_affirmation(user_message):
            return None
        offer = self._latest_assistant_offer(messages)
        if not offer:
            return None
        offer_text = str(offer.get("content") or "")
        if _MAP_LOAD_OFFER_RE.search(offer_text):
            artifacts = self._recent_artifacts(messages, offer_text)
            return PendingIntent(
                kind="confirm_map_load",
                source_text=_compact(offer_text, 500),
                resolved_objective=self._map_load_objective(artifacts),
                expected_tools=("add_layer", "set_categorized_style", "update_layer_style", "zoom_to_layer"),
                avoid_tools=("bash", "execute_code", "run_script_file", "list_directory", "glob"),
                artifacts=tuple(artifacts[:8]),
                metadata={"offer": _compact(offer_text, 500)},
            )
        return None

    @staticmethod
    def _latest_assistant_offer(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                return message
        return None

    def _recent_artifacts(self, messages: list[dict[str, Any]], offer_text: str) -> list[str]:
        candidates: list[str] = []
        for text in [offer_text, *self._recent_tool_texts(messages)]:
            candidates.extend(self._extract_paths(text))
        return _dedupe([item for item in candidates if Path(item).suffix.lower() in _DATA_EXTENSIONS])

    def _recent_tool_texts(self, messages: list[dict[str, Any]], limit: int = 18) -> list[str]:
        out: list[str] = []
        for message in reversed(messages):
            if len(out) >= limit:
                break
            meta = message.get("_meta") if isinstance(message.get("_meta"), dict) else {}
            if meta.get("kind") == "tool_result" or message.get("role") == "tool":
                out.append(str(message.get("content") or ""))
        return out

    def _extract_paths(self, text: str) -> list[str]:
        paths: list[str] = []
        paths.extend(self._extract_json_paths(text))
        for match in _PATH_RE.finditer(text or ""):
            paths.append(self._resolve_path(match.group("path")))
        return paths

    def _extract_json_paths(self, text: str) -> list[str]:
        try:
            data = json.loads(text)
        except Exception:
            return []
        out: list[str] = []

        def visit(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    visit(child_value, str(child_key))
            elif isinstance(value, list):
                for item in value[:100]:
                    visit(item, key)
            elif isinstance(value, str):
                suffix = Path(value).suffix.lower()
                if key.endswith("_path") or key in {"path", "output_path", "save_path"} or suffix in _DATA_EXTENSIONS:
                    if suffix in _DATA_EXTENSIONS:
                        out.append(self._resolve_path(value))

        visit(data)
        return out

    def _resolve_path(self, raw: str) -> str:
        text = str(raw or "").strip().strip("`'\"")
        if not text:
            return ""
        path = Path(text)
        if path.is_absolute() or not self.workspace_path:
            return text
        return str(Path(self.workspace_path).expanduser().resolve() / path)

    @staticmethod
    def _map_load_objective(artifacts: list[str]) -> str:
        if artifacts:
            return (
                "用户确认上一轮提议：把上一轮产出的数据结果加载到地图上查看。"
                "优先加载这些产物，设置清晰样式并缩放到结果范围。"
                "不要重新测试 operation、重复分析文件或扫描目录，除非加载失败。"
            )
        return (
            "用户确认上一轮提议：把上一轮结果加载到地图上查看。"
            "使用最近的产物或图层状态完成加载、样式设置和缩放。"
            "不要重新测试或重复分析，除非缺少可加载产物。"
        )


def is_short_affirmation(text: str) -> bool:
    return bool(_AFFIRMATION_RE.match(text or ""))


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _compact(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 18] + " ... [omitted]"


__all__ = [
    "PendingIntent",
    "PendingIntentResolver",
    "is_short_affirmation",
]
