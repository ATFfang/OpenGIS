"""Shared execution kernel for function-call agent loops.

The kernel owns mechanics that should not be duplicated by loop variants:
context compression, message projection, tool materialization, one provider
turn, tool settlement, and telemetry. AgentLoop and WorkflowLoop keep policy:
when to nudge, when to accept text, and how to summarize.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.execution.tool_materializer import (
    ToolMaterialization,
    ToolMaterializer,
    format_active_tool_prompt,
)
from opengis_backend.agent.execution.tool_runtime import ToolRuntime
from opengis_backend.agent.loop.turn_runner import (
    LoopTurnTelemetry,
    ProviderTurnCallbacks,
    ProviderTurnCaller,
    ProviderTurnResult,
    ToolCallSettler,
    ToolSettlement,
)

logger = logging.getLogger(__name__)


@dataclass
class LoopKernelHooks:
    on_thought_delta: Callable[[str], None] | None = None
    on_code_start: Callable[[int], None] | None = None
    on_code_delta: Callable[[int, str], None] | None = None
    on_code_end: Callable[[int], None] | None = None
    on_reasoning_start: Callable[[int], None] | None = None
    on_reasoning_end: Callable[[int], None] | None = None
    on_tool_start: Callable[[str, dict, str], None] | None = None
    on_tool_result: Callable[..., Any] | None = None


@dataclass
class LoopTurnRequest:
    iteration: int
    code_steps: int
    tool_steps: int
    system_prompt: str
    text_code_step: int
    code_step_for_tool: Callable[[int], int]
    user_instructions: str | None = None
    exclude_workflow_context: bool = False
    progress_stage: str = "calling_llm"
    progress_detail: str = "Calling LLM..."
    reasoning_round_seq: int | None = None
    enable_reasoning_lifecycle: bool = False
    retry_detail: str = "Connection error"
    logger_prefix: str = "LOOP"
    scope: str | None = None
    assistant_tool_scope_kind: str = "tool_calls"
    tool_result_scope_kind: str = "tool_result"
    compress_context: bool = True
    tool_progress_label: str | None = None
    force_all_tools: bool = False


@dataclass
class LoopTurnOutcome:
    provider_result: ProviderTurnResult
    updated_reasoning_round: int | None
    settlements: list[ToolSettlement]
    telemetry: LoopTurnTelemetry
    materialization: ToolMaterialization | None

    @property
    def response_text(self) -> str:
        return self.provider_result.response_text

    @property
    def tool_calls(self) -> list[dict[str, Any]] | None:
        return self.provider_result.tool_calls

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.provider_result.tool_calls)


class LoopKernel:
    """Run one provider/tool turn for a loop policy."""

    def __init__(
        self,
        *,
        llm_call: Callable[..., Any],
        context: ContextManager,
        tool_runtime: ToolRuntime | None,
        tool_schemas: list[dict] | None,
        streaming_parser_factory: Callable[..., Any],
        retryable_exceptions: tuple[type[BaseException], ...],
        max_retries: int,
        base_delay: float,
        tool_materializer: ToolMaterializer | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
        interrupted: Callable[[], bool] | None = None,
        hooks: LoopKernelHooks | None = None,
    ) -> None:
        self.llm_call = llm_call
        self.context = context
        self.tool_runtime = tool_runtime
        self.tool_schemas = list(tool_schemas or [])
        self.streaming_parser_factory = streaming_parser_factory
        self.retryable_exceptions = retryable_exceptions
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.tool_materializer = tool_materializer
        self.progress_callback = progress_callback
        self.interrupted = interrupted or (lambda: False)
        self.hooks = hooks or LoopKernelHooks()

    def run_turn(self, request: LoopTurnRequest) -> LoopTurnOutcome:
        if request.compress_context:
            should_compress, reason = self.context.should_compress()
            if should_compress:
                logger.info("Compression triggered (pre-call): %s", reason)
                self.context.compress(self.llm_call)

        context_t0 = time.monotonic()
        messages = self.context.build_messages(
            request.system_prompt,
            user_instructions=request.user_instructions,
            exclude_workflow_context=request.exclude_workflow_context,
        )
        context_build_ms = (time.monotonic() - context_t0) * 1000

        materialization = (
            self.tool_materializer.materialize(messages, force_all=request.force_all_tools)
            if self.tool_materializer is not None
            else None
        )
        active_tool_schemas = (
            materialization.schemas
            if materialization is not None
            else self.tool_schemas
        )
        active_tool_prompt = format_active_tool_prompt(materialization)
        if active_tool_prompt:
            messages = [
                messages[0],
                {"role": "system", "content": active_tool_prompt},
                *messages[1:],
            ]

        telemetry = LoopTurnTelemetry(
            iteration=request.iteration,
            code_steps=request.code_steps,
            tool_steps=request.tool_steps,
            message_count=len(messages),
            context_tokens=self.context.estimate_request_tokens(messages, active_tool_schemas),
            tool_schema_count=len(active_tool_schemas or []),
            tool_schema_total=materialization.total_count if materialization else len(active_tool_schemas or []),
            tool_schema_reason=materialization.reason if materialization else "static",
            context_build_ms=context_build_ms,
        )

        if self.progress_callback:
            try:
                self.progress_callback(request.progress_stage, request.progress_detail)
            except Exception:
                logger.exception("progress_callback failed before provider turn")

        provider_result, updated_round = ProviderTurnCaller(
            llm_call=self.llm_call,
            tool_schemas=active_tool_schemas,
            streaming_parser_factory=self.streaming_parser_factory,
            retryable_exceptions=self.retryable_exceptions,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            progress_callback=self.progress_callback,
            interrupted=self.interrupted,
            logger_prefix=request.logger_prefix,
        ).call(
            messages,
            callbacks=ProviderTurnCallbacks(
                on_thought_delta=self.hooks.on_thought_delta,
                on_code_start=self.hooks.on_code_start,
                on_code_delta=self.hooks.on_code_delta,
                on_code_end=self.hooks.on_code_end,
                on_reasoning_start=self.hooks.on_reasoning_start,
                on_reasoning_end=self.hooks.on_reasoning_end,
            ),
            code_step_for_tool=request.code_step_for_tool,
            text_code_step=request.text_code_step,
            reasoning_round_seq=request.reasoning_round_seq,
            enable_reasoning_lifecycle=request.enable_reasoning_lifecycle,
            retry_detail=request.retry_detail,
        )

        telemetry.llm_ms = provider_result.duration_ms
        telemetry.response_chars = len(provider_result.response_text)
        telemetry.tool_call_count = len(provider_result.tool_calls) if provider_result.tool_calls else 0
        provider_result.tool_schema_count = len(active_tool_schemas or [])
        provider_result.tool_schema_total = telemetry.tool_schema_total
        provider_result.tool_schema_reason = telemetry.tool_schema_reason

        settlements: list[ToolSettlement] = []
        if provider_result.tool_calls:
            self.context.add_assistant_with_tool_calls(
                provider_result.response_text,
                provider_result.tool_calls,
            )
            if request.scope:
                self.context.mark_recent_scope(
                    request.scope,
                    count=1,
                    kind=request.assistant_tool_scope_kind,
                )

            tool_t0 = time.monotonic()
            progress_callback = self.progress_callback
            if request.tool_progress_label and self.progress_callback:
                def _scoped_progress(stage: str, detail: str = "") -> None:
                    if stage == "tool_call" and detail.startswith("Calling ") and detail.endswith("..."):
                        tool_name = detail[len("Calling "):-3]
                        detail = f"{request.tool_progress_label} — calling {tool_name}..."
                    try:
                        self.progress_callback(stage, detail)  # type: ignore[misc]
                    except Exception:
                        logger.exception("progress_callback failed")
                progress_callback = _scoped_progress

            settlements = ToolCallSettler(
                context=self.context,
                tool_runtime=self.tool_runtime,
                progress_callback=progress_callback,
                on_tool_start=self.hooks.on_tool_start,
                on_tool_result=self.hooks.on_tool_result,
            ).settle_all(
                provider_result.tool_calls,
                streamed_tool_code=provider_result.streamed_tool_code,
            )
            telemetry.tool_ms = (time.monotonic() - tool_t0) * 1000
            if request.scope and settlements:
                self.context.mark_recent_scope(
                    request.scope,
                    count=len(settlements),
                    kind=request.tool_result_scope_kind,
                )

        return LoopTurnOutcome(
            provider_result=provider_result,
            updated_reasoning_round=updated_round,
            settlements=settlements,
            telemetry=telemetry,
            materialization=materialization,
        )


__all__ = [
    "LoopKernel",
    "LoopKernelHooks",
    "LoopTurnOutcome",
    "LoopTurnRequest",
]
