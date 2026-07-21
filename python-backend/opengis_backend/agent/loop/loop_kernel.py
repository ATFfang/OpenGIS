"""Shared execution kernel for function-call agent loops.

The kernel owns mechanics that should not be duplicated by loop variants:
context compression, message projection, tool materialization, one provider
turn, tool settlement, and telemetry. AgentLoop and WorkflowLoop keep policy:
when to nudge, when to accept text, and how to summarize.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.context.provider_request import ProviderRequest, PromptSection
from opengis_backend.agent.context.provider_request_adapter import ProviderRequestAdapter
from opengis_backend.agent.context.request_budget import RequestBudgetManager, RequestBudgetReport
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
    on_tool_start: Callable[[str, dict, str], None] | None = None
    on_tool_result: Callable[..., Any] | None = None
    on_provider_result: Callable[..., Any] | None = None


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
    progress_detail: str = "Thinking..."
    retry_detail: str = "Connection error"
    logger_prefix: str = "LOOP"
    scope: str | None = None
    assistant_tool_scope_kind: str = "tool_calls"
    tool_result_scope_kind: str = "tool_result"
    compress_context: bool = True
    tool_progress_label: str | None = None
    force_all_tools: bool = False
    disable_tools: bool = False
    disabled_tools_reason: str = ""
    materialization_options: dict[str, Any] | None = None
    extra_system_messages: list[str] | None = None
    tool_call_guard: Callable[[list[dict[str, Any]]], Any] | None = None


@dataclass
class LoopTurnOutcome:
    provider_result: ProviderTurnResult
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

        budget_manager = RequestBudgetManager(
            input_token_budget=getattr(self.context, "token_budget", 100_000),
        )
        budget_limits = budget_manager.suggest_limits(
            live_tokens=self.context.estimate_live_tokens(),
        )
        context_t0 = time.monotonic()
        messages = self.context.build_messages(
            request.system_prompt,
            user_instructions=request.user_instructions,
            exclude_workflow_context=request.exclude_workflow_context,
            projection_limits=budget_limits,
        )
        context_build_ms = (time.monotonic() - context_t0) * 1000

        materialization_options = dict(request.materialization_options or {})
        if request.disable_tools:
            materialization = ToolMaterialization(
                schemas=[],
                selected_names=[],
                total_count=len(self.tool_schemas),
                reason="disabled:" + (request.disabled_tools_reason or "loop_policy"),
            )
        else:
            materialization = (
                self.tool_materializer.materialize(
                    force_all=request.force_all_tools,
                    **materialization_options,
                )
                if self.tool_materializer is not None
                else None
            )
        active_tool_schemas = (
            materialization.schemas
            if materialization is not None
            else self.tool_schemas
        )
        active_tool_prompt = format_active_tool_prompt(materialization)
        system_inserts = list(request.extra_system_messages or [])
        if active_tool_prompt:
            system_inserts.append(active_tool_prompt)
        if system_inserts:
            messages = [
                messages[0],
                *({"role": "system", "content": content} for content in system_inserts if content),
                *messages[1:],
            ]
        messages = _ensure_provider_tool_protocol(messages)

        budget_report = self._analyze_request_budget(messages, active_tool_schemas)
        if (
            request.compress_context
            and budget_report.pressure in {"hot", "overflow"}
            and self.context.prune_tool_results() > 0
        ):
            budget_limits = budget_manager.suggest_limits(
                pressure=budget_report.pressure,
                live_tokens=self.context.estimate_live_tokens(),
            )
            logger.info(
                "Request budget pressure=%s total=%d; pruned tool results and rebuilding provider messages.",
                budget_report.pressure,
                budget_report.total_tokens,
            )
            messages = self.context.build_messages(
                request.system_prompt,
                user_instructions=request.user_instructions,
                exclude_workflow_context=request.exclude_workflow_context,
                projection_limits=budget_limits,
            )
            if system_inserts:
                messages = [
                    messages[0],
                    *({"role": "system", "content": content} for content in system_inserts if content),
                    *messages[1:],
                ]
            messages = _ensure_provider_tool_protocol(messages)
            budget_report = self._analyze_request_budget(messages, active_tool_schemas)

        provider_request = _provider_request_from_messages(messages)
        prompt_cache_plan = ProviderRequestAdapter(provider_request).prompt_cache_plan(
            model=str(getattr(self.llm_call, "opengis_model", "") or ""),
            provider=str(getattr(self.llm_call, "opengis_provider", "") or ""),
            tools=active_tool_schemas,
        )

        telemetry = LoopTurnTelemetry(
            iteration=request.iteration,
            code_steps=request.code_steps,
            tool_steps=request.tool_steps,
            message_count=len(messages),
            context_tokens=budget_report.total_tokens,
            tool_schema_count=len(active_tool_schemas or []),
            tool_schema_total=materialization.total_count if materialization else len(active_tool_schemas or []),
            tool_schema_reason=materialization.reason if materialization else "static",
            request_pressure=budget_report.pressure,
            context_build_ms=context_build_ms,
        )

        if self.progress_callback:
            try:
                self.progress_callback(request.progress_stage, request.progress_detail)
            except Exception:
                logger.exception("progress_callback failed before provider turn")

        provider_result = ProviderTurnCaller(
            llm_call=self.llm_call,
            tool_schemas=active_tool_schemas,
            retryable_exceptions=self.retryable_exceptions,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            progress_callback=self.progress_callback,
            interrupted=self.interrupted,
            logger_prefix=request.logger_prefix,
        ).call(
            messages,
            callbacks=ProviderTurnCallbacks(
                on_code_start=self.hooks.on_code_start,
                on_code_delta=self.hooks.on_code_delta,
                on_code_end=self.hooks.on_code_end,
            ),
            code_step_for_tool=request.code_step_for_tool,
            retry_detail=request.retry_detail,
            prompt_cache_key=prompt_cache_plan.cache_key,
            prompt_cache_metadata=prompt_cache_plan.to_dict(),
        )

        telemetry.llm_ms = provider_result.duration_ms
        telemetry.response_chars = len(provider_result.response_text)
        telemetry.tool_call_count = len(provider_result.tool_calls) if provider_result.tool_calls else 0
        usage = getattr(provider_result.response, "usage", None)
        if isinstance(usage, dict):
            telemetry.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            telemetry.cached_tokens = int(usage.get("cached_tokens") or 0)
            telemetry.completion_tokens = int(usage.get("completion_tokens") or 0)
            telemetry.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
            telemetry.cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        if self.hooks.on_provider_result:
            try:
                self.hooks.on_provider_result(
                    usage=getattr(provider_result.response, "usage", None) or {},
                    prompt_cache=getattr(provider_result.response, "prompt_cache", None) or prompt_cache_plan.to_dict(),
                    telemetry=asdict(telemetry),
                    request={
                        "message_count": len(messages),
                        "section_count": len(provider_request.sections),
                        "sections": provider_request.section_debug(),
                        "cacheable_prefix_hash": provider_request.cacheable_prefix_hash,
                        "tool_schema_count": len(active_tool_schemas or []),
                        "tool_schema_total": telemetry.tool_schema_total,
                        "tool_schema_reason": telemetry.tool_schema_reason,
                        "request_pressure": budget_report.pressure,
                    },
                )
            except Exception:
                logger.debug("on_provider_result hook failed", exc_info=True)
        provider_result.tool_schema_count = len(active_tool_schemas or [])
        provider_result.tool_schema_total = telemetry.tool_schema_total
        provider_result.tool_schema_reason = telemetry.tool_schema_reason
        if request.disable_tools and provider_result.tool_calls:
            logger.warning(
                "Provider returned %d tool call(s) during disabled-tools final turn; discarding.",
                len(provider_result.tool_calls),
            )
            provider_result.tool_calls = None
            if not provider_result.response_text:
                provider_result.response_text = (
                    "本轮执行达到预算上限，模型仍请求继续调用工具，Runner 已停止新的工具执行。"
                    "请提高 Agent 执行预算或发送“继续”让 Agent 基于当前结果接着完成。"
                )
            telemetry.tool_call_count = 0

        settlements: list[ToolSettlement] = []
        if provider_result.tool_calls:
            blocked_call_ids: dict[str, str] = {}
            if request.tool_call_guard is not None:
                try:
                    guard_result = request.tool_call_guard(provider_result.tool_calls)
                    raw_blocked = getattr(guard_result, "blocked_call_ids", None)
                    if isinstance(raw_blocked, dict):
                        blocked_call_ids = {
                            str(call_id): str(reason)
                            for call_id, reason in raw_blocked.items()
                            if str(call_id)
                        }
                except Exception:
                    logger.exception("tool_call_guard failed; continuing without guard blocks")

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
                    if stage in {"tool_intent", "executing_code", "loading_geodata", "loading_raster", "loading_data", "saving_results", "generating_visualization"}:
                        detail = f"{request.tool_progress_label} — {detail}"
                    elif stage == "tool_call" and detail.startswith("Calling ") and detail.endswith("..."):
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
                settlement_identity={
                    "provider_turn_id": f"{request.logger_prefix.lower()}:{request.iteration}",
                    "assistant_message_id": f"{request.logger_prefix.lower()}:{request.iteration}:assistant",
                },
            ).settle_all(
                provider_result.tool_calls,
                streamed_tool_code=provider_result.streamed_tool_code,
                blocked_call_ids=blocked_call_ids,
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
            settlements=settlements,
            telemetry=telemetry,
            materialization=materialization,
        )

    def _analyze_request_budget(
        self,
        messages: list[dict[str, Any]],
        active_tool_schemas: list[dict[str, Any]] | None,
    ) -> RequestBudgetReport:
        return RequestBudgetManager(
            input_token_budget=getattr(self.context, "token_budget", 100_000),
        ).analyze(messages=messages, tools=active_tool_schemas or [])


def _provider_request_from_messages(messages: list[dict[str, Any]]) -> ProviderRequest:
    """Wrap the already-built provider messages in cache diagnostic sections.

    This is intentionally observational: it must not reorder or rewrite the
    messages sent to the model on this branch.
    """
    if not messages:
        return ProviderRequest(messages=[], sections=[])
    sections: list[PromptSection] = [
        PromptSection(
            id="system.base",
            kind="system",
            messages=[dict(messages[0])],
            stability="static",
            cache_policy="cacheable",
        )
    ]
    if len(messages) > 1:
        sections.append(
            PromptSection(
                id="context.cacheable_prefix",
                kind="history",
                messages=[dict(message) for message in messages[1:]],
                stability="turn_dynamic",
                cache_policy="breakpoint",
                metadata={"observational": True},
            )
        )
    return ProviderRequest(messages=messages, sections=sections)


def _ensure_provider_tool_protocol(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize final provider request messages for OpenAI tool-call rules."""
    out: list[dict[str, Any]] = []
    pending_buffer: list[dict[str, Any]] = []
    pending_ids: set[str] = set()

    def summarize_pending() -> None:
        nonlocal pending_buffer, pending_ids
        if not pending_buffer:
            return
        assistant = pending_buffer[0]
        names: list[str] = []
        tool_calls = assistant.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                fn = tool_call.get("function") if isinstance(tool_call, dict) else None
                if isinstance(fn, dict):
                    names.append(str(fn.get("name") or "unknown"))
        missing = f" missing_ids={','.join(sorted(pending_ids))}" if pending_ids else ""
        out.append({
            "role": "system",
            "content": (
                "[Historical tool-call transaction summarized for provider protocol safety.] "
                f"tool_calls={', '.join(names) or 'unknown'}{missing}"
            ),
        })
        pending_buffer = []
        pending_ids = set()

    for message in messages:
        role = message.get("role")
        if pending_buffer:
            tool_call_id = str(message.get("tool_call_id") or "")
            if role == "tool" and tool_call_id in pending_ids:
                pending_buffer.append(message)
                pending_ids.discard(tool_call_id)
                if not pending_ids:
                    out.extend(pending_buffer)
                    pending_buffer = []
                continue
            summarize_pending()

        tool_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            ids = {
                str(tool_call.get("id") or "")
                for tool_call in tool_calls
                if isinstance(tool_call, dict) and str(tool_call.get("id") or "")
            }
            if ids:
                pending_buffer = [message]
                pending_ids = ids
            else:
                out.append({
                    "role": "system",
                    "content": "[Historical assistant tool_calls omitted because call ids were missing.]",
                })
            continue

        if role == "tool":
            out.append({
                "role": "system",
                "content": (
                    "[Historical orphan tool result summarized for provider protocol safety.] "
                    f"tool={message.get('name') or 'tool'} "
                    f"tool_call_id={message.get('tool_call_id') or '?'}"
                ),
            })
            continue

        out.append(message)

    summarize_pending()
    return out


__all__ = [
    "LoopKernel",
    "LoopKernelHooks",
    "LoopTurnOutcome",
    "LoopTurnRequest",
]
