"""Agent Loop — function-call-first architecture.

The LLM can respond in two ways at each step:
1. **Tool calls** (primary): structured function calls executed directly.
2. **Text replies**: final answer or a short nudge target after tool work.

Termination:
- Tool calls → loop continues until LLM stops calling tools (finish_reason="stop")
- Text reply → treated as final answer
- OpenCode-style: function-call tools with bounded tool output and context compression
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from opengis_backend.agent.context.context_manager import ContextManager
from opengis_backend.agent.context.pending_intent import PendingIntentResolver
from opengis_backend.agent.loop.loop_kernel import LoopKernel, LoopKernelHooks, LoopTurnRequest
from opengis_backend.agent.loop.retry_policy import (
    LLM_BASE_DELAY,
    LLM_MAX_RETRIES,
    LLM_RETRYABLE_EXCEPTIONS,
)
from opengis_backend.agent.execution.tool_materializer import ToolMaterializer, is_tool_visibility_miss
from opengis_backend.agent.execution.tool_runtime import ToolRuntime
from opengis_backend.agent.loop.types import AgentStep, CodeExecResult
from opengis_backend.agent.loop.policy import LoopPolicy, final_turn_instruction
from opengis_backend.agent.loop.runtime_control import RuntimeControl
from opengis_backend.agent.governance.profile import AgentProfile
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# The Agent Loop
# ─────────────────────────────────────────────────────────────────────

@dataclass
class AgentLoop:
    """Custom agent loop implementing function-call tool use.

    The LLM decides at each step whether to call a structured tool or reply
    with text. Python is executed only through the ``execute_code`` tool;
    Markdown code fences in assistant text are never executed.

    Termination is event-settled:
    - Tool calls are settled locally, then the provider gets another turn.
    - A provider turn without tool calls is the final user-visible text.
    - No heuristic text nudge is used in the free-form chat loop.

    Parameters
    ----------
    llm_call:
        Callable that takes provider messages and returns ``LLMResponse``.
        Provider routing is handled upstream.
    executor_call:
        Callable that takes a code string and returns a CodeExecResult.
        Wraps the SubprocessPythonExecutor.
    system_prompt:
        The full system prompt (with tool signatures baked in).
    step_callback:
        Optional callback invoked after each step with an AgentStep.
    context:
        Optional pre-existing ContextManager (for multi-turn conversations).
    """

    llm_call: Callable[..., Any]
    executor_call: Callable[[str], CodeExecResult]
    system_prompt: str
    step_callback: Optional[Callable[[AgentStep], None]] = None
    progress_callback: Optional[Callable[[str, str], None]] = None
    # Tool calling support
    tool_runtime: Optional[ToolRuntime] = None
    tool_schemas: Optional[list[dict]] = None
    tool_materializer: Optional[ToolMaterializer] = None
    on_tool_start: Optional[Callable[[str, dict, str], None]] = None
    on_tool_result: Optional[Callable[..., None]] = None
    # Commit hooks. Provider text deltas are draft fragments until the loop
    # accepts them as final text; execute_code tool input remains streamable.
    on_thought_delta: Optional[Callable[[str], None]] = None
    on_code_start: Optional[Callable[[int], None]] = None  # arg: step number (1-indexed code step)
    on_code_delta: Optional[Callable[[int, str], None]] = None
    on_code_end: Optional[Callable[[int], None]] = None
    on_provider_result: Optional[Callable[..., None]] = None
    context: ContextManager = field(default_factory=ContextManager)
    user_instructions: Optional[str] = None
    agent_profile: Optional[AgentProfile] = None
    exclude_workflow_context: bool = True
    # Set by external code (e.g. cancel handler) to signal the loop to
    # stop at the next safe point. Checked at the top of each iteration.
    _interrupted: bool = field(default=False, init=False, repr=False)

    def interrupt(self) -> None:
        """Signal the loop to stop at the next safe point."""
        logger.debug("[AGENT] interrupt() called from thread=%d, setting _interrupted=True", threading.get_ident())
        self._interrupted = True

    # -- Public API --------------------------------------------------

    def run(self, user_message: str) -> str:
        """Run the agent loop synchronously. Returns the final answer.

        This method blocks until the LLM produces a final answer, is
        cancelled, or encounters an unrecoverable error.

        Called from a worker thread (via asyncio.to_thread) so it's safe
        to block on LLM calls and subprocess execution.
        """
        workspace_path = getattr(self.tool_runtime, "workspace_path", None)
        pending_intent = PendingIntentResolver(workspace_path).resolve(
            self.context.messages,
            user_message,
        )
        user_meta = None
        if pending_intent is not None:
            user_meta = {
                "kind": "resolved_followup",
                "pending_intent_kind": pending_intent.kind,
                "resolved_objective": pending_intent.resolved_objective,
            }
        self.context.add_user_message(user_message, meta=user_meta)
        if self.tool_materializer is None and self.tool_schemas:
            self.tool_materializer = ToolMaterializer(self.tool_schemas)

        code_steps = 0  # Only count code execution steps toward the limit.
        tool_steps = 0  # Count non-code tool settlements for telemetry/guardrails.
        force_all_tools_once = False
        force_final_reason: str | None = None
        profile = self.agent_profile or AgentProfile.gis_build()
        policy = LoopPolicy.from_profile(profile)
        runtime_control = RuntimeControl.from_user_message(
            user_message,
            workspace_path=workspace_path,
            pending_intent=pending_intent,
        )
        kernel = LoopKernel(
            llm_call=self.llm_call,
            context=self.context,
            tool_runtime=self.tool_runtime,
            tool_schemas=self.tool_schemas,
            tool_materializer=self.tool_materializer,
            retryable_exceptions=LLM_RETRYABLE_EXCEPTIONS,
            max_retries=LLM_MAX_RETRIES,
            base_delay=LLM_BASE_DELAY,
            progress_callback=self.progress_callback,
            interrupted=lambda: self._interrupted,
            hooks=LoopKernelHooks(
                on_thought_delta=self.on_thought_delta,
                on_code_start=self.on_code_start,
                on_code_delta=self.on_code_delta,
                on_code_end=self.on_code_end,
                on_tool_start=self.on_tool_start,
                on_tool_result=self.on_tool_result,
                on_provider_result=self.on_provider_result,
            ),
        )

        iteration = 0
        while True:
            current_iteration = iteration
            iteration += 1
            # Check for external interruption.
            logger.debug("[AGENT] iteration=%d, code_steps=%d, tool_steps=%d, _interrupted=%s, thread=%d",
                        current_iteration, code_steps, tool_steps, self._interrupted, threading.get_ident())
            if self._interrupted:
                logger.debug("[AGENT] EXITING due to _interrupted=True at iteration top")
                logger.info(
                    "Agent loop interrupted externally after %d code steps and %d tool steps.",
                    code_steps,
                    tool_steps,
                )
                return "(Task interrupted by user.)"
            tentative_step = code_steps + 1

            logger.debug("[AGENT] LLM call START, _interrupted=%s", self._interrupted)
            policy_decision = policy.before_provider_turn(
                iteration=current_iteration,
                code_steps=code_steps,
                tool_steps=tool_steps,
                force_final_reason=force_final_reason,
            )
            stage = "finalizing" if policy_decision.force_final else ("calling_llm" if code_steps == 0 else "thinking_next_step")
            detail = "Finalizing..." if policy_decision.force_final else ("Thinking..." if code_steps == 0 else f"Thinking through step {code_steps + 1}...")
            extra_system_messages = (
                [final_turn_instruction(policy_decision.reason)]
                if policy_decision.force_final
                else None
            )
            system_inserts = list(extra_system_messages or [])
            system_inserts.append(runtime_control.system_prompt())
            outcome = kernel.run_turn(
                LoopTurnRequest(
                    iteration=current_iteration,
                    code_steps=code_steps,
                    tool_steps=tool_steps,
                    system_prompt=self.system_prompt,
                    user_instructions=self.user_instructions,
                    exclude_workflow_context=self.exclude_workflow_context,
                    progress_stage=stage,
                    progress_detail=detail,
                    text_code_step=tentative_step,
                    code_step_for_tool=lambda tool_index: tentative_step + tool_index,
                    retry_detail="Connection error",
                    logger_prefix="LOOP",
                    force_all_tools=force_all_tools_once,
                    disable_tools=policy_decision.force_final,
                    disabled_tools_reason=policy_decision.reason,
                    materialization_options=policy.materialization_options(),
                    extra_system_messages=system_inserts,
                    tool_call_guard=runtime_control.guard_tool_calls,
                )
            )
            force_all_tools_once = False
            provider_result = outcome.provider_result
            telemetry = outcome.telemetry
            duration_ms = provider_result.duration_ms
            response_text = provider_result.response_text
            tool_calls = provider_result.tool_calls

            logger.debug("[AGENT] LLM call END, duration=%.0fms, _interrupted=%s, content_len=%d, tool_calls=%d",
                        duration_ms, self._interrupted, len(response_text), len(tool_calls) if tool_calls else 0)

            # ── Handle tool_calls (primary path) ──
            if tool_calls:
                code_step_delta = sum(1 for item in outcome.settlements if item.counts_as_code_step)
                tool_step_delta = sum(1 for item in outcome.settlements if not item.counts_as_code_step)
                code_steps += code_step_delta
                tool_steps += tool_step_delta
                telemetry.code_steps = code_steps
                telemetry.tool_steps = tool_steps
                telemetry.continuation = "tool_results"
                settle_decision = policy.after_settlements(
                    outcome.settlements,
                    code_steps=code_steps,
                    tool_steps=tool_steps,
                )
                control_decision = runtime_control.observe_settlements(outcome.settlements)
                if control_decision.has_correction:
                    self.context.add_system_message(
                        control_decision.corrective_message,
                        meta={"kind": "runner_control", "mode": runtime_control.objective.mode.value},
                    )
                    telemetry.continuation = "runner_control_correction"
                    telemetry.log()
                    if control_decision.force_final_reason:
                        force_final_reason = control_decision.force_final_reason
                    continue
                if control_decision.force_final_reason:
                    force_final_reason = control_decision.force_final_reason
                    telemetry.continuation = "runner_control_force_final"
                    telemetry.log()
                    continue
                telemetry.log()
                if settle_decision.force_final:
                    force_final_reason = settle_decision.reason
                # Keep nudge state scoped to the whole user turn. Resetting it
                # after every tool call lets a short completed task be pushed
                # into unrelated follow-up work repeatedly.
                continue  # Let LLM see the tool results

            # ── No tool_calls: handle as text response ──
            #
            # Function-call architecture rule: plain text is never executed.
            # Custom Python must arrive through the execute_code tool. This
            # removes the unsafe path where a Markdown code fence in
            # the assistant reply could accidentally run as Python.
            response = response_text
            thought = response

            logger.info("LLM text response: %d chars", len(response) if response else 0)
            if response:
                logger.debug("[AGENT] LLM response preview: %.300s", response)

            if (
                outcome.materialization is not None
                and outcome.materialization.reason != "all"
                and is_tool_visibility_miss(response)
            ):
                logger.info(
                    "Text reply appears to be tool visibility confusion; retrying once with all tools."
                )
                self.context.add_assistant_message(response)
                self.context.add_user_message(
                    "[System] The previous reply may have confused dynamic tool visibility "
                    "with platform capability. Retry once with the full function tool set. "
                    "If the needed tool exists, call it. If it truly does not exist, answer "
                    "with the exact missing capability."
                )
                telemetry.continuation = "tool_visibility_retry"
                telemetry.log()
                force_all_tools_once = True
                continue

            total_work_steps = code_steps + tool_steps
            logger.info(
                "Text response accepted after %d tool/code steps.",
                total_work_steps,
            )
            self.context.add_assistant_message(response)
            if response and self.on_thought_delta:
                try:
                    self.on_thought_delta(response)
                except Exception:
                    logger.exception("on_thought_delta failed for final text response")
            step = AgentStep(
                step_num=code_steps + 1,
                thought=thought,
                is_text_reply=True,
                text_reply=response,
                duration_ms=duration_ms,
            )
            if self.step_callback:
                try:
                    self.step_callback(step)
                except Exception:
                    logger.exception("step_callback failed")

            logger.info(
                "Text reply after %d code steps and %d tool steps -- treating as task complete.",
                code_steps,
                tool_steps,
            )
            telemetry.code_steps = code_steps
            telemetry.tool_steps = tool_steps
            telemetry.continuation = "complete"
            telemetry.log()
            return response


__all__ = [
    "AgentLoop",
    "AgentStep",
    "CodeExecResult",
]
