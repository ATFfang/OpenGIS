"""Turn-level helpers for the function-call agent loop.

This module is the migration boundary toward an OpenCode-style runner:
one provider turn produces assistant text/tool calls, local tool calls are
settled through a single path, and the loop decides whether to continue.
The current AgentLoop still owns streaming UI callbacks and provider retry,
but tool settlement and telemetry no longer live inline in the loop body.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.llm import LLMResponse
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer
from opengis_backend.agent.execution.tool_runtime import ToolRuntime, parse_tool_arguments
from opengis_backend.agent.execution.tool_runtime import validate_execute_code_payload

logger = logging.getLogger(__name__)

CODE_EXECUTION_TOOLS = {"execute_code", "run_script_file"}


def tool_intent_progress(tool_name: str, arguments: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return a compact, user-facing progress stage for a tool call.

    This is intentionally not chain-of-thought. It exposes the next concrete
    action so the UI does not look idle while the model immediately uses
    function calls.
    """
    args = arguments or {}
    target = _tool_target_hint(args)

    explicit: dict[str, tuple[str, str]] = {
        "execute_code": ("executing_code", "运行 Python 代码"),
        "run_script_file": ("executing_code", "运行已保存脚本"),
        "list_layers": ("tool_intent", "读取当前地图图层"),
        "get_layer": ("tool_intent", "查看图层详情"),
        "query_features": ("tool_intent", "查询图层要素"),
        "fly_to": ("tool_intent", "调整地图视角"),
        "set_map_camera": ("tool_intent", "调整地图相机"),
        "enter_3d_view": ("tool_intent", "切换到三维视角"),
        "exit_3d_view": ("tool_intent", "退出三维视角"),
        "zoom_to_layer": ("tool_intent", "缩放到目标图层"),
        "set_basemap_visibility": ("tool_intent", "切换底图显示"),
        "get_map_state": ("tool_intent", "读取地图状态"),
        "add_layer": ("loading_geodata", "加载要素图层"),
        "add_raster": ("loading_raster", "加载栅格图层"),
        "remove_layer": ("tool_intent", "移除地图图层"),
        "csv_to_geojson": ("loading_data", "转换表格为空间数据"),
        "update_layer_style": ("tool_intent", "更新图层样式"),
        "set_graduated_style": ("tool_intent", "设置数值分级符号"),
        "set_categorized_style": ("tool_intent", "设置分类符号"),
        "set_extrusion_style": ("tool_intent", "设置三维拉伸"),
        "set_layer_visual_variables": ("tool_intent", "设置图层视觉变量"),
        "set_layer_filter": ("tool_intent", "设置图层过滤"),
        "set_layer_label": ("tool_intent", "设置图层标注"),
        "highlight_features": ("tool_intent", "高亮要素"),
        "set_layer_order": ("tool_intent", "调整图层顺序"),
        "update_legend_spec": ("tool_intent", "更新图例配置"),
        "read_file": ("tool_intent", "读取文件"),
        "write_file": ("tool_intent", "写入文件"),
        "edit_file": ("tool_intent", "编辑文件"),
        "file_exists": ("tool_intent", "检查文件是否存在"),
        "list_directory": ("tool_intent", "查看目录"),
        "glob": ("tool_intent", "搜索文件路径"),
        "grep": ("tool_intent", "搜索文件内容"),
        "bash": ("tool_intent", "运行 Shell 命令"),
        "webfetch": ("tool_intent", "抓取网页内容"),
        "websearch": ("tool_intent", "搜索网页信息"),
        "save_plot": ("saving_results", "保存图表"),
        "list_operations": ("tool_intent", "读取 Operation 列表"),
        "get_operation": ("tool_intent", "查看 Operation 定义"),
        "validate_operation": ("tool_intent", "校验 Operation 契约"),
        "run_operation": ("tool_intent", "运行 Operation"),
        "create_operation": ("tool_intent", "创建 Operation"),
        "edit_operation": ("tool_intent", "编辑 Operation"),
        "promote_script_to_operation": ("tool_intent", "沉淀脚本为 Operation"),
        "create_workflow": ("tool_intent", "创建 Workflow"),
        "update_plan": ("tool_intent", "更新执行计划"),
        "run_subagent": ("tool_intent", "启动 Subagent"),
        "run_subagents": ("tool_intent", "启动多个 Subagent"),
        "start_worker": ("tool_intent", "启动 Worker"),
        "start_dynamic_map_worker": ("tool_intent", "启动动态地图 Worker"),
        "get_worker": ("tool_intent", "查看 Worker 状态"),
        "wait_worker_update": ("tool_intent", "等待 Worker 更新"),
        "restart_worker": ("tool_intent", "重启 Worker"),
        "list_workers": ("tool_intent", "读取 Worker 列表"),
        "pause_worker": ("tool_intent", "暂停 Worker"),
        "delete_worker": ("tool_intent", "删除 Worker"),
        "debug_agent_context": ("tool_intent", "检查 Agent 上下文"),
        "load_skill": ("tool_intent", "加载 Skill"),
        "update_user_instructions": ("tool_intent", "更新用户偏好"),
    }
    if tool_name in explicit:
        stage, detail = explicit[tool_name]
        return stage, _append_target_hint(detail, target)

    if tool_name.startswith("layout_"):
        return "tool_intent", _append_target_hint("更新制图画布", target)
    if tool_name.startswith("academic_"):
        return "tool_intent", "处理学术文本"
    if tool_name.startswith("export_") or tool_name.endswith("_pdf"):
        return "saving_results", _append_target_hint("导出结果", target)
    if tool_name.startswith("write_report") or tool_name.startswith("interactive_snapshot"):
        return "tool_intent", _append_target_hint("生成报告内容", target)
    return "tool_intent", _append_target_hint(f"调用工具 {tool_name}", target)


