"""Runtime control layer for OpenGIS agent loops.

This module keeps task-control policy out of the raw function-call loop.  It
does not prescribe a rigid workflow; it provides a turn objective, a soft task
mode, and lightweight guardrails that stop clearly unrelated tool execution
before side effects happen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opengis_backend.agent.context.failure_memory import FailureMemoryProjector
from opengis_backend.agent.context.pending_intent import PendingIntent
from opengis_backend.agent.execution.capability_router import CapabilityRouter, CapabilitySelection
from opengis_backend.agent.execution.tool_capabilities import capability_for, tools_with_side_effect
from opengis_backend.agent.loop.turn_runner import ToolSettlement


class TaskMode(str, Enum):
    GENERAL = "general"
    OPERATION_REPAIR = "operation_repair"
    OPERATION_RUN = "operation_run"
    MAP_RENDERING = "map_rendering"
    DATA_ANALYSIS = "data_analysis"
    WORKFLOW = "workflow"
    WORKER = "worker"


@dataclass(frozen=True)
class TurnObjective:
    """Immutable objective for one user turn."""

    user_request: str
    mode: TaskMode
    constraints: tuple[str, ...] = ()

    def to_prompt(self) -> str:
        constraints = "; ".join(_compact_text(item, 180) for item in self.constraints) or "none"
        return (
            "## Turn Objective\n"
            f"mode={self.mode.value}\n"
            f"request={_compact_text(self.user_request, 900)}\n"
            f"constraints={constraints}\n"
            "rule=serve this request; prefer it over older assistant summaries/tool logs."
        )


@dataclass(frozen=True)
class GuardResult:
    blocked_call_ids: dict[str, str] = field(default_factory=dict)

    @property
    def has_blocks(self) -> bool:
        return bool(self.blocked_call_ids)


@dataclass(frozen=True)
class ControlDecision:
    corrective_message: str = ""
    force_final_reason: str | None = None

    @property
    def has_correction(self) -> bool:
        return bool(self.corrective_message.strip())


@dataclass(frozen=True)
class LoopAnomaly:
    kind: str
    message: str


class LoopAnomalyDetector:
    """Detect tool-path patterns that usually indicate loop drift."""

    @staticmethod
    def detect(tool_history: list[str], recent_failures: list[str]) -> LoopAnomaly | None:
        map_churn = LoopAnomalyDetector._map_churn(tool_history)
        if map_churn:
            return LoopAnomaly(
                kind="map_churn",
                message=(
                    "The last actions repeatedly removed/added/styled map layers. "
                    "Check whether this still serves the current user objective."
                ),
            )
        repeated_failure = LoopAnomalyDetector._repeated_failure(recent_failures)
        if repeated_failure:
            return LoopAnomaly(
                kind="repeated_failure",
                message=(
                    f"The same tool appears to be failing repeatedly: {repeated_failure}. "
                    "Do not keep retrying the same call unchanged."
                ),
            )
        return None

    @staticmethod
    def _map_churn(tool_history: list[str]) -> bool:
        if len(tool_history) < 6:
            return False
        recent = tool_history[-8:]
        return recent.count("remove_layer") >= 2 and recent.count("add_layer") >= 2

    @staticmethod
    def _repeated_failure(recent_failures: list[str]) -> str:
        if len(recent_failures) < 2:
            return ""
        last = _failure_signature(recent_failures[-1])
        previous = _failure_signature(recent_failures[-2])
        if last and last == previous:
            return last
        return ""


@dataclass
class RuntimeControl:
    """Soft runner control for one user turn."""

    objective: TurnObjective
    deviation_count: int = 0
    saw_operation_failure: bool = False
    saw_operation_edit: bool = False
    saw_operation_run: bool = False
    saw_worker_tool: bool = False
    saw_workflow_tool: bool = False
    saw_map_tool: bool = False
    analysis_code_successes: int = 0
    recent_failures: list[str] = field(default_factory=list)
    tool_history: list[str] = field(default_factory=list)
    workspace_path: str | None = None
    pending_intent: PendingIntent | None = None
    feature_flags: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_user_message(
        cls,
        user_message: str,
        *,
        workspace_path: str | None = None,
        pending_intent: PendingIntent | None = None,
        feature_flags: dict[str, Any] | None = None,
    ) -> "RuntimeControl":
        mode = TaskMode.MAP_RENDERING if pending_intent and pending_intent.kind == "confirm_map_load" else infer_task_mode(user_message)
        constraints: list[str] = []
        if pending_intent and pending_intent.kind == "confirm_map_load":
            constraints.extend(
                [
                    "User confirmed previous offer; execute resolved objective, not literal confirmation.",
                    "Load/style/zoom previous result artifacts; do not rerun analysis unless loading fails.",
                ]
            )
        elif mode is TaskMode.OPERATION_REPAIR:
            constraints.extend(
                [
                    "Repair existing Operation in place; do not bypass with one-off script.",
                    "After failed run_operation: inspect contract/code, edit, rerun same operation.",
                    "Avoid map/data-conversion side paths until operation is repaired or user changes objective.",
                ]
            )
        elif mode is TaskMode.MAP_RENDERING:
            constraints.append("Use current map/layer state; avoid unrelated file/operation repair work.")
        elif mode is TaskMode.DATA_ANALYSIS:
            constraints.append(
                "Answer once evidence is enough; do not create reports/charts/layers/files unless asked."
            )
        elif mode is TaskMode.WORKER:
            constraints.append("Use worker lifecycle tools; do not emulate background behavior with one-shot scripts.")
        return cls(
            objective=TurnObjective(user_request=user_message, mode=mode, constraints=tuple(constraints)),
            workspace_path=workspace_path,
            pending_intent=pending_intent,
            feature_flags=dict(feature_flags or {}),
        )

    def system_prompt(self) -> str:
        parts = [self.objective.to_prompt()]
        if self.pending_intent:
            parts.append(self.pending_intent.to_prompt(self.objective.user_request))
        if self.recent_failures:
            parts.append(
                "## Recent Failures\n"
                + "\n".join(f"- {_compact_text(item, 220)}" for item in self.recent_failures[-3:])
                + "\nrule=change input/code/contract or explain blocker; do not retry unchanged."
            )
            learned = self._project_failure_lessons()
            if learned:
                parts.append(f"## Failure Lessons\n{_compact_text(learned, 700)}")
        if self.objective.mode is TaskMode.OPERATION_REPAIR and self.saw_operation_failure and not self.saw_operation_edit:
            parts.append(
                "## Repair Policy\n"
                "run_operation failed and no edit happened. Next: inspect/edit existing operation, then rerun. "
                "Do not bypass with execute_code/data conversion/map styling."
            )
        return "\n\n".join(parts)

    def capability_selection(self) -> CapabilitySelection:
        router = CapabilityRouter(
            workspace_path=self.workspace_path,
            max_skills=_feature_int(self.feature_flags, "capability_router_max_skills", 4),
            max_operations=_feature_int(self.feature_flags, "capability_router_max_operations", 4),
            max_evolution_assets=_feature_int(self.feature_flags, "capability_router_max_assets", 4),
            min_score=_feature_float(self.feature_flags, "capability_router_min_score", 2.0),
            cache_ttl_seconds=_feature_float(self.feature_flags, "capability_router_cache_ttl_seconds", 5.0),
        )
        base_packs = self.base_tool_packs()
        query = "\n".join(
            item for item in [
                self.objective.user_request,
                self.pending_intent.resolved_objective if self.pending_intent else "",
                *self.recent_failures[-3:],
            ]
            if item
        )
        return router.select(
            query,
            base_tool_packs=base_packs,
            allow_3d=bool(self.feature_flags.get("enable_3d")),
            force_tool_packs=self._forced_tool_packs(),
        )

    def tool_packs(self) -> set[str]:
        return set(self.capability_selection().tool_packs)

    def base_tool_packs(self) -> set[str]:
        # Keep profile-owned control tools available while the router adds
        # only task-relevant domain packs.
        packs: set[str] = {"core", "skill"}
        mode = self.objective.mode
        if mode is TaskMode.MAP_RENDERING:
            packs.update({"map", "style"})
        elif mode is TaskMode.DATA_ANALYSIS:
            packs.update({"data", "code"})
        elif mode is TaskMode.OPERATION_REPAIR:
            packs.update({"operation", "file", "code"})
        elif mode is TaskMode.OPERATION_RUN:
            packs.update({"operation", "file"})
        elif mode is TaskMode.WORKFLOW:
            packs.update({"workflow", "file", "code"})
        elif mode is TaskMode.WORKER:
            packs.update({"worker", "file"})
        return packs

    def _forced_tool_packs(self) -> set[str]:
        packs: set[str] = set()
        if self.objective.mode is TaskMode.OPERATION_REPAIR:
            packs.add("operation")
        elif self.objective.mode is TaskMode.OPERATION_RUN:
            packs.add("operation")
        elif self.objective.mode is TaskMode.WORKFLOW:
            packs.add("workflow")
        elif self.objective.mode is TaskMode.WORKER:
            packs.add("worker")
        elif self.objective.mode is TaskMode.MAP_RENDERING:
            packs.add("style")
        elif self.objective.mode is TaskMode.DATA_ANALYSIS:
            packs.add("data")
        return packs

    def _project_failure_lessons(self) -> str:
        if not self.workspace_path or not self.recent_failures:
            return ""
        query = "\n".join([self.objective.user_request, *self.recent_failures[-3:]])
        try:
            return FailureMemoryProjector(self.workspace_path).project(query, limit=4)
        except Exception:
            return ""

    def guard_tool_calls(self, tool_calls: list[dict[str, Any]]) -> GuardResult:
        blocked: dict[str, str] = {}
        for tool_call in tool_calls:
            call_id, name, arguments = _tool_call_parts(tool_call)
            if not call_id:
                continue
            reason = self._block_reason(name, arguments)
            if reason:
                blocked[call_id] = reason
        return GuardResult(blocked_call_ids=blocked)

    def observe_settlements(self, settlements: list[ToolSettlement]) -> ControlDecision:
        correction = ""
        for settlement in settlements:
            self.tool_history.append(settlement.name)
            if settlement.name in {"edit_operation", "edit_file", "write_file"} and not settlement.error:
                if self.objective.mode is TaskMode.OPERATION_REPAIR:
                    self.saw_operation_edit = True
                    # A repair action changes the object under test. Old
                    # failures are no longer evidence of an unchanged retry
                    # loop; the next run_operation is a legitimate validation.
                    self.recent_failures.clear()
            if settlement.name == "run_operation":
                self.saw_operation_run = True
                if settlement.error or _content_success(settlement.content) is False:
                    self.saw_operation_failure = True
                    failure = _summarize_failure(settlement)
                    self.recent_failures.append(failure)
                    correction = (
                        "## Runner Correction\n"
                        f"mode=operation_repair failed={_compact_text(failure, 260)}\n"
                        "next=get_operation(include_code=true)|validate_operation|edit_operation/edit_file; then rerun run_operation.\n"
                        "forbidden=standalone execute_code bypass."
                    )
            elif settlement.error:
                self.recent_failures.append(_summarize_failure(settlement))
            elif _content_success(settlement.content) is False:
                failure = _summarize_failure(settlement)
                self.recent_failures.append(failure)
                structured = _structured_tool_failure(settlement.content)
                if structured.get("do_not_retry_same_request") is True or structured.get("retryable") is False:
                    correction = (
                        "## Runner Correction\n"
                        f"tool={settlement.name} structured_failure={_compact_text(failure, 260)}\n"
                        "next=use expected schema/suggestion, change input materially, or explain blocker."
                    )
            elif (
                self.objective.mode is TaskMode.DATA_ANALYSIS
                and settlement.name in {"execute_code", "run_script_file"}
            ):
                self.analysis_code_successes += 1

            if (
                self.objective.mode is TaskMode.WORKER
                and settlement.name in {"start_worker", "start_dynamic_map_worker", "restart_worker", "wait_worker_update"}
                and not settlement.error
                and _worker_runtime_is_healthy(settlement.content)
            ):
                return ControlDecision(
                    corrective_message=(
                        "## Runner Control\n"
                        "worker=healthy; stop inspection/rewrites/restarts; final answer with worker id, health, rendering."
                    ),
                    force_final_reason="worker_running_verified",
                )

            capability = capability_for(settlement.name)
            if capability.domain == "worker":
                self.saw_worker_tool = True
            if capability.domain == "workflow":
                self.saw_workflow_tool = True
            if capability.domain == "map":
                self.saw_map_tool = True

            if settlement.metadata.get("runner_guard_blocked"):
                self.deviation_count += 1
                correction = (
                    "## Runner Correction\n"
                    f"blocked_tool={settlement.name}\n"
                    f"objective={_compact_text(self.objective.user_request, 260)}\n"
                    f"reason={_compact_text(str(settlement.metadata.get('runner_guard_reason') or ''), 260)}\n"
                    "next=choose directly relevant tool."
                )

            if (
                self.objective.mode is TaskMode.DATA_ANALYSIS
                and not settlement.error
                and settlement.name in {"execute_code", "run_script_file"}
                and self.analysis_code_successes >= 2
                and not _requests_visual_or_file_deliverable(self.objective.user_request)
                and _looks_like_sufficient_analysis_output(settlement.content)
            ):
                return ControlDecision(
                    corrective_message=(
                        "## Runner Control\n"
                        "analysis_answer_ready=true; stop exploration; no extra reports/charts/files/layers unless asked; final answer from results."
                    ),
                    force_final_reason="analysis_answer_ready",
                )

        if correction:
            if self.deviation_count >= 2:
                return ControlDecision(
                    corrective_message=correction
                    + "\nsecond_deviation=true; repair target object or explain blocker.",
                    force_final_reason=None,
                )
            return ControlDecision(corrective_message=correction)

        anomaly = LoopAnomalyDetector.detect(self.tool_history, self.recent_failures)
        if anomaly:
            self.deviation_count += 1
            return ControlDecision(
                corrective_message=(
                    "## Runner Correction\n"
                    f"anomaly={anomaly.kind} message={_compact_text(anomaly.message, 220)}\n"
                    f"objective={_compact_text(self.objective.user_request, 260)}\n"
                    "next=inspect current/failed object, repair input/contract/code, or explain blocker."
                )
            )
        return ControlDecision()

    def _block_reason(self, tool_name: str, arguments: dict[str, Any]) -> str:
        capability = capability_for(tool_name)
        if self.pending_intent and self.pending_intent.kind == "confirm_map_load":
            if tool_name in set(self.pending_intent.avoid_tools) and not self.saw_map_tool:
                return (
                    "the user only confirmed the previous offer to load results on the map; "
                    "use map loading/styling/zoom tools before rerunning analysis or scanning files"
                )
        if self.objective.mode is TaskMode.OPERATION_REPAIR:
            if capability.side_effect == "map":
                return "operation repair mode does not permit map rendering/styling side effects before the operation is repaired"
            if self.saw_operation_failure and not self.saw_operation_edit:
                if tool_name in OPERATION_REPAIR_BYPASS_TOOLS or capability.side_effect == "map":
                    return "run_operation failed; repair the existing operation before using one-off code, data conversion, or map mutation tools"
        if self.objective.mode is TaskMode.WORKER:
            if tool_name in ONE_SHOT_EXECUTION_TOOLS and not self.saw_worker_tool:
                return "worker mode requires worker lifecycle tools for continuous/background behavior instead of one-shot code execution"
        if self.objective.mode is TaskMode.WORKFLOW:
            if capability.side_effect in {"map", "worker"} and not self.saw_workflow_tool:
                return "workflow mode should first use workflow tools or inspect the workflow before direct map/worker side effects"
        if self.objective.mode is TaskMode.DATA_ANALYSIS:
            if capability.side_effect == "map" and not _requests_visual_or_file_deliverable(self.objective.user_request):
                return (
                    "the current request asks for an analysis/opinion, not map rendering; "
                    "answer from the analysis results instead of creating or styling map layers"
                )
        if tool_name == "bash" and self._recent_osm_tool_blocker() and _bash_looks_like_osm_network_call(arguments):
            return (
                "osm_call already reported an OSM/Overpass blocker in this turn; "
                "do not bypass the dedicated OSM tool with shell/curl/wget network calls. "
                "Use corrected osm_call parameters, a materially smaller query, cached/local data, or explain the external blocker."
            )
        return ""

    def _recent_osm_tool_blocker(self) -> bool:
        for failure in self.recent_failures[-4:]:
            text = failure.lower()
            if "osm_call" in text and (
                "osm_network_error" in text
                or "osm_network_timeout" in text
                or "osm_invalid_params" in text
                or "overpass" in text
                or "nominatim" in text
            ):
                return True
        return False


def infer_task_mode(user_message: str) -> TaskMode:
    text = (user_message or "").lower()
    if "workflow" in text or "工作流" in text:
        return TaskMode.WORKFLOW
    if "worker" in text or "驻守" in text or "后台" in text:
        return TaskMode.WORKER
    if "operation" in text or "操作" in text:
        if any(token in text for token in ("修", "改", "失败", "报错", "错误", "fix", "repair", "绕过", "bypass")):
            return TaskMode.OPERATION_REPAIR
        return TaskMode.OPERATION_RUN
    if any(token in text for token in ("图层", "地图", "渲染", "样式", "颜色", "缩放", "layer", "map", "style")):
        return TaskMode.MAP_RENDERING
    if any(token in text for token in ("分析", "统计", "评价", "评估", "如何", "怎么样", "觉得", "通达度", "可达性", "绘制", "图表", "chart", "plot", "analy")):
        return TaskMode.DATA_ANALYSIS
    return TaskMode.GENERAL


def _tool_call_parts(tool_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    call_id = str(tool_call.get("id") or "")
    fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(fn.get("name") or "")
    raw_args = fn.get("arguments") or "{}"
    try:
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception:
        parsed = {}
    return call_id, name, parsed if isinstance(parsed, dict) else {}


def _content_success(content: str) -> bool | None:
    try:
        data = json.loads(content)
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("success"), bool):
        return bool(data["success"])
    return None


def _summarize_failure(settlement: ToolSettlement) -> str:
    error = settlement.error or ""
    if not error:
        try:
            data = json.loads(settlement.content)
            if isinstance(data, dict):
                error = str(data.get("error") or data.get("message") or "")
        except Exception:
            error = ""
    error = _compact_text(error, 260) or "unknown error"
    return f"{settlement.name}({settlement.call_id}) failed: {error}"


def _compact_text(text: str, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    head = max(40, max_chars // 2)
    tail = max(0, max_chars - head - 22)
    return compact[:head].rstrip() + " ... " + compact[-tail:].lstrip()


def _feature_int(flags: dict[str, Any], key: str, default: int) -> int:
    if key not in flags:
        return default
    try:
        return int(flags[key])
    except (TypeError, ValueError):
        return default


def _feature_float(flags: dict[str, Any], key: str, default: float) -> float:
    if key not in flags:
        return default
    try:
        return float(flags[key])
    except (TypeError, ValueError):
        return default


def _structured_tool_failure(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except Exception:
        return {}
    if isinstance(data, dict) and data.get("success") is False:
        return data
    return {}


def _worker_runtime_is_healthy(content: str) -> bool:
    try:
        data = json.loads(content)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if str(data.get("status") or "").lower() != "running":
        return False
    health = data.get("health")
    if isinstance(health, dict) and health.get("ok") is True:
        return True
    startup = data.get("startup_check")
    return isinstance(startup, dict) and startup.get("state") == "ok"


def _failure_signature(failure: str) -> str:
    """Return a stable repeated-failure signature.

    Ignore volatile call ids, but keep the error shape. Retrying the same tool
    after a repair may legitimately produce a different error; that should not
    be treated as the same loop.
    """
    tool = failure.split("(", 1)[0].strip()
    detail = failure.split(" failed: ", 1)[1] if " failed: " in failure else failure
    detail = " ".join(detail.split()).strip()
    if not tool:
        return ""
    if not detail:
        return tool
    return f"{tool}: {detail[:120]}"


OPERATION_REPAIR_BYPASS_TOOLS = {
    "execute_code",
    "run_script_file",
    "bash",
    "csv_to_geojson",
} | tools_with_side_effect("map")

ONE_SHOT_EXECUTION_TOOLS = {
    "execute_code",
    "run_script_file",
    "bash",
}


def _bash_looks_like_osm_network_call(arguments: dict[str, Any]) -> bool:
    command = str(arguments.get("command") or "").lower()
    if not command:
        return False
    network_markers = ("curl", "wget", "http://", "https://")
    osm_markers = ("overpass", "nominatim", "openstreetmap", "osm.org", "kumi.systems")
    return any(marker in command for marker in network_markers) and any(marker in command for marker in osm_markers)


def _requests_visual_or_file_deliverable(user_request: str) -> bool:
    text = (user_request or "").lower()
    markers = (
        "画图",
        "绘图",
        "图表",
        "图片",
        "报告",
        "保存",
        "导出",
        "生成文件",
        "地图",
        "图层",
        "可视化",
        "展示到地图",
        "加载到地图",
        "加到地图",
        "plot",
        "chart",
        "figure",
        "report",
        "export",
        "save",
        "map",
        "layer",
        "visual",
    )
    return any(marker in text for marker in markers)


def _looks_like_sufficient_analysis_output(content: str) -> bool:
    text = " ".join((content or "").split())
    if len(text) < 160:
        return False
    conclusion_markers = (
        "综合评价",
        "总体评级",
        "评级",
        "结论",
        "建议",
        "总分",
        "得分",
        "score",
        "rating",
        "conclusion",
        "summary",
    )
    evidence_markers = (
        "道路",
        "路网",
        "公交",
        "地铁",
        "距离",
        "覆盖",
        "密度",
        "统计",
        "count",
        "mean",
        "distance",
    )
    return (
        sum(1 for marker in conclusion_markers if marker in text) >= 2
        and sum(1 for marker in evidence_markers if marker in text) >= 2
    )


__all__ = [
    "ControlDecision",
    "GuardResult",
    "LoopAnomaly",
    "LoopAnomalyDetector",
    "RuntimeControl",
    "TaskMode",
    "TurnObjective",
    "infer_task_mode",
]
