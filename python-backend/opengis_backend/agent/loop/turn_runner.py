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
    continuation: str = ""

    def log(self) -> None:
        logger.info(
            "[LOOP-TURN] iteration=%d code_steps=%d tool_steps=%d "
            "messages=%d est_request_tokens=%d tools=%d/%d tool_reason=%s "
            "context_build=%.0fms llm=%.0fms tool=%.0fms "
            "response_chars=%d tool_calls=%d continuation=%s",
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
    ) -> ProviderTurnResult:
        t0 = time.monotonic()
        materialized_tools = (
            self.tool_materializer.materialize(messages)
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
    ) -> None:
        self.context = context
        self.tool_runtime = tool_runtime
        self.progress_callback = progress_callback
        self.on_tool_start = on_tool_start
        self.on_tool_result = on_tool_result

    def settle_all(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        streamed_tool_code: dict[int, dict[str, Any]] | None = None,
    ) -> list[ToolSettlement]:
        settlements: list[ToolSettlement] = []
        streamed_tool_code = streamed_tool_code or {}
        for tool_index, tc in enumerate(tool_calls):
            settlements.append(
                self._settle_one(
                    tool_index=tool_index,
                    tool_call=tc,
                    streamed_tool_code=streamed_tool_code,
                )
            )
        return settlements

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
                self.progress_callback("tool_call", f"Calling {tool_name}...")
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
    "tool_counts_as_code_step",
]