def _tool_target_hint(arguments: dict[str, Any]) -> str:
    for key in (
        "layer_id",
        "operation_id",
        "worker_id",
        "workflow_id",
        "path",
        "file_path",
        "raster_path",
        "geojson_path",
        "script_path",
        "url",
    ):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _compact_target(value.strip())
    return ""


def _compact_target(value: str, limit: int = 36) -> str:
    compact = value.replace("\\", "/").rstrip("/").split("/")[-1] or value
    compact = compact.replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _append_target_hint(detail: str, target: str) -> str:
    return f"{detail} · {target}" if target else detail


@dataclass
class ToolSettlement:
    call_id: str
    name: str
    arguments: dict[str, Any]
    content: str
    error: str | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    counts_as_code_step: bool = False


@dataclass
class LoopTurnTelemetry:
    iteration: int
    code_steps: int
    tool_steps: int
    message_count: int
    context_tokens: int
    tool_schema_count: int
    context_build_ms: float = 0.0
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    response_chars: int = 0
    tool_call_count: int = 0
    tool_schema_total: int = 0
    tool_schema_reason: str = ""
    request_pressure: str = ""
    continuation: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        total_input = self.prompt_tokens + self.cache_read_tokens + self.cache_creation_tokens
        if total_input <= 0:
            return 0.0
        return min(1.0, self.cached_tokens / total_input)

    def log(self) -> None:
        logger.info(
            "[LOOP-TURN] iteration=%d code_steps=%d tool_steps=%d "
            "messages=%d est_request_tokens=%d tools=%d/%d tool_reason=%s "
            "context_build=%.0fms llm=%.0fms tool=%.0fms "
            "response_chars=%d tool_calls=%d pressure=%s "
            "prompt_tokens=%d cached_tokens=%d cache_write=%d cache_hit=%.1f%% continuation=%s",
            self.iteration,
            self.code_steps,
            self.tool_steps,
            self.message_count,
            self.context_tokens,
            self.tool_schema_count,
            self.tool_schema_total or self.tool_schema_count,
            self.tool_schema_reason or "-",
            self.context_build_ms,
            self.llm_ms,
            self.tool_ms,
            self.response_chars,
            self.tool_call_count,
            self.request_pressure or "-",
            self.prompt_tokens,
            self.cached_tokens,
            self.cache_creation_tokens,
            self.cache_hit_rate * 100.0,
            self.continuation or "-",
        )


@dataclass
class ProviderTurnResult:
    response: Any
    response_text: str
    tool_calls: list[dict[str, Any]] | None
    duration_ms: float
    streamed_tool_code: dict[int, dict[str, Any]]
    draft_text_parts: list[str] = field(default_factory=list)
    tool_schema_count: int = 0
    tool_schema_total: int = 0
    tool_schema_reason: str = ""


@dataclass(frozen=True)
class ContinuationDecision:
    action: str  # "accept" | "nudge"
    reason: str

    @property
    def should_nudge(self) -> bool:
        return self.action == "nudge"

    @property
    def should_accept(self) -> bool:
        return self.action == "accept"


@dataclass
class ProviderTurnCallbacks:
    on_code_start: Callable[[int], None] | None = None
    on_code_delta: Callable[[int, str], None] | None = None
    on_code_end: Callable[[int], None] | None = None


CONTINUATION_MARKERS = (
    "next i will",
    "i will now",
    "i will",
    "i'll",
    "next i",
    "下一步",
    "接下来",
    "继续",
    "还需要",
    "尚未",
    "将会",
    "需要先",
    "not complete",
    "need to",
    "need to call",
    "need to run",
)

COMPLETION_MARKERS = (
    "done",
    "completed",
    "finished",
    "successfully",
    "all set",
    "已完成",
    "已经完成",
    "处理完成",
    "执行完成",
    "已按要求完成",
    "完成了",
)

ACTION_COMPLETION_MARKERS = (
    "已缩放到",
    "已定位到",
    "已飞行到",
    "已切换",
    "已打开",
    "已关闭",
    "已加载",
    "已添加",
    "已移除",
    "已删除",
    "已更新",
    "已设置",
    "已保存",
    "已导出",
    "已渲染",
    "已绘制",
    "已生成",
    "已显示",
    "已隐藏",
    "已创建",
    "已启动",
    "已停止",
    "已暂停",
    "已恢复",
    "zoomed to",
    "switched to",
    "loaded",
    "added",
    "removed",
    "updated",
    "saved",
    "exported",
    "rendered",
    "generated",
    "created",
    "started",
    "stopped",
)


def looks_like_completion(text: str) -> bool:
    """Conservative detector for text that likely ends a local turn."""
    if not text or not text.strip():
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in CONTINUATION_MARKERS):
        return False
    return any(marker in lowered for marker in COMPLETION_MARKERS)


def looks_like_action_completion(text: str) -> bool:
    """Detect concise post-tool confirmations without requiring "completed"."""
    if not text or not text.strip():
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in CONTINUATION_MARKERS):
        return False
    return any(marker in lowered for marker in ACTION_COMPLETION_MARKERS)


def decide_text_continuation(
    text: str,
    *,
    code_steps: int = 0,
    tool_steps: int = 0,
    max_work_steps: int | None = None,
    nudged: bool = False,
    accept_after_any_tool: bool = False,
    accept_text_without_tools: bool = False,
) -> ContinuationDecision:
    """Decide whether a text-only model reply completes or needs a tool nudge."""
    stripped = (text or "").strip()
    if not stripped:
        return ContinuationDecision("nudge", "empty_text")

    total_work_steps = code_steps + tool_steps
    if accept_after_any_tool and (total_work_steps > 0 or nudged):
        return ContinuationDecision("accept", "tool_or_nudge_seen")

    if accept_text_without_tools:
        return ContinuationDecision("accept", "text_allowed_without_tools")

    if code_steps > 0 and looks_like_completion(stripped):
        return ContinuationDecision("accept", "completion_marker_after_code")

    if total_work_steps > 0:
        if looks_like_action_completion(stripped):
            return ContinuationDecision("accept", "action_completion_after_tool")
        if (max_work_steps is None or total_work_steps <= max_work_steps) and not nudged:
            return ContinuationDecision("nudge", "mid_task_text_without_tool")
        return ContinuationDecision("accept", "already_nudged_or_step_limit")

    if looks_like_completion(stripped):
        return ContinuationDecision("accept", "completion_marker")

    return ContinuationDecision("nudge", "no_tool_work_yet")


class ProviderTurnCaller:
    """Call the LLM once and collect draft text/tool-input fragments."""

    def __init__(
        self,
        *,
        llm_call: Callable[..., Any],
        tool_schemas: list[dict] | None,
        tool_materializer: ToolMaterializer | None = None,
        retryable_exceptions: tuple[type[BaseException], ...],
        max_retries: int,
        base_delay: float,
        progress_callback: Callable[[str, str], None] | None = None,
        interrupted: Callable[[], bool] | None = None,
        logger_prefix: str = "LOOP",
    ) -> None:
        self.llm_call = llm_call
        self.tool_schemas = tool_schemas
        self.tool_materializer = tool_materializer
        self.retryable_exceptions = retryable_exceptions
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.progress_callback = progress_callback
        self.interrupted = interrupted or (lambda: False)
        self.logger_prefix = logger_prefix

    def call(
        self,
        messages: list[dict],
        *,
        callbacks: ProviderTurnCallbacks,
        code_step_for_tool: Callable[[int], int],
        retry_detail: str = "Connection error",
        prompt_cache_key: str | None = None,
        prompt_cache_metadata: dict[str, Any] | None = None,
    ) -> ProviderTurnResult:
        t0 = time.monotonic()
        materialized_tools = (
            self.tool_materializer.materialize()
            if self.tool_materializer is not None
            else None
        )
        active_tool_schemas = (
            materialized_tools.schemas
            if materialized_tools is not None
            else self.tool_schemas
        )
        streamed_tool_code: dict[int, dict[str, Any]] = {}

        def _on_tool_delta(tool_index: int, tool_name: str, payload: dict[str, Any]) -> None:
            if tool_name != "execute_code":
                return
            code = payload.get("code")
            if not isinstance(code, str):
                return
            state = streamed_tool_code.setdefault(
                tool_index,
                {"step": code_step_for_tool(tool_index), "length": 0, "open": False, "invalid": False},
            )
            if state.get("invalid"):
                return
            if validate_execute_code_payload(code):
                state["invalid"] = True
                return
            if not state["open"]:
                if callbacks.on_code_start:
                    try:
                        callbacks.on_code_start(int(state["step"]))
                    except Exception:
                        logger.exception("on_code_start failed")
                state["open"] = True
            previous_length = int(state["length"])
            if len(code) > previous_length and callbacks.on_code_delta:
                try:
                    callbacks.on_code_delta(int(state["step"]), code[previous_length:])
                except Exception:
                    logger.exception("on_code_delta failed")
                state["length"] = len(code)

        draft_text_parts: list[str] = []

        def _on_llm_delta(piece: str) -> None:
            # Provider text deltas are draft fragments until the turn is
            # settled. The loop policy decides later whether they become a
            # visible assistant text part or remain only model-visible
            # assistant content owned by tool calls.
            draft_text_parts.append(piece)

        response: LLMResponse | None = None
        for retry_attempt in range(self.max_retries + 1):
            try:
                response = self.llm_call(
                    messages,
                    on_delta=_on_llm_delta,
                    on_tool_delta=_on_tool_delta,
                    tools=active_tool_schemas,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_metadata=prompt_cache_metadata,
                )
                break
            except self.retryable_exceptions as exc:
                if self.interrupted():
                    logger.debug("[%s] Interrupted during retry, not retrying.", self.logger_prefix)
                    raise
                if retry_attempt >= self.max_retries:
                    logger.error(
                        "[%s] LLM call failed after %d retries: %s(%s)",
                        self.logger_prefix,
                        self.max_retries,
                        type(exc).__name__,
                        exc,
                    )
                    raise
                delay = self.base_delay * (2 ** retry_attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "[%s-RETRY] LLM call attempt %d/%d failed (%s: %s), retrying in %.1fs...",
                    self.logger_prefix,
                    retry_attempt + 1,
                    self.max_retries,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                if self.progress_callback:
                    try:
                        self.progress_callback("retrying", f"{retry_detail}, retrying ({retry_attempt + 1}/{self.max_retries})...")
                    except Exception:
                        logger.exception("progress_callback failed on retry")
                time.sleep(delay)

        duration_ms = (time.monotonic() - t0) * 1000
        if response is None:
            raise RuntimeError("LLM call did not return a response")
        response_text = response.content or ""
        tool_calls = response.tool_calls

        for state in streamed_tool_code.values():
            if state.get("open") and callbacks.on_code_end:
                try:
                    callbacks.on_code_end(int(state["step"]))
                except Exception:
                    logger.exception("on_code_end failed")

        return ProviderTurnResult(
            response=response,
            response_text=response_text,
            tool_calls=tool_calls,
            duration_ms=duration_ms,
            streamed_tool_code=streamed_tool_code,
            draft_text_parts=draft_text_parts,
            tool_schema_count=len(active_tool_schemas or []),
            tool_schema_total=(
                materialized_tools.total_count
                if materialized_tools is not None
                else len(active_tool_schemas or [])
            ),
            tool_schema_reason=materialized_tools.reason if materialized_tools is not None else "static",
        )


def tool_counts_as_code_step(tool_name: str, metadata: dict[str, Any] | None = None) -> bool:
    """Return whether a tool should advance the code execution budget."""
    if tool_name in CODE_EXECUTION_TOOLS:
        return True
    if metadata and metadata.get("rerun_script"):
        return True
    return False


class ToolCallSettler:
    """Execute and persist one batch of model-emitted tool calls."""

    def __init__(
        self,
        *,
        context: ContextManager,
        tool_runtime: ToolRuntime | None,
        progress_callback: Callable[[str, str], None] | None = None,
        on_tool_start: Callable[[str, dict, str], None] | None = None,
        on_tool_result: Callable[..., Any] | None = None,
        settlement_identity: dict[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.tool_runtime = tool_runtime
        self.progress_callback = progress_callback
        self.on_tool_start = on_tool_start
        self.on_tool_result = on_tool_result
        self.settlement_identity = dict(settlement_identity or {})

    def settle_all(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        streamed_tool_code: dict[int, dict[str, Any]] | None = None,
        blocked_call_ids: dict[str, str] | None = None,
    ) -> list[ToolSettlement]:
        settlements: list[ToolSettlement] = []
        streamed_tool_code = streamed_tool_code or {}
        blocked_call_ids = blocked_call_ids or {}
        for tool_index, tc in enumerate(tool_calls):
            tc_id = str(tc.get("id", ""))
            if tc_id in blocked_call_ids:
                settlements.append(
                    self._settle_blocked(
                        tool_call=tc,
                        reason=blocked_call_ids[tc_id],
                    )
                )
                continue
            settlements.append(
                self._settle_one(
                    tool_index=tool_index,
                    tool_call=tc,
                    streamed_tool_code=streamed_tool_code,
                )
            )
        return settlements

    def _settle_blocked(
        self,
        *,
        tool_call: dict[str, Any],
        reason: str,
    ) -> ToolSettlement:
        tc_id = str(tool_call.get("id", ""))
        func = tool_call.get("function", {}) if isinstance(tool_call.get("function"), dict) else {}
        tool_name = str(func.get("name", ""))
        arguments = parse_tool_arguments(func.get("arguments", "{}"))
        content = json.dumps(
            {
                "success": False,
                "error": "runner_guard_blocked",
                "reason": reason,
                "retry": "Choose a tool that directly serves the current turn objective.",
            },
            ensure_ascii=False,
        )
        metadata = {
            "runner_guard_blocked": True,
            "runner_guard_reason": reason,
            **self.settlement_identity,
            "tool_call_id": tc_id,
        }
        logger.warning("TOOL BLOCKED: %s(%s) -> %s", tool_name, tc_id, reason)
        self.context.add_tool_result(
            tc_id,
            tool_name,
            content,
            meta=metadata,
        )
        return ToolSettlement(
            call_id=tc_id,
            name=tool_name,
            arguments=arguments,
            content=content,
            error="runner_guard_blocked",
            duration_ms=0.0,
            metadata=metadata,
            counts_as_code_step=False,
        )

    def _settle_one(
        self,
        *,
        tool_index: int,
        tool_call: dict[str, Any],
        streamed_tool_code: dict[int, dict[str, Any]],
    ) -> ToolSettlement:
        tc_id = str(tool_call.get("id", ""))
        func = tool_call.get("function", {}) if isinstance(tool_call.get("function"), dict) else {}
        tool_name = str(func.get("name", ""))
        arguments = parse_tool_arguments(func.get("arguments", "{}"))

        try:
            args_preview = json.dumps(arguments, ensure_ascii=False)[:200]
        except Exception:
            args_preview = repr(arguments)[:200]
        logger.info("TOOL CALL: %s(%s)", tool_name, args_preview)

        if self.progress_callback:
            try:
                stage, detail = tool_intent_progress(tool_name, arguments)
                self.progress_callback(stage, detail)
            except Exception:
                logger.exception("progress_callback failed for tool start")

        if self.on_tool_start:
            try:
                self.on_tool_start(tool_name, arguments, tc_id)
            except Exception:
                logger.exception("on_tool_start failed")

        t0 = time.monotonic()
        if self.tool_runtime is None:
            result_content = '{"success": false, "error": "Tool runtime not configured"}'
            result_error = "Tool runtime not configured"
            result_ms = 0.0
            result_metadata: dict[str, Any] = {}
        else:
            tool_result = self.tool_runtime.execute(tool_name, arguments)
            result_content = tool_result.content
            result_error = tool_result.error
            result_ms = tool_result.duration_ms
            result_metadata = dict(tool_result.metadata or {})
            if tool_name == "execute_code":
                stream_state = streamed_tool_code.get(tool_index)
                if stream_state is not None:
                    result_metadata["code_step"] = int(stream_state["step"])

        if not result_ms:
            result_ms = (time.monotonic() - t0) * 1000
        result_metadata.update(self.settlement_identity)
        result_metadata["tool_call_id"] = tc_id

        if self.on_tool_result:
            try:
                updated_metadata = self.on_tool_result(
                    tool_name,
                    result_content,
                    result_error,
                    result_ms,
                    tc_id,
                    result_metadata,
                )
                if isinstance(updated_metadata, dict):
                    result_metadata.update(updated_metadata)
            except Exception:
                logger.exception("on_tool_result failed")

        if tool_name == "execute_code" and result_metadata:
            script_path = result_metadata.get("script_path") or result_metadata.get("script_abs_path")
            if script_path:
                persisted_note = (
                    "\n\n[script] Persisted script path: "
                    f"{script_path}\n"
                    "If this code failed or needs refinement, read this file and patch it "
                    "with edit_file, then call run_script_file(script_path=...) instead "
                    "of creating a near-duplicate script."
                )
                result_content = f"{result_content}{persisted_note}"

        self.context.add_tool_result(
            tc_id,
            tool_name,
            result_content,
            meta=result_metadata,
        )
        self.context.prune_tool_results()

        return ToolSettlement(
            call_id=tc_id,
            name=tool_name,
            arguments=arguments,
            content=result_content,
            error=result_error,
            duration_ms=result_ms,
            metadata=result_metadata,
            counts_as_code_step=tool_counts_as_code_step(tool_name, result_metadata),
        )


__all__ = [
    "CODE_EXECUTION_TOOLS",
    "ContinuationDecision",
    "LoopTurnTelemetry",
    "ProviderTurnCallbacks",
    "ProviderTurnCaller",
    "ProviderTurnResult",
    "ToolCallSettler",
    "ToolSettlement",
    "decide_text_continuation",
    "looks_like_action_completion",
    "looks_like_completion",
    "tool_intent_progress",
    "tool_counts_as_code_step",
]
